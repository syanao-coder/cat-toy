"""レーザー点を描く仮想カメラで、自動キャリブレーションの手順を通しで動かす。"""

import math

import numpy as np

from cattoy import calibrate, hardware
from cattoy.calibration import Calibration
from cattoy.config import Config
from test_calibration import floor_point, project


class VirtualRoom(hardware.Camera):
    def __init__(self, laser, pantilt):
        self.size = (640, 480)
        self.laser, self.pantilt = laser, pantilt
        self.bg = np.random.default_rng(0).integers(40, 120, size=(480, 640, 3), dtype=np.uint8)

    def read(self):
        img = self.bg.copy()
        if self.laser.is_on:
            x, y = project(*floor_point(self.pantilt.pan, self.pantilt.tilt))
            if 0 <= x < 640 and 0 <= y < 480:
                ys, xs = np.mgrid[0:480, 0:640]
                g = np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / 8.0) * 150
                img = img.astype(np.float64)
                img[:, :, 2] += g
                img = np.clip(img, 0, 255).astype(np.uint8)
        return img


def test_calibration_routine(monkeypatch, tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.servo.backend = cfg.laser.backend = "mock"
    cfg.servo.pan_min, cfg.servo.pan_max = 55, 125
    cfg.servo.tilt_min, cfg.servo.tilt_max = 32, 75
    cfg.calibration.settle_s = 0
    laser = hardware.MockLaser(cfg.laser)
    pantilt = hardware.MockPanTilt(cfg.servo)
    monkeypatch.setattr(calibrate, "make_laser", lambda c: laser)
    monkeypatch.setattr(calibrate, "make_pantilt", lambda c: pantilt)
    monkeypatch.setattr(calibrate, "make_camera", lambda c: VirtualRoom(laser, pantilt))
    monkeypatch.setattr(calibrate.time, "sleep", lambda s: None)

    cal = calibrate.run_calibration(cfg)

    assert (tmp_path / "calibration.json").exists()
    assert (tmp_path / "calibration.jpg").exists()
    assert len(cal.points) > 60
    assert cal.rms_px < 1.0
    loaded = Calibration.load(tmp_path / "calibration.json")
    x, y = project(*floor_point(90, 50))
    pan, tilt = loaded.pixel_to_angles(x, y)
    assert math.hypot(pan - 90, tilt - 50) < 0.5
    assert not laser.is_on
