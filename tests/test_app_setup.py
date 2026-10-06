"""SSH を使わず、操作画面だけで初期設定（可動範囲の調整 → 保存 → 位置合わせ）を終えられることを確かめる。"""

import json
import socket
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

from cattoy import app, calibrate, hardware
from cattoy.config import Config, load_full_config
from test_calibrate_routine import VirtualRoom


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NoCat:
    provider = "CPU"

    def __init__(self, *a, **k):
        pass

    def detect(self, frame):
        return []


@pytest.fixture
def running(monkeypatch, tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.play.active_hours = ""
    laser = hardware.MockLaser(cfg.laser)
    pantilt = hardware.MockPanTilt(cfg.servo)  # 実機（esp32）の設定のまま、機器だけ差し替える
    monkeypatch.setattr(app, "make_laser", lambda *a: laser)
    monkeypatch.setattr(app, "make_pantilt", lambda *a: pantilt)
    monkeypatch.setattr(app, "make_camera", lambda *a: VirtualRoom(laser, pantilt))
    monkeypatch.setattr("cattoy.detector.YoloOnnxDetector", NoCat)
    monkeypatch.setattr(calibrate, "time", types.SimpleNamespace(sleep=lambda s: None, monotonic=time.monotonic))
    port = free_port()
    stop = threading.Event()
    th = threading.Thread(target=app.run, args=(cfg, port, stop), daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/status", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield cfg, base, laser, pantilt
    stop.set()
    th.join(timeout=5)
    assert not th.is_alive()
    assert not laser.is_on


def get(base, path):
    return json.loads(urllib.request.urlopen(base + path, timeout=2).read())


def post(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def test_first_setup_from_web(running, tmp_path):
    cfg, base, laser, pantilt = running

    st = get(base, "/api/status")
    assert st["needs_calibration"] is True and st["label"] == "位置合わせが必要です"
    assert not laser.is_on

    # ① 可動範囲: 保存済みの範囲の外にも動かせる（範囲を広げるため）
    post(base, "/api/aim", {"pan": 20, "tilt": 50, "laser": True})
    assert (pantilt.pan, pantilt.tilt) == (20, 50) and laser.is_on
    assert get(base, "/api/status")["label"] == "可動範囲の調整中"
    post(base, "/api/aim/stop")
    assert not laser.is_on

    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, "/api/servo-limits", {"pan_min": 120, "pan_max": 60})
    assert e.value.code == 400
    post(base, "/api/servo-limits", {"pan_min": 55, "pan_max": 125, "tilt_min": 32, "tilt_max": 75})
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["servo"]["pan_min"] == 55 and saved["servo"]["tilt_max"] == 75
    assert load_full_config(tmp_path / "config.toml", environ={}).servo.pan_max == 125  # 再起動しても残る

    # ② 位置合わせ
    post(base, "/api/calibrate")
    for _ in range(200):
        st = get(base, "/api/status")
        if st["maintenance"]["mode"] is None and st["maintenance"]["result"]:
            break
        time.sleep(0.1)
    assert st["maintenance"]["result"]["ok"], st["maintenance"]["result"]
    assert st["needs_calibration"] is False and st["calibration"]["points"] > 60
    assert st["label"] == "猫を待っています"
    assert (tmp_path / "calibration.json").exists()
    assert urllib.request.urlopen(base + "/calibration.jpg", timeout=2).headers["Content-Type"] == "image/jpeg"


def test_calibration_failure_keeps_running(running, monkeypatch):
    cfg, base, laser, pantilt = running
    cfg.servo.pan_min, cfg.servo.pan_max = 0, 5  # 映らない方向だけを測らせる
    cfg.servo.tilt_min, cfg.servo.tilt_max = 0, 5
    post(base, "/api/calibrate")
    for _ in range(200):
        st = get(base, "/api/status")
        if st["maintenance"]["mode"] is None and st["maintenance"]["result"]:
            break
        time.sleep(0.1)
    assert st["maintenance"]["result"]["ok"] is False
    assert st["needs_calibration"] is True  # 失敗してもアプリは動き続ける
