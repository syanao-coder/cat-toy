"""本体の実行ループ。

  映像スレッド:   カメラ → YOLO 検出 → 追跡器の更新（数〜十数 fps）
  制御ループ:     追跡器から猫の位置を外挿 → 遊び方の決定 → サーボ・レーザー（50 Hz）

検出は遅いので、制御を別ループにして外挿することでレーザーの動きを滑らかにしている。
"""

from __future__ import annotations

import datetime as dt
import logging
import signal
import threading
import time

from .behavior import Command, PlayBehavior
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


class _Shared:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cmd: Command | None = None
        self.person_seen_at = -1e9
        self.fps = 0.0
        self.error: BaseException | None = None


def run(cfg: Config, web_port: int | None = None) -> None:
    from .detector import YoloOnnxDetector

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    camera = make_camera(cfg.camera)
    laser = make_laser(cfg.laser)
    pantilt = make_pantilt(cfg.servo)
    preview = None
    vision: threading.Thread | None = None
    try:
        size = camera.size
        cal = load_calibration(cfg, size)
        area = play_area(cfg, cal, size)
        goal = finish_point(cfg, size)
        d = cfg.detector
        detector = YoloOnnxDetector(cfg.resolve(d.model_path), d.input_size, d.conf_threshold, d.iou_threshold, d.threads)
        tracker = CatTracker(lost_timeout_s=cfg.play.lost_timeout_s)
        behavior = PlayBehavior(cfg.play, area, size, finish_point=goal)
        shared = _Shared()

        port = web_port if web_port is not None else cfg.runtime.web_port
        if port:
            from .preview import PreviewServer

            preview = PreviewServer(port)

        def vision_loop() -> None:
            from .preview import draw_overlay

            last = time.monotonic()
            try:
                while not stop.is_set():
                    frame = camera.read()
                    t = time.monotonic()
                    dets: list[Detection] = detector.detect(frame)
                    tracker.update(t, [x for x in dets if x.label == "cat"])
                    now = time.monotonic()
                    with shared.lock:
                        if any(x.label == "person" for x in dets):
                            shared.person_seen_at = t
                        shared.fps = 0.8 * shared.fps + 0.2 / max(now - last, 1e-3)
                        cmd, fps = shared.cmd, shared.fps
                    last = now
                    if preview is not None:
                        img = draw_overlay(
                            frame, area, dets, tracker.state_at(now), cmd, goal, cfg.play.keepout_margin,
                            f"{fps:.1f}fps played {behavior.played_s:.0f}s",
                        )
                        preview.update(img)
            except BaseException as e:  # 制御ループ側で検知して安全に止める
                shared.error = e
                stop.set()

        vision = threading.Thread(target=vision_loop, name="vision", daemon=True)
        vision.start()

        log.info("開始しました（画像 %dx%d, 制御 %.0f Hz）", size[0], size[1], cfg.runtime.control_hz)
        period = 1.0 / cfg.runtime.control_hz
        last_aim = time.monotonic()
        next_tick = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            cat = tracker.state_at(now)
            with shared.lock:
                person = now - shared.person_seen_at < PERSON_HOLD_S
            cmd = behavior.step(now, cat, person, is_active_time(cfg.play.active_hours, dt.datetime.now().time()))
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
        if vision is not None:
            vision.join(timeout=3.0)
        pantilt.close()
        camera.close()
        if preview is not None:
            preview.close()
        log.info("停止しました")
