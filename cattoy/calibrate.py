"""自動キャリブレーション。

サーボを格子状に動かしながらレーザーを点滅させ、カメラに写った点の位置を記録する。
得られた「ピクセル座標 ⇔ サーボ角度」の組から写像を求めて保存する。
実行中はレーザーが部屋中を照らすので、猫と人がいない状態で行うこと。
"""

from __future__ import annotations

import logging
import time

import numpy as np

from .calibration import Calibration, find_laser_dot, grid_angles
from typing import Callable

from .config import Config
from .hardware import Camera, Laser, PanTilt, make_camera, make_laser, make_pantilt

log = logging.getLogger(__name__)


def run_calibration(cfg: Config, web_port: int | None = None) -> Calibration:
    """コマンド（cattoy calibrate）から実行する。機器をつないで測り、終わったら閉じる。"""
    camera = make_camera(cfg.camera, cfg.esp32)
    laser = make_laser(cfg.laser, cfg.esp32)
    pantilt = make_pantilt(cfg.servo, cfg.esp32)
    preview = None
    try:
        if web_port:
            from .preview import PreviewServer

            preview = PreviewServer(web_port)
        return calibrate_with(cfg, camera, laser, pantilt, on_frame=preview.update if preview else None)
    finally:
        laser.close()
        pantilt.close()
        camera.close()
        if preview is not None:
            preview.close()


def calibrate_with(
    cfg: Config,
    camera: Camera,
    laser: Laser,
    pantilt: PanTilt,
    progress: Callable[[str], None] | None = None,
    on_frame: Callable[[np.ndarray], None] | None = None,
) -> Calibration:
    """つながっている機器で測って calibration.json に保存する（操作画面からも使う）。"""
    c, s = cfg.calibration, cfg.servo
    report = progress or (lambda msg: None)
    try:
        laser.off()
        camera.set_streaming(True)
        angles = grid_angles((s.pan_min, s.pan_max), (s.tilt_min, s.tilt_max), c.grid)
        points: list[tuple[float, float, float, float]] = []
        pantilt.move(*angles[len(angles) // 2])
        time.sleep(1.0)
        report("映像の遅れを測っています")
        latency = measure_latency(camera, laser, c.min_dot_intensity)
        log.info("映像の遅れ: %.0f ms", latency * 1000)
        settle = max(c.settle_s, latency + 0.2)  # 遅れがあると、前の点灯が次の「消灯」画像に写り込むため
        last_frame = camera.read_fresh()
        for i, (pan, tilt) in enumerate(angles, 1):
            report(f"測定中 {i}/{len(angles)}（見つかった点 {len(points)}）")
            pantilt.move(pan, tilt)
            time.sleep(settle)
            off = camera.read_fresh().copy()
            laser.on()
            time.sleep(max(0.15, latency + 0.1))
            on = camera.read_fresh().copy()
            laser.off()
            dot = find_laser_dot(off, on, c.min_dot_intensity)
            if dot is None:
                log.info("[%2d/%d] pan=%5.1f tilt=%5.1f → 見つかりません", i, len(angles), pan, tilt)
            else:
                points.append((dot[0], dot[1], pan, tilt))
                log.info("[%2d/%d] pan=%5.1f tilt=%5.1f → (%.1f, %.1f)", i, len(angles), pan, tilt, *dot)
            last_frame = off
            if on_frame is not None:
                on_frame(_draw_points(on, points, None))

        cal = Calibration.fit(points, camera.size, c.poly_degree)
        cal.latency_s = latency
        path = cfg.resolve(c.path)
        cal.save(path)
        log.info(
            "保存しました: %s（%d 点 / 誤差 %.1f px・%.2f 度）",
            path, len(cal.points), cal.rms_px, cal.rms_deg,
        )
        if cal.rms_px > 8:
            log.warning("誤差が大きめです。サーボのガタ・照明の変化・床以外（家具や壁）に当たった点がないか確認してください。")
        try:
            import cv2

            debug_path = path.with_suffix(".jpg")
            cv2.imwrite(str(debug_path), _draw_points(last_frame, points, cal))
            log.info("確認用画像: %s（緑=実測, 赤=近似式による推定, 水色=遊ぶ範囲の既定値）", debug_path)
        except ImportError:
            pass
        return cal
    finally:
        laser.off()
        pantilt.release()


def measure_latency(camera, laser, min_intensity: int, trials: int = 5, timeout: float = 3.0) -> float:
    """レーザーを点けてから、その光がカメラ画像に写るまでの時間（中央値）を測る。

    映像がネットワーク経由で届く分の遅れを、追跡の先読みに使う。点が見つからなければ 0。
    """
    results = []
    for _ in range(trials):
        laser.off()
        time.sleep(0.5)
        off = camera.read_fresh().copy()
        t0 = time.monotonic()
        laser.on()
        while time.monotonic() - t0 < timeout:
            frame, t = camera.read_with_time()
            if find_laser_dot(off, frame, min_intensity) is not None:
                results.append(max(t - t0, 0.0))
                break
        laser.off()
    if not results:
        log.warning("映像の遅れを測れませんでした（レーザーの点が写っていません）。0 として扱います")
        return 0.0
    return float(np.median(results))


def _draw_points(frame: np.ndarray, points: list[tuple[float, float, float, float]], cal: Calibration | None) -> np.ndarray:
    import cv2

    img = frame.copy()
    for x, y, pan, tilt in points:
        cv2.circle(img, (int(x), int(y)), 4, (0, 255, 0), 1)
        if cal is not None:
            ex, ey = cal.angles_to_pixel(pan, tilt)
            cv2.drawMarker(img, (int(ex), int(ey)), (0, 0, 255), cv2.MARKER_CROSS, 6)
    if cal is not None:
        area = np.array([[int(x), int(y)] for x, y in cal.default_play_area()], dtype=np.int32)
        cv2.polylines(img, [area], True, (255, 200, 0), 1)
    return img
