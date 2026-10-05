"""カメラと検出器を差し替えて、本体ループ（app.run）を通しで動かすスモークテスト。"""

import time

import numpy as np
import pytest

from cattoy import app, hardware
from cattoy.config import Config
from cattoy.tracker import Detection


class StopSim(Exception):
    pass


class FakeCamera(hardware.Camera):
    def __init__(self, seconds):
        self.size = (640, 480)
        self.t0 = time.monotonic()
        self.seconds = seconds

    def read(self):
        time.sleep(0.05)
        if time.monotonic() - self.t0 > self.seconds:
            raise StopSim
        return np.zeros((480, 640, 3), np.uint8)


class FakeDetector:
    """画面を左右に歩く猫を 1 匹返す。"""

    def __init__(self, *a, **kw):
        self.t0 = time.monotonic()

    def detect(self, frame):
        x = 200 + 40 * (time.monotonic() - self.t0)
        return [Detection("cat", 0.9, (x - 30, 220, x + 30, 260))]


def test_run_loop_with_mocks(monkeypatch):
    cfg = Config()
    cfg.servo.backend = "mock"
    cfg.laser.backend = "mock"
    cfg.play.active_hours = ""
    cfg.play.start_delay_s = 0.2
    cfg.calibration.path = "/nonexistent/calibration.json"  # mock なので仮の対応付けになる

    laser_states = []
    pantilts = []
    orig_set = hardware.MockLaser.set

    def record_set(self, on):
        orig_set(self, on)
        laser_states.append(self.is_on)

    def make_pantilt(c):
        pt = hardware.MockPanTilt(c)
        pantilts.append(pt)
        return pt

    monkeypatch.setattr(hardware.MockLaser, "set", record_set)
    monkeypatch.setattr(app, "make_camera", lambda c: FakeCamera(3.0))
    monkeypatch.setattr(app, "make_pantilt", make_pantilt)
    monkeypatch.setattr("cattoy.detector.YoloOnnxDetector", FakeDetector)

    with pytest.raises(StopSim):
        app.run(cfg, web_port=0)

    assert any(laser_states), "猫がいるのにレーザーが点灯しなかった"
    assert laser_states[-1] is False, "終了時にレーザーが消えていない"
    pt = pantilts[0]
    assert (pt.pan, pt.tilt) != (cfg.servo.pan_home, cfg.servo.tilt_home)
