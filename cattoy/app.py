"""本体の実行ループ。

  映像スレッド:   カメラ → YOLO 検出 → 追跡器の更新（数〜十数 fps）
  制御ループ:     追跡器から猫の位置を外挿 → 遊び方の決定 → サーボ・レーザー（50 Hz）

検出は遅いので、制御を別ループにして外挿することでレーザーの動きを滑らかにしている。

負荷を下げるため、映像スレッドは状況に応じて 3 段階で動く。
  off      OFF・動作時間外・休憩中          映像も認識も止める（プレビューを見ている間だけ映像を取る）
  standby  ON で猫がいない                  standby_interval_s ごとに 1 枚だけ認識する
  active   猫がいる・遊んでいる              連続で認識する
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .behavior import Command, Mode, PlayBehavior
from .calibration import Calibration
from .config import Config, is_active_time
from .geometry import Point
from .hardware import make_camera, make_laser, make_pantilt
from .tracker import CatTracker, Detection

log = logging.getLogger(__name__)

PERSON_HOLD_S = 1.5  # 人を最後に検出してからこの秒数は消灯を続ける


def load_calibration(cfg: Config, image_size: tuple[int, int]) -> Calibration:
    path = cfg.resolve(cfg.calibration.path)
    if path.exists():
        cal = Calibration.load(path)
        if tuple(cal.image_size) != tuple(image_size):
            raise SystemExit(
                f"キャリブレーション時の画像サイズ {cal.image_size} と現在の設定 {image_size} が違います。"
                " calibrate をやり直してください。"
            )
        return cal
    if cfg.servo.backend == "mock":
        log.warning("キャリブレーションファイルがないため、仮の対応付けで動かします（mock 用）")
        s = cfg.servo
        return Calibration.linear_fallback(image_size, (s.pan_min, s.pan_max), (s.tilt_min, s.tilt_max))
    raise SystemExit(f"{path} がありません。先に `cattoy calibrate` を実行してください。")


def model_path(cfg: Config) -> Path:
    """検出モデルの場所。設定の場所になければ、コンテナに同梱したモデル（CATTOY_BUNDLED_MODEL）を使う。"""
    path = cfg.resolve(cfg.detector.model_path)
    bundled = os.environ.get("CATTOY_BUNDLED_MODEL")
    if not path.exists() and bundled and Path(bundled).exists():
        log.info("同梱の検出モデルを使います: %s", bundled)
        return Path(bundled)
    return path


def try_load_calibration(cfg: Config, image_size: tuple[int, int]) -> Calibration | None:
    """キャリブレーションを読む。まだない（または画像サイズが違う）なら None。"""
    try:
        return load_calibration(cfg, image_size)
    except SystemExit as e:
        log.warning("%s 操作画面の「位置合わせ」から実行できます。", e)
        return None


def play_area(cfg: Config, cal: Calibration, image_size: tuple[int, int]) -> list[Point]:
    w, h = image_size
    if cfg.play.play_area:
        if len(cfg.play.play_area) < 3:
            raise SystemExit("play_area は 3 点以上で指定してください")
        return [(x * w, y * h) for x, y in cfg.play.play_area]
    return cal.default_play_area()


def finish_point(cfg: Config, image_size: tuple[int, int]) -> Point | None:
    if not cfg.play.finish_point:
        return None
    x, y = cfg.play.finish_point
    return (x * image_size[0], y * image_size[1])


class StateStore:
    """ON/OFF と、日ごとの遊んだ時間を保存する（再起動しても引き継ぐ）。"""

    SAVE_INTERVAL_S = 30.0

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.enabled = True
        self.play_log: dict[str, float] = {}
        self._dirty = False
        self._saved_at = time.monotonic()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.enabled = bool(data.get("enabled", True))
            self.play_log = {k: float(v) for k, v in data.get("play_log", {}).items()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("%s を読めません（初期状態で始めます）: %s", path, e)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if on != self.enabled:
                log.info("%s にしました", "ON" if on else "OFF")
            self.enabled = on
            self._dirty = True
        self.save()

    def toggle(self) -> None:
        self.set_enabled(not self.enabled)

    def add_play(self, seconds: float, today: str | None = None) -> None:
        today = today or dt.date.today().isoformat()
        with self._lock:
            self.play_log[today] = self.play_log.get(today, 0.0) + seconds
            self._dirty = True
            due = time.monotonic() - self._saved_at > self.SAVE_INTERVAL_S
        if due:
            self.save()

    def today_play_s(self) -> float:
        with self._lock:
            return self.play_log.get(dt.date.today().isoformat(), 0.0)

    def recent(self, days: int = 7) -> list[tuple[str, float]]:
        today = dt.date.today()
        with self._lock:
            return [
                (d, self.play_log.get(d, 0.0))
                for d in ((today - dt.timedelta(days=k)).isoformat() for k in range(days - 1, -1, -1))
            ]

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            cutoff = (dt.date.today() - dt.timedelta(days=90)).isoformat()
            self.play_log = {k: v for k, v in self.play_log.items() if k >= cutoff}
            data = {"enabled": self.enabled, "play_log": {k: round(v, 1) for k, v in self.play_log.items()}}
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("%s に保存できません: %s", self.path, e)


class _Shared:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cmd: Command | None = None
        self.person_seen_at = -1e9
        self.fps = 0.0
        self.vision = "off"
        self.esp32: dict | None = None
        self.error: BaseException | None = None


class PlaySetup:
    """キャリブレーションから決まる、遊びの一式（位置合わせをやり直すと作り直す）。"""

    def __init__(self, cfg: Config, cal: Calibration, size: tuple[int, int]):
        self.cal = cal
        self.size = size
        self.latency = cal.latency_s
        self.area = play_area(cfg, cal, size)
        self.goal = finish_point(cfg, size)
        self.tracker = CatTracker(lost_timeout_s=cfg.play.lost_timeout_s, max_extrapolate_s=0.5 + self.latency)
        self.behavior = PlayBehavior(cfg.play, self.area, size, finish_point=self.goal, latency_s=self.latency)


AIM_IDLE_S = 60.0  # 可動範囲の調整で、操作がこれだけないと終了する
AIM_LASER_S = 20.0  # 調整中のレーザーは、操作がこれだけないと消す


class Maintenance:
    """操作画面から行う調整（位置合わせ・可動範囲の確認）。行っている間は遊びを止め、機器を調整側に渡す。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode: str | None = None  # None / "calibrating" / "aim"
        self.message = ""
        self.result: dict | None = None
        self.last_aim = 0.0
        self.aim_laser = False

    def snapshot(self) -> dict:
        with self.lock:
            return {"mode": self.mode, "message": self.message, "result": self.result}

    def set_message(self, msg: str) -> None:
        with self.lock:
            self.message = msg


