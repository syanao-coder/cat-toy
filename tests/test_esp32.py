"""ESP32 との通信部分を、ローカルの UDP / HTTP サーバー相手に確かめる。"""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from cattoy import esp32  # noqa: E402
from cattoy.config import CameraConfig, Esp32Config, LaserConfig, ServoConfig  # noqa: E402


def jpeg(value: int) -> bytes:
    img = np.full((480, 640, 3), value, np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_mjpeg_parser_handles_split_chunks():
    a, b = jpeg(10), jpeg(200)
    stream = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + a + b"\r\n--frame\r\n\r\n" + b + b"\r\n"
    p = esp32.MjpegParser()
    frames = []
    for i in range(0, len(stream), 777):
        frames += p.feed(stream[i : i + 777])
    assert frames == [a, b]


def test_angle_to_pulse():
    cfg = ServoConfig()
    assert esp32.angle_to_pulse_us(0, cfg) == 500
    assert esp32.angle_to_pulse_us(90, cfg) == 1500
    assert esp32.angle_to_pulse_us(180, cfg) == 2500
    assert esp32.angle_to_pulse_us(999, cfg) == 2500


@pytest.fixture
def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(1.0)
    yield sock
    sock.close()


def latest(sock, wait=0.3):
    """wait 秒の間に届いた最後のパケットを返す。"""
    end = time.monotonic() + wait
    msg = None
    while time.monotonic() < end:
        try:
            msg = sock.recv(256).decode()
        except socket.timeout:
            break
    return msg


def test_link_sends_commands_and_keepalive(udp_listener):
    port = udp_listener.getsockname()[1]
    cfg = Esp32Config(host="127.0.0.1", udp_port=port, key="secret")
    pt = esp32.Esp32PanTilt(ServoConfig(), cfg)
    laser = esp32.Esp32Laser(LaserConfig(), cfg)
    assert pt.link is laser.link  # サーボとレーザーで 1 本の通信を共有する

    pt.move(90, 45)
    laser.on()
    msg = latest(udp_listener)
    f = msg.split()
    assert f[0] == "CT1" and f[1] == "secret"
    assert f[3:] == ["1500", "1000", "1", "1"]

    # 何も変えなくても送り続ける（ESP32 の見張り役が止めないように）
    t0 = time.monotonic()
    n = 0
    while time.monotonic() - t0 < 0.5:
        try:
            udp_listener.recv(256)
            n += 1
        except socket.timeout:
            break
    assert n >= 3

    pt.close()
    laser.close()
    assert latest(udp_listener).split()[5:] == ["0", "0"]  # 閉じるとサーボもレーザーも止める
    assert esp32._links == {}


def test_link_requires_host():
    with pytest.raises(ValueError, match="host"):
        esp32.Esp32Link(Esp32Config(host=""))


@pytest.fixture
def fake_esp32():
    frames = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/capture":
                body = jpeg(50)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        frames["n"] += 1
                        body = jpeg(min(250, frames["n"]))
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body + b"\r\n")
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif self.path == "/status":
                body = b'{"fw": "0.1.0", "rssi": -55, "button": 2}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    yield Esp32Config(host="127.0.0.1", http_port=port, stream_port=port, udp_port=9), frames
    srv.shutdown()


def test_camera_stream_and_capture(fake_esp32):
    cfg, frames = fake_esp32
    cam = esp32.Esp32Camera(CameraConfig(), cfg, timeout=3.0)
    try:
        a, ta = cam.read_with_time()
        b, tb = cam.read_with_time()
        assert a.shape == (480, 640, 3)
        assert tb > ta and int(b.mean()) > int(a.mean())  # 毎回新しいフレームを返す

        cam.set_streaming(False)
        still = cam.read()  # 連続取得を止めると静止画を取りに行く
        assert abs(int(still.mean()) - 50) <= 2
        time.sleep(0.3)
        n = frames["n"]
        time.sleep(0.3)
        assert frames["n"] - n <= 1  # 止めた後は映像を受け取っていない

        cam.set_streaming(True)
        c, tc = cam.read_with_time()
        assert tc > tb
    finally:
        cam.close()


def test_status(fake_esp32):
    cfg, _ = fake_esp32
    link = esp32.Esp32Link(cfg)
    try:
        assert link.status()["button"] == 2
    finally:
        link.close()
    assert esp32.Esp32Link(Esp32Config(host="127.0.0.1", http_port=1)).status(timeout=0.3) is None


def test_app_end_to_end_over_network(fake_esp32, udp_listener, tmp_path, monkeypatch):
    """本物の通信経路（HTTP の映像・UDP の指令）で本体ループを動かす。"""
    from cattoy import app
    from cattoy.calibration import Calibration
    from cattoy.config import Config
    from cattoy.tracker import Detection

    esp_cfg, _ = fake_esp32
    esp_cfg.udp_port = udp_listener.getsockname()[1]
    cfg = Config(base_dir=tmp_path, esp32=esp_cfg)
    cfg.play.active_hours = ""
    cfg.play.start_delay_s = 0.2
    cfg.runtime.web_port = 0
    cfg.runtime.standby_interval_s = 0.2
    Calibration.linear_fallback((640, 480), (40, 140), (40, 120)).save(tmp_path / "calibration.json")

    class Det:
        provider = "GPU"

        def __init__(self, *a, **k):
            self.t0 = time.monotonic()

        def detect(self, frame):
            x = 200 + 60 * (time.monotonic() - self.t0)
            return [Detection("cat", 0.9, (x - 30, 220, x + 30, 260))]

    monkeypatch.setattr("cattoy.detector.YoloOnnxDetector", Det)
    stop = threading.Event()
    th = threading.Thread(target=app.run, args=(cfg, 0, stop), daemon=True)
    th.start()
    msgs = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0:
        try:
            msgs.append(udp_listener.recv(256).decode().split())
        except socket.timeout:
            pass
    stop.set()
    th.join(timeout=5.0)
    assert not th.is_alive()
    assert any(m[6] == "1" for m in msgs), "レーザーの点灯指令が届いていない"
    assert len({(m[3], m[4]) for m in msgs}) > 5, "サーボが動いていない"
    end = latest(udp_listener, wait=0.5)
    assert end is not None and end.split()[5:] == ["0", "0"]  # 終了時は消灯・サーボ停止
