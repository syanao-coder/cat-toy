"""カメラと検出器を差し替えて、本体ループ（app.run）を通しで動かすスモークテスト。"""

import json
import socket
import threading
import time
import urllib.request

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
        self.streaming_calls = []

    def set_streaming(self, on):
        self.streaming_calls.append(on)

    def read(self):
        time.sleep(0.05)
        if time.monotonic() - self.t0 > self.seconds:
            raise StopSim
        return np.zeros((480, 640, 3), np.uint8)


class FakeDetector:
    """cat_after 秒後から、画面を左右に歩く猫を 1 匹返す。"""

    provider = "CPU"
    calls = 0

    def __init__(self, *a, cat_after=0.0, **kw):
        self.t0 = time.monotonic()
        self.cat_after = cat_after
        FakeDetector.calls = 0

    def detect(self, frame):
        FakeDetector.calls += 1
        el = time.monotonic() - self.t0
        if el < self.cat_after:
            return []
        x = 200 + 40 * el
        return [Detection("cat", 0.9, (x - 30, 220, x + 30, 260))]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sim(monkeypatch, tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.servo.backend = "mock"
    cfg.laser.backend = "mock"
    cfg.play.active_hours = ""
    cfg.play.start_delay_s = 0.2
    cfg.runtime.web_port = 0
    cfg.calibration.path = "/nonexistent/calibration.json"  # mock なので仮の対応付けになる

    rec = {"laser": [], "pantilts": [], "camera": None}
    orig_set = hardware.MockLaser.set

    def record_set(self, on):
        orig_set(self, on)
        rec["laser"].append(self.is_on)

    def make_pantilt(c, *a):
        pt = hardware.MockPanTilt(c)
        rec["pantilts"].append(pt)
        return pt

    def make_camera(c, *a):
        rec["camera"] = FakeCamera(rec.get("seconds", 3.0))
        return rec["camera"]

    monkeypatch.setattr(hardware.MockLaser, "set", record_set)
    monkeypatch.setattr(app, "make_camera", make_camera)
    monkeypatch.setattr(app, "make_pantilt", make_pantilt)
    monkeypatch.setattr("cattoy.detector.YoloOnnxDetector", FakeDetector)
    return cfg, rec


def test_run_loop_with_mocks(sim):
    cfg, rec = sim
    with pytest.raises(StopSim):
        app.run(cfg)
    assert any(rec["laser"]), "猫がいるのにレーザーが点灯しなかった"
    assert rec["laser"][-1] is False, "終了時にレーザーが消えていない"
    pt = rec["pantilts"][0]
    assert (pt.pan, pt.tilt) != (cfg.servo.pan_home, cfg.servo.tilt_home)
    log = json.loads((cfg.base_dir / "state.json").read_text())
    assert sum(log["play_log"].values()) > 1.0  # 遊んだ時間が記録されている


def test_off_never_lights_and_skips_detection(sim):
    cfg, rec = sim
    (cfg.base_dir / "state.json").write_text(json.dumps({"enabled": False}))
    rec["seconds"] = 1.5
    th = threading.Thread(target=lambda: pytest.raises(StopSim, app.run, cfg), daemon=True)
    th.start()
    th.join(timeout=3.0)
    assert not any(rec["laser"])
    assert FakeDetector.calls == 0
    assert rec["camera"].streaming_calls and rec["camera"].streaming_calls[-1] is False


def test_standby_detects_slowly_until_cat_appears(sim, monkeypatch):
    cfg, rec = sim
    cfg.runtime.standby_interval_s = 0.5
    monkeypatch.setattr("cattoy.detector.YoloOnnxDetector", lambda *a, **k: FakeDetector(cat_after=1.6))
    rec["seconds"] = 1.5
    with pytest.raises(StopSim):
        app.run(cfg)
    assert FakeDetector.calls <= 4  # 1.5 秒間、0.5 秒ごと（連続なら 20 回以上）
    assert rec["camera"].streaming_calls[0] is False


def test_web_api_toggles(sim):
    cfg, rec = sim
    port = free_port()
    rec["seconds"] = 4.0
    th = threading.Thread(target=lambda: pytest.raises(StopSim, app.run, cfg, port), daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            st = json.loads(urllib.request.urlopen(base + "/api/status", timeout=1).read())
            break
        except OSError:
            time.sleep(0.1)
    assert st["enabled"] is True and st["detector"] == "CPU"
    page = urllib.request.urlopen(base + "/", timeout=1).read().decode()
    assert "ねこレーザー" in page
    req = urllib.request.Request(base + "/api/enabled", data=b'{"enabled": false}', headers={"Content-Type": "application/json"})
    st = json.loads(urllib.request.urlopen(req, timeout=1).read())
    assert st["enabled"] is False and st["label"] == "停止中"
    n = len(rec["laser"])
    time.sleep(0.5)
    assert not any(rec["laser"][n:])  # OFF にした後は点灯しない
    th.join(timeout=6.0)
    assert json.loads((cfg.base_dir / "state.json").read_text())["enabled"] is False