def state_label(enabled: bool, active: bool, mode: Mode, cooldown_left: float) -> str:
    if not enabled:
        return "停止中"
    if not active:
        return "動作時間外"
    if mode is Mode.COOLDOWN:
        return f"休憩中（あと {max(1, round(cooldown_left / 60))} 分）"
    if mode is Mode.PLAY:
        return "遊んでいます"
    if mode is Mode.FINISH:
        return "おしまいの合図中"
    return "猫を待っています"


def _json_body(body: bytes) -> dict:
    try:
        data = json.loads(body or b"{}")
    except ValueError as e:
        raise ValueError("JSON を送ってください") from e
    if not isinstance(data, dict):
        raise ValueError("JSON のオブジェクトを送ってください")
    return data


def run(cfg: Config, web_port: int | None = None, stop: threading.Event | None = None) -> None:
    from .calibrate import calibrate_with
    from .config import save_ui_settings
    from .detector import YoloOnnxDetector

    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())

    camera = make_camera(cfg.camera, cfg.esp32)
    laser = make_laser(cfg.laser, cfg.esp32)
    pantilt = make_pantilt(cfg.servo, cfg.esp32)
    store = StateStore(cfg.resolve(cfg.runtime.state_path))
    preview = None
    threads: list[threading.Thread] = []
    try:
        size = camera.size
        d = cfg.detector
        detector = YoloOnnxDetector(
            model_path(cfg), d.input_size, d.conf_threshold, d.iou_threshold, d.threads, d.device
        )
        shared = _Shared()
        maint = Maintenance()
        cal = try_load_calibration(cfg, size)
        play: PlaySetup | None = PlaySetup(cfg, cal, size) if cal is not None else None

        def active_now() -> bool:
            return is_active_time(cfg.play.active_hours, dt.datetime.now().time())

        def status() -> dict:
            now = time.monotonic()
            with shared.lock:
                cmd, fps, vision, esp, person_at = shared.cmd, shared.fps, shared.vision, shared.esp32, shared.person_seen_at
            p = play
            active = active_now()
            mode = p.behavior.mode if p else Mode.IDLE
            cooldown_left = max(0.0, p.behavior.cooldown_until - now) if p else 0.0
            m = maint.snapshot()
            if m["mode"] == "calibrating":
                label = "位置合わせ中"
            elif m["mode"] == "aim":
                label = "可動範囲の調整中"
            elif p is None:
                label = "位置合わせが必要です"
            else:
                label = state_label(store.enabled, active, mode, cooldown_left)
            s = cfg.servo
            return {
                "enabled": store.enabled,
                "label": label,
                "mode": mode.value,
                "move": cmd.move.value if cmd and cmd.move else None,
                "reason": cmd.reason if cmd else "",
                "laser": bool(cmd and cmd.laser_on),
                "cat": bool(p and p.tracker.state_at(now) is not None),
                "person": now - person_at < PERSON_HOLD_S,
                "active_hours": cfg.play.active_hours,
                "in_active_hours": active,
                "vision": vision,
                "fps": round(fps, 1),
                "detector": detector.provider,
                "needs_calibration": p is None,
                "calibration": (
                    {"points": len(p.cal.points), "rms_px": round(p.cal.rms_px, 1), "latency_ms": round(p.latency * 1000)}
                    if p else None
                ),
                "latency_ms": round(p.latency * 1000) if p else 0,
                "session_played_s": round(p.behavior.played_s) if p else 0,
                "session_max_s": cfg.play.session_max_s,
                "cooldown_left_s": round(cooldown_left),
                "today_play_s": round(store.today_play_s()),
                "recent": store.recent(),
                "esp32": esp,
                "maintenance": m,
                "servo": {
                    "pan_min": s.pan_min, "pan_max": s.pan_max, "tilt_min": s.tilt_min, "tilt_max": s.tilt_max,
                    "pan_home": s.pan_home, "tilt_home": s.tilt_home, "range": s.actuation_range,
                    "pan": round(pantilt.pan, 1), "tilt": round(pantilt.tilt, 1),
                },
            }

        # ---------------------------------------------------------------- 操作画面からの調整

        def start_calibration(_: bytes) -> dict:
            with maint.lock:
                if maint.mode == "calibrating":
                    raise ValueError("位置合わせはすでに実行中です")
                maint.mode, maint.message, maint.result = "calibrating", "準備しています", None

            def worker() -> None:
                nonlocal play
                try:
                    new_cal = calibrate_with(
                        cfg, camera, laser, pantilt, progress=maint.set_message,
                        on_frame=preview.update if preview is not None else None,
                    )
                    new_play = PlaySetup(cfg, new_cal, size)
                    play = new_play
                    result = {
                        "ok": True, "points": len(new_cal.points), "rms_px": round(new_cal.rms_px, 1),
                        "latency_ms": round(new_cal.latency_s * 1000),
                    }
                    log.info("操作画面からの位置合わせが終わりました: %s", result)
                except Exception as e:  # 失敗しても遊びは前の状態のまま続ける
                    log.exception("位置合わせに失敗しました")
                    result = {"ok": False, "error": str(e)}
                finally:
                    laser.off()
                with maint.lock:
                    maint.mode, maint.message, maint.result = None, "", result

            threading.Thread(target=worker, name="calibrate", daemon=True).start()
            return {"ok": True}

        def aim(body: bytes) -> dict:
            data = _json_body(body)
            s = cfg.servo
            pan = float(data.get("pan", pantilt.pan))
            tilt = float(data.get("tilt", pantilt.tilt))
            want_laser = bool(data.get("laser", False))
            with maint.lock:
                if maint.mode == "calibrating":
                    raise ValueError("位置合わせ中です")
                maint.mode = "aim"
                maint.last_aim = time.monotonic()
                maint.aim_laser = want_laser
            # 調整中は保存済みの可動範囲を超えて動かせる（範囲を広げるため）。サーボの物理的な範囲には収める
            pan = min(max(pan, 0.0), s.actuation_range)
            tilt = min(max(tilt, 0.0), s.actuation_range)
            saved = (s.pan_min, s.pan_max, s.tilt_min, s.tilt_max)
            s.pan_min, s.pan_max, s.tilt_min, s.tilt_max = 0.0, s.actuation_range, 0.0, s.actuation_range
            try:
                pantilt.move(pan, tilt)
            finally:
                s.pan_min, s.pan_max, s.tilt_min, s.tilt_max = saved
            laser.set(want_laser)
            return {"ok": True, "pan": pantilt.pan, "tilt": pantilt.tilt}

        def stop_aim(_: bytes = b"") -> dict:
            with maint.lock:
                if maint.mode == "aim":
                    maint.mode = None
            laser.off()
            pantilt.release()
            return {"ok": True}

        def save_servo_limits(body: bytes) -> dict:
            data = _json_body(body)
            s = cfg.servo
            values = {k: float(data[k]) for k in ("pan_min", "pan_max", "tilt_min", "tilt_max") if k in data}
            merged = {k: values.get(k, getattr(s, k)) for k in ("pan_min", "pan_max", "tilt_min", "tilt_max")}
            for axis in ("pan", "tilt"):
                lo, hi = merged[f"{axis}_min"], merged[f"{axis}_max"]
                if not 0 <= lo < hi <= s.actuation_range:
                    raise ValueError(f"{axis} の範囲が不正です（0 ≦ 最小 ＜ 最大 ≦ {s.actuation_range:g}）")
            merged["pan_home"] = (merged["pan_min"] + merged["pan_max"]) / 2
            merged["tilt_home"] = (merged["tilt_min"] + merged["tilt_max"]) / 2
            save_ui_settings(cfg, "servo", merged)
            log.info("操作画面から可動範囲を保存しました: %s", merged)
            return {"ok": True, **merged}

        def maintenance_actions() -> dict:
            actions = {
                "/api/calibrate": start_calibration,
                "/api/aim": aim,
                "/api/aim/stop": stop_aim,
                "/api/servo-limits": save_servo_limits,
            }
            if getattr(pantilt, "link", None) is not None:
                from . import firmware

                def update(body: bytes) -> dict:
                    log.info("操作画面からファームウェアの書き換えを受け付けました（%d バイト）", len(body))
                    return {"ok": True, **firmware.upload_firmware(cfg.esp32, body)}

                def reboot(_: bytes) -> dict:
                    firmware.reboot(cfg.esp32)
                    return {"ok": True}

                actions.update({"/api/esp32/firmware": update, "/api/esp32/reboot": reboot})
            return actions

        port = web_port if web_port is not None else cfg.runtime.web_port
        if port:
            from .preview import PreviewServer

            preview = PreviewServer(
                port, status=status, set_enabled=store.set_enabled, actions=maintenance_actions(),
                files={"/calibration.jpg": lambda: cfg.resolve(cfg.calibration.path).with_suffix(".jpg")},
            )

        # ---------------------------------------------------------------- 映像

        def vision_mode(now: float, p: PlaySetup | None) -> str:
            if p is None or not store.enabled or not active_now() or p.behavior.mode is Mode.COOLDOWN:
                return "off"
            if p.behavior.mode in (Mode.PLAY, Mode.FINISH) or p.tracker.state_at(now) is not None:
                return "active"
            return "standby"

        def vision_loop() -> None:
            from .preview import draw_overlay

            last = time.monotonic()
            try:
                while not stop.is_set():
                    if maint.mode == "calibrating":  # カメラは位置合わせ側が使う
                        stop.wait(0.2)
                        continue
                    started = time.monotonic()
                    p = play
                    mode = vision_mode(started, p)
                    viewing = preview is not None and preview.has_viewers()
                    with shared.lock:
                        shared.vision = mode
                    if mode == "off" and not viewing:
                        camera.set_streaming(False)
                        stop.wait(0.5)
                        continue
                    camera.set_streaming(mode == "active" or viewing)
                    frame, arrived = camera.read_with_time()
                    if maint.mode == "calibrating":
                        continue
                    dets: list[Detection] = detector.detect(frame) if mode != "off" else []
                    now = time.monotonic()
                    if p is not None:
                        t = arrived - p.latency  # 実際に撮影されたおおよその時刻
                        p.tracker.update(t, [x for x in dets if x.label == "cat"])
                    with shared.lock:
                        if any(x.label == "person" for x in dets):
                            shared.person_seen_at = arrived
                        shared.fps = 0.8 * shared.fps + 0.2 / max(now - last, 1e-3)
                        cmd, fps = shared.cmd, shared.fps
                    last = now
                    if viewing:
                        img = draw_overlay(
                            frame, p.area if p else None, dets, p.tracker.state_at(now) if p else None, cmd,
                            p.goal if p else None, cfg.play.keepout_margin,
                            f"{mode} {fps:.1f}fps {detector.provider}" + (f" played {p.behavior.played_s:.0f}s" if p else ""),
                        )
                        preview.update(img)
                    if mode == "standby":
                        stop.wait(max(0.0, cfg.runtime.standby_interval_s - (time.monotonic() - started)))
            except BaseException as e:  # 制御ループ側で検知して安全に止める
                shared.error = e
                stop.set()

        threads.append(threading.Thread(target=vision_loop, name="vision", daemon=True))

        link = getattr(pantilt, "link", None)
        if link is not None:

            def esp32_poll_loop() -> None:
                """ESP32 の状態を 1 秒ごとに取得する。本体のボタンが押されたら ON/OFF を切り替える。"""
                last_button: int | None = None
                while not stop.wait(1.0):
                    st = link.status()
                    with shared.lock:
                        shared.esp32 = {"online": st is not None, **(st or {})}
                    if st is None:
                        continue
                    button = int(st.get("button", 0))
                    if last_button is not None and button > last_button:
                        store.toggle()
                    last_button = button

            threads.append(threading.Thread(target=esp32_poll_loop, name="esp32-poll", daemon=True))

        for th in threads:
            th.start()

        log.info(
            "開始しました（画像 %dx%d, 認識 %s, %s, %s）",
            size[0], size[1], detector.provider,
            f"映像の遅れ {play.latency * 1000:.0f} ms" if play else "位置合わせ未実施",
            "ON" if store.enabled else "OFF",
        )

        # ---------------------------------------------------------------- 制御
        period = 1.0 / cfg.runtime.control_hz
        last_aim = time.monotonic()
        last_t = time.monotonic()
        next_tick = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            dt_s, last_t = now - last_t, now
            p = play
            with maint.lock:
                m_mode, idle = maint.mode, now - maint.last_aim
            if m_mode == "aim":
                # 可動範囲の調整中: サーボとレーザーは調整側が動かす。放っておかれたら安全側に戻す
                if idle > AIM_LASER_S:
                    laser.off()
                if idle > AIM_IDLE_S:
                    stop_aim()
                cmd = Command(laser.is_on, None, Mode.IDLE, None, "可動範囲の調整中")
            elif m_mode == "calibrating":
                cmd = Command(False, None, Mode.IDLE, None, "位置合わせ中")
            elif p is None:
                cmd = Command(False, None, Mode.IDLE, None, "位置合わせが必要です")
                laser.set(False)
            else:
                if store.enabled:
                    cat = p.tracker.state_at(now)
                    with shared.lock:
                        person = now - shared.person_seen_at < PERSON_HOLD_S
                    cmd = p.behavior.step(now, cat, person, active_now())
                    if cmd.mode is Mode.PLAY:
                        store.add_play(dt_s)
                else:
                    cmd = Command(False, None, p.behavior.mode, None, "OFF")
                if cmd.target is not None:
                    pantilt.move(*p.cal.pixel_to_angles(*cmd.target))
                    last_aim = now
                laser.set(cmd.laser_on)
            if m_mode is None and cmd.target is None and now - last_aim > cfg.servo.release_after_s:
                pantilt.release()
            with shared.lock:
                shared.cmd = cmd
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
        if shared.error is not None:
            raise shared.error
    finally:
        laser.close()  # 何があってもまずレーザーを消す
        stop.set()
        for th in threads:
            if th.is_alive():
                th.join(timeout=3.0)
        pantilt.close()
        camera.close()
        store.save()
        if preview is not None:
            preview.close()
        log.info("停止しました")
