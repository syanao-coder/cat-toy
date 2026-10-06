import math
import random

import numpy as np
import pytest

from cattoy.calibration import Calibration, find_laser_dot, grid_angles
from cattoy.geometry import point_in_polygon

W, H = 640, 480
MOUNT_H = 2.3  # 取り付け高さ (m)


def floor_point(pan: float, tilt: float) -> tuple[float, float]:
    """壁の高い位置に付けたパン・チルト: pan=方位角, tilt=水平からの俯角。"""
    phi = math.radians(pan - 90)
    delta = math.radians(tilt)
    r = MOUNT_H / math.tan(delta)
    return r * math.sin(phi), r * math.cos(phi)


def project(x: float, y: float) -> tuple[float, float]:
    """レーザーの 10cm 横に付けたカメラ（俯角 50 度・広角の樽型歪みあり）で床の点を撮る。"""
    p = np.array([x - 0.1, y, -MOUNT_H])
    a = math.radians(50)
    f = np.array([0, math.cos(a), -math.sin(a)])
    right = np.array([1.0, 0, 0])
    down = np.array([0, -math.sin(a), -math.cos(a)])
    u, v = p @ right / (p @ f), p @ down / (p @ f)
    k = 1 - 0.08 * (u * u + v * v)  # 樽型歪み
    return 320 + 330 * u * k, 240 + 330 * v * k


def synth_points(n: int = 9):
    pts = []
    for pan, tilt in grid_angles((55, 125), (32, 75), n):
        x, y = project(*floor_point(pan, tilt))
        if 0 <= x < W and 0 <= y < H:
            pts.append((x, y, pan, tilt))
    return pts


def test_fit_is_accurate_inside_calibrated_region():
    pts = synth_points()
    assert len(pts) > 30
    cal = Calibration.fit(pts, (W, H))
    assert cal.rms_px < 2.0
    area = cal.default_play_area()
    rng = random.Random(0)
    checked = 0
    for _ in range(500):
        pan, tilt = rng.uniform(55, 125), rng.uniform(32, 75)
        x, y = project(*floor_point(pan, tilt))
        if not point_in_polygon((x, y), area):
            continue
        p, t = cal.pixel_to_angles(x, y)
        assert math.hypot(p - pan, t - tilt) < 0.6
        checked += 1
    assert checked > 100


def test_outlier_is_removed():
    pts = synth_points()
    pts.append((100.0, 100.0, 120.0, 70.0))  # 家具に当たった等の外れ値
    cal = Calibration.fit(pts, (W, H))
    assert len(cal.points) == len(pts) - 1
    assert cal.rms_px < 2.0


def test_degree_is_lowered_when_few_points():
    pts = synth_points(3)
    cal = Calibration.fit(pts, (W, H))
    assert cal.pix_to_servo.degree < 3


def test_save_load_roundtrip(tmp_path):
    cal = Calibration.fit(synth_points(), (W, H))
    path = tmp_path / "cal.json"
    cal.save(path)
    loaded = Calibration.load(path)
    assert loaded.image_size == (W, H)
    assert loaded.pixel_to_angles(320, 300) == pytest.approx(cal.pixel_to_angles(320, 300))


def test_linear_fallback():
    cal = Calibration.linear_fallback((W, H), (30, 150), (40, 140))
    assert cal.pixel_to_angles(0, 0) == pytest.approx((30, 40), abs=1e-6)
    assert cal.pixel_to_angles(W, H) == pytest.approx((150, 140), abs=1e-6)


def _scene(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(40, 120, size=(H, W, 3), dtype=np.uint8)


def _with_dot(img: np.ndarray, cx: float, cy: float, peak: float = 150) -> np.ndarray:
    ys, xs = np.mgrid[0:H, 0:W]
    g = np.exp(-((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * 2.0**2)) * peak
    out = img.astype(np.float64)
    out[:, :, 2] += g  # 赤
    out[:, :, 1] += g * 0.3
    return np.clip(out, 0, 255).astype(np.uint8)


def test_find_laser_dot_subpixel():
    off = _scene()
    on = _with_dot(off, 200.4, 333.7)
    x, y = find_laser_dot(off, on)
    assert x == pytest.approx(200.4, abs=0.5)
    assert y == pytest.approx(333.7, abs=0.5)


def test_find_laser_dot_none_when_absent_or_lighting_changed():
    off = _scene()
    assert find_laser_dot(off, off.copy()) is None
    brighter = np.clip(off.astype(int) + 60, 0, 255).astype(np.uint8)
    assert find_laser_dot(off, brighter) is None
