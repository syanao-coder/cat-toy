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
from .config import Config
from .hardware import make_camera, make_laser, make_pantilt

log = logging.getLogger(__name__)


def run_calibration(cfg: Config, web_port: int | None = None) -> Calibration:
    c, s = cfg.calibration, cfg.servo
    camera = make_camera(cfg.camera)
    laser = make_laser(cfg.laser)
    pantilt = make_pantilt(cfg.servo)
    preview = None
    try:
        if web_port:
            from .preview import PreviewServer

            preview = PreviewServer(web_port)
        laser.off()
        angles = grid_angles((s.pan_min, s.pan_max), (s.tilt_min, s.tilt_max), c.grid)
        points: list[tuple[float, float, float, float]] = []
        last_frame = camera.read_fresh()
        for i, (pan, tilt) in enumerate(angles, 1):
            pantilt.move(pan, tilt)
            time.sleep(c.settle_s)
            off = camera.read_fresh().copy()
            laser.on()
            time.sleep(0.15)
            on = camera.read_fresh().copy()
            laser.off()
            dot = find_laser_dot(off, on, c.min_dot_intensity)
            if dot is None:
                log.info("[%2d/%d] pan=%5.1f tilt=%5.1f → 見つかりません", i, len(angles), pan, tilt)
            else:
                points.append((dot[0], dot[1], pan, tilt))
                log.info("[%2d/%d] pan=%5.1f tilt=%5.1f → (%.1f, %.1f)", i, len(angles), pan, tilt, *dot)
            last_frame = off
            if preview is not None:
                preview.update(_draw_points(on, points, None))

        cal = Calibration.fit(points, camera.size, c.poly_degree)
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
        laser.close()
        pantilt.close()
        camera.close()
        if preview is not None:
            preview.close()


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
