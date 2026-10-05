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


def run(cfg: Config, web_port: int | None = None, stop: threading.Event | None = None) -> None:
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
        cal = load_calibration(cfg, size)
        area = play_area(cfg, cal, size)
        goal = finish_point(cfg, size)
        d = cfg.detector
        detector = YoloOnnxDetector(
            cfg.resolve(d.model_path), d.input_size, d.conf_threshold, d.iou_threshold, d.threads, d.device
        )
        latency = cal.latency_s
        tracker = CatTracker(lost_timeout_s=cfg.play.lost_timeout_s, max_extrapolate_s=0.5 + latency)
        behavior = PlayBehavior(cfg.play, area, size, finish_point=goal, latency_s=latency)
        shared = _Shared()

        def active_now() -> bool:
            return is_active_time(cfg.play.active_hours, dt.datetime.now().time())

        def status() -> dict:
            now = time.monotonic()
            with shared.lock:
                cmd, fps, vision, esp, person_at = shared.cmd, shared.fps, shared.vision, shared.esp32, shared.person_seen_at
            cooldown_left = max(0.0, behavior.cooldown_until - now)
            active = active_now()
            return {
                "enabled": store.enabled,
                "label": state_label(store.enabled, active, behavior.mode, cooldown_left),
                "mode": behavior.mode.value,
                "move": cmd.move.value if cmd and cmd.move else None,
                "reason": cmd.reason if cmd else "",
                "laser": bool(cmd and cmd.laser_on),
                "cat": tracker.state_at(now) is not None,
                "person": now - person_at < PERSON_HOLD_S,
                "active_hours": cfg.play.active_hours,
                "in_active_hours": active,
                "vision": vision,
                "fps": round(fps, 1),
                "detector": detector.provider,
                "latency_ms": round(latency * 1000),
                "session_played_s": round(behavior.played_s),
                "session_max_s": cfg.play.session_max_s,
                "cooldown_left_s": round(cooldown_left),
                "today_play_s": round(store.today_play_s()),
                "recent": store.recent(),
                "esp32": esp,
            }

        port = web_port if web_port is not None else cfg.runtime.web_port
        if port:
            from .preview import PreviewServer

            preview = PreviewServer(port, status=status, set_enabled=store.set_enabled)

        def vision_mode(now: float) -> str:
            if not store.enabled or not active_now() or behavior.mode is Mode.COOLDOWN:
                return "off"
            if behavior.mode in (Mode.PLAY, Mode.FINISH) or tracker.state_at(now) is not None:
                return "active"
            return "standby"

        def vision_loop() -> None:
            from .preview import draw_overlay

            last = time.monotonic()
            try:
                while not stop.is_set():
                    started = time.monotonic()
                    mode = vision_mode(started)
                    viewing = preview is not None and preview.has_viewers()
                    with shared.lock:
                        shared.vision = mode
                    if mode == "off" and not viewing:
                        camera.set_streaming(False)
                        stop.wait(0.5)
                        continue
                    camera.set_streaming(mode == "active" or viewing)
                    frame, arrived = camera.read_with_time()
                    t = arrived - latency  # 実際に撮影されたおおよその時刻
                    dets: list[Detection] = detector.detect(frame) if mode != "off" else []
                    tracker.update(t, [x for x in dets if x.label == "cat"])
                    now = time.monotonic()
                    with shared.lock:
                        if any(x.label == "person" for x in dets):
                            shared.person_seen_at = t
                        shared.fps = 0.8 * shared.fps + 0.2 / max(now - last, 1e-3)
                        cmd, fps = shared.cmd, shared.fps
                    last = now
                    if viewing:
                        img = draw_overlay(
                            frame, area, dets, tracker.state_at(now), cmd, goal, cfg.play.keepout_margin,
                            f"{mode} {fps:.1f}fps {detector.provider} played {behavior.played_s:.0f}s",
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
            "開始しました（画像 %dx%d, 認識 %s, 映像の遅れ %.0f ms, %s）",
            size[0], size[1], detector.provider, latency * 1000, "ON" if store.enabled else "OFF",
        )
        period = 1.0 / cfg.runtime.control_hz
        last_aim = time.monotonic()
        last_t = time.monotonic()
        next_tick = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            dt_s, last_t = now - last_t, now
            if store.enabled:
                cat = tracker.state_at(now)
                with shared.lock:
                    person = now - shared.person_seen_at < PERSON_HOLD_S
                cmd = behavior.step(now, cat, person, active_now())
                if cmd.mode is Mode.PLAY:
                    store.add_play(dt_s)
            else:
                cmd = Command(False, None, behavior.mode, None, "OFF")
            if cmd.target is not None:
                pantilt.move(*cal.pixel_to_angles(*cmd.target))
                last_aim = now
            laser.set(cmd.laser_on)
            if cmd.target is None and now - last_aim > cfg.servo.release_after_s:
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
