"""ESP32 の遠隔メンテナンス（ファームウェアの書き換え・再起動・Wi-Fi 設定）を、偽の ESP32 相手に確かめる。"""

import hashlib
import json
import struct
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cattoy import cli, firmware
from cattoy.config import Esp32Config


def fake_image(chip_id=9, size=200_000, app_magic=0xABCD5432):
    head = bytearray(size)
    head[0] = 0xE9
    struct.pack_into("<H", head, 12, chip_id)
    struct.pack_into("<I", head, 32, app_magic)
    head[48:48 + 5] = b"0.2.0"
    head[80:80 + 13] = b"cattoy_esp32\0"
    head[112:112 + 8] = b"12:34:56"
    head[128:128 + 11] = b"Oct  6 2026"
    return bytes(head)


def test_inspect_image_accepts_s3_app():
    info = firmware.inspect_image(fake_image())
    assert info["project"] == "cattoy_esp32"
    assert info["built"] == "Oct  6 2026 12:34:56"
    assert info["md5"] == hashlib.md5(fake_image()).hexdigest()


@pytest.mark.parametrize(
    "data,msg",
    [
        (b"hello" * 100, "ファームウェアのファイルではありません"),
        (fake_image(chip_id=0), "ESP32-S3 用ではありません"),
        (fake_image(app_magic=0), "アプリのイメージではありません"),
        (fake_image(size=4 * 1024 * 1024), "大きすぎます"),
    ],
)
def test_inspect_image_rejects(data, msg):
    with pytest.raises(ValueError, match=msg):
        firmware.inspect_image(data)


@pytest.fixture
def fake_esp32():
    rec = {"posts": [], "uptime": 5000, "reject": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/status":
                st = {"fw": "0.2.0", "built": "x", "partition": "app1", "uptime_s": rec["uptime"], "ssid": "home", "rssi": -50}
                self._send(200, json.dumps(st).encode(), "application/json")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            rec["posts"].append((self.path, self.headers, body))  # 見出しは大文字小文字を区別しない
            if rec["reject"] or self.headers.get("X-Cattoy-Key") != "secret":
                self._send(403, "key が違います".encode())
                return
            if self.path == "/update":
                rec["uptime"] = 3  # 再起動したことにする
            self._send(200, b"OK")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Esp32Config(host="127.0.0.1", http_port=srv.server_address[1], udp_port=9, key="secret"), rec
    srv.shutdown()


def test_upload_sends_key_md5_and_body(fake_esp32):
    cfg, rec = fake_esp32
    data = fake_image()
    firmware.upload_firmware(cfg, data)
    path, headers, body = rec["posts"][-1]
    assert path == "/update" and body == data
    assert headers["X-Cattoy-Key"] == "secret"
    assert headers["X-Cattoy-MD5"] == hashlib.md5(data).hexdigest()


def test_upload_reports_rejection(fake_esp32):
    cfg, rec = fake_esp32
    rec["reject"] = True
    with pytest.raises(RuntimeError, match="拒否"):
        firmware.upload_firmware(cfg, fake_image())


def test_bad_file_is_never_sent(fake_esp32):
    cfg, rec = fake_esp32
    with pytest.raises(ValueError):
        firmware.upload_firmware(cfg, b"\0" * 1000)
    assert rec["posts"] == []


def test_set_wifi_and_reboot(fake_esp32):
    cfg, rec = fake_esp32
    firmware.set_wifi(cfg, "新しいWi-Fi", "p&ss=word")
    path, _, body = rec["posts"][-1]
    assert path == "/wifi"
    assert urllib.parse.parse_qs(body.decode()) == {"ssid": ["新しいWi-Fi"], "pass": ["p&ss=word"]}
    firmware.reboot(cfg)
    assert rec["posts"][-1][0] == "/reboot"


def test_cli_esp32_update(fake_esp32, tmp_path, monkeypatch, capsys):
    cfg, rec = fake_esp32
    conf = tmp_path / "config.toml"
    conf.write_text(f'[esp32]\nhost = "127.0.0.1"\nhttp_port = {cfg.http_port}\nudp_port = 9\nkey = "secret"\n')
    binfile = tmp_path / "cattoy_esp32.ino.bin"
    binfile.write_bytes(fake_image())
    monkeypatch.setattr(firmware, "time", types.SimpleNamespace(sleep=lambda s: None, monotonic=__import__("time").monotonic))
    cli.main(["--config", str(conf), "esp32-update", str(binfile)])
    out = capsys.readouterr().out
    assert "書き換えました" in out and "再起動しました" in out
    assert rec["posts"][-1][0] == "/update"


def test_web_api_firmware_action():
    from cattoy.preview import PreviewServer

    got = {}

    def update(body):
        got["body"] = body
        if body == b"bad":
            raise ValueError("ESP32 のファームウェアのファイルではありません")
        return {"ok": True}

    srv = PreviewServer(0, status=lambda: {}, set_enabled=lambda on: None, actions={"/api/esp32/firmware": update})
    port = srv.httpd.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/api/esp32/firmware"
        r = urllib.request.urlopen(urllib.request.Request(url, data=b"\xe9data"), timeout=2)
        assert json.loads(r.read()) == {"ok": True} and got["body"] == b"\xe9data"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(url, data=b"bad"), timeout=2)
        assert e.value.code == 400
    finally:
        srv.close()
