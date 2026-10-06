"""レーザー点を描く仮想カメラで、自動キャリブレーションの手順を通しで動かす。"""

import math
import time
import types

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
    monkeypatch.setattr(calibrate, "make_laser", lambda *a: laser)
    monkeypatch.setattr(calibrate, "make_pantilt", lambda *a: pantilt)
    monkeypatch.setattr(calibrate, "make_camera", lambda *a: VirtualRoom(laser, pantilt))
    # time.sleep を全体で差し替えると他のテストのスレッドが空回りするので、calibrate の中だけ差し替える
    monkeypatch.setattr(calibrate, "time", types.SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic))

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


class DelayedRoom(VirtualRoom):
    """点灯から 0.2 秒経たないとレーザーが写らない（ネットワーク越しの映像の遅れを模擬）。"""

    def __init__(self, laser, pantilt):
        super().__init__(laser, pantilt)
        self.on_since = None

    def read(self):
        time.sleep(0.03)
        if self.laser.is_on and self.on_since is None:
            self.on_since = time.monotonic()
        if not self.laser.is_on:
            self.on_since = None
        visible = self.on_since is not None and time.monotonic() - self.on_since >= 0.2
        if not visible:
            return self.bg.copy()
        return super().read()


def test_measure_latency():
    cfg = Config()
    laser = hardware.MockLaser(cfg.laser)
    pantilt = hardware.MockPanTilt(cfg.servo)
    pantilt.move(90, 50)
    latency = calibrate.measure_latency(DelayedRoom(laser, pantilt), laser, 60, trials=3)
    assert 0.18 <= latency <= 0.32
    assert not laser.is_on
