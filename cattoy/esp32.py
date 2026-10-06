"""カメラ付き ESP32（firmware/cattoy_esp32）をネットワーク越しに使う。

  映像     ESP32 → NAS   HTTP の MJPEG（:81/stream）または静止画（:80/capture）
  指令     NAS → ESP32   UDP（:4210）。サーボのパルス幅とレーザーの ON/OFF を 1 行のテキストで送る
  状態     NAS → ESP32   HTTP（:80/status）。電波強度・ボタンが押された回数など

ESP32 は指令が 0.3 秒届かないとレーザーを消し、3 秒届かないとサーボを止める。
NAS 側はこのため、状態が変わらなくても 0.1 秒ごとに同じ指令を送り続ける。
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request

import numpy as np

from .config import CameraConfig, Esp32Config, LaserConfig, ServoConfig
from .hardware import Camera, Laser, PanTilt

log = logging.getLogger(__name__)

KEEPALIVE_S = 0.1


# ====================================================================== 指令（UDP）


class Esp32Link:
    """サーボとレーザーで共有する、ESP32 への指令チャンネル。"""

    def __init__(self, cfg: Esp32Config):
        if not cfg.host:
            raise ValueError("config.toml の [esp32] host に ESP32 の IP アドレスを書いてください")
        self.cfg = cfg
        self.addr = (cfg.host, cfg.udp_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._lock = threading.Lock()
        self.pan_us = 1500
        self.tilt_us = 1500
        self.servo_on = False
        self.laser_on = False
        self._seq = 0
        self._users = 0
        self._last_error = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._keepalive, name="esp32-link", daemon=True)
        self._thread.start()

    def message(self) -> bytes:
        key = self.cfg.key or "-"
        return f"CT1 {key} {self._seq} {self.pan_us} {self.tilt_us} {int(self.servo_on)} {int(self.laser_on)}\n".encode()

    def update(self, **fields: object) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)
            self._send_locked()

    def _send_locked(self) -> None:
        self._seq = (self._seq + 1) % 1_000_000_000
        try:
            self.sock.sendto(self.message(), self.addr)
        except OSError as e:
            now = time.monotonic()
            if now - self._last_error > 10:
                log.warning("ESP32 へ送れません (%s): %s", self.cfg.host, e)
                self._last_error = now

    def _keepalive(self) -> None:
        while not self._stop.wait(KEEPALIVE_S):
            with self._lock:
                self._send_locked()

    def status(self, timeout: float = 1.0) -> dict | None:
        url = f"http://{self.cfg.host}:{self.cfg.http_port}/status"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (OSError, ValueError):
            return None

    def close(self) -> None:
        with self._lock:
            self.laser_on = False
            self.servo_on = False
            for _ in range(3):  # UDP は落ちることがあるので念のため複数回
                self._send_locked()
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.sock.close()


_links: dict[tuple, Esp32Link] = {}
_links_lock = threading.Lock()


def acquire_link(cfg: Esp32Config) -> Esp32Link:
    key = (cfg.host, cfg.udp_port)
    with _links_lock:
        link = _links.get(key)
        if link is None:
            link = _links[key] = Esp32Link(cfg)
        link._users += 1
        return link


def release_link(link: Esp32Link) -> None:
    with _links_lock:
        link._users -= 1
        if link._users <= 0:
            _links.pop((link.cfg.host, link.cfg.udp_port), None)
            link.close()


def angle_to_pulse_us(angle: float, cfg: ServoConfig) -> int:
    ratio = min(max(angle / cfg.actuation_range, 0.0), 1.0)
    return round(cfg.min_pulse_us + ratio * (cfg.max_pulse_us - cfg.min_pulse_us))


class Esp32PanTilt(PanTilt):
    def __init__(self, cfg: ServoConfig, esp32: Esp32Config):
        super().__init__(cfg)
        self.link = acquire_link(esp32)
        self._closed = False

    def _write(self, pan: float, tilt: float) -> None:
        self.link.update(pan_us=angle_to_pulse_us(pan, self.cfg), tilt_us=angle_to_pulse_us(tilt, self.cfg), servo_on=True)

    def _release(self) -> None:
        self.link.update(servo_on=False)

    def close(self) -> None:
        super().close()
        if not self._closed:
            self._closed = True
            release_link(self.link)


class Esp32Laser(Laser):
    def __init__(self, cfg: LaserConfig, esp32: Esp32Config):
        super().__init__(cfg)
        self.link = acquire_link(esp32)
        self._closed = False

    def _write(self, on: bool) -> None:
        self.link.update(laser_on=on)

    def close(self) -> None:
        super().close()
        if not self._closed:
            self._closed = True
            release_link(self.link)


# ====================================================================== 映像（HTTP）


class MjpegParser:
    """MJPEG のバイト列から JPEG を 1 枚ずつ切り出す（SOI 0xFFD8 〜 EOI 0xFFD9）。"""

    def __init__(self, max_buffer: int = 2_000_000):
        self.buf = bytearray()
        self.max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(b"\xff\xd8")
            if start < 0:
                self.buf.clear()
                break
            end = self.buf.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    del self.buf[:start]
                if len(self.buf) > self.max_buffer:  # 壊れたデータで溜まり続けないように
                    self.buf.clear()
                break
            frames.append(bytes(self.buf[start : end + 2]))
            del self.buf[: end + 2]
        return frames


class Esp32Camera(Camera):
    """ESP32 の映像を受け取る。

    連続取得中（set_streaming(True)）は MJPEG を受信し続けて最新の 1 枚だけを持つ（遅れが溜まらない）。
    それ以外は read() のたびに静止画を 1 枚取りに行く（待機中の通信と ESP32 の発熱を減らす）。
    JPEG の展開は read() されたときにだけ行う。
    """

    def __init__(self, cfg: CameraConfig, esp32: Esp32Config, timeout: float = 5.0):
        if not esp32.host:
            raise ValueError("config.toml の [esp32] host に ESP32 の IP アドレスを書いてください")
        self.cfg = cfg
        self.size = (cfg.width, cfg.height)
        self.timeout = timeout
        self.stream_url = f"http://{esp32.host}:{esp32.stream_port}/stream"
        self.capture_url = f"http://{esp32.host}:{esp32.http_port}/capture"
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._jpeg_t = 0.0
        self._jpeg_id = 0
        self._read_id = 0
        self._streaming = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self.set_streaming(True)

    # --- 連続取得 ---

    def set_streaming(self, on: bool) -> None:
        with self._cond:
            if on == self._streaming:
                return
            self._streaming = on
            if on:
                self._read_id = self._jpeg_id  # 古い 1 枚を返さないよう、次に届く 1 枚を待つ
                if self._thread is None:
                    self._thread = threading.Thread(target=self._stream_loop, name="esp32-stream", daemon=True)
                    self._thread.start()

    def _stream_loop(self) -> None:
        while True:
            with self._cond:
                if self._closed or not self._streaming:
                    self._thread = None  # 終了の判断と、次の set_streaming(True) を同じロックで扱う
                    return
            try:
                with urllib.request.urlopen(self.stream_url, timeout=self.timeout) as r:
                    parser = MjpegParser()
                    while not self._closed and self._streaming:
                        chunk = r.read1(65536)
                        if not chunk:
                            break
                        for jpeg in parser.feed(chunk):
                            self._put(jpeg)
            except OSError as e:
                if not self._closed and self._streaming:
                    log.warning("ESP32 の映像を受信できません: %s（2 秒後に再接続）", e)
                    time.sleep(2.0)

    def _put(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._jpeg_t = time.monotonic()
            self._jpeg_id += 1
            self._cond.notify_all()

    # --- 取得 ---

    def read_with_time(self) -> tuple[np.ndarray, float]:
        if self._streaming:
            with self._cond:
                ok = self._cond.wait_for(lambda: self._jpeg_id != self._read_id or self._closed, timeout=self.timeout)
                if not ok or self._jpeg is None:
                    raise RuntimeError(f"ESP32 から映像が届きません: {self.stream_url}")
                jpeg, t = self._jpeg, self._jpeg_t
                self._read_id = self._jpeg_id
        else:
            with urllib.request.urlopen(self.capture_url, timeout=self.timeout) as r:
                jpeg = r.read()
            t = time.monotonic()
        return self._decode(jpeg), t

    def read(self) -> np.ndarray:
        return self.read_with_time()[0]

    def _decode(self, jpeg: bytes) -> np.ndarray:
        import cv2

        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("ESP32 の画像を展開できません")
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            frame = cv2.resize(frame, self.size)
        if self.cfg.hflip or self.cfg.vflip:
            code = -1 if (self.cfg.hflip and self.cfg.vflip) else (1 if self.cfg.hflip else 0)
            frame = cv2.flip(frame, code)
        return frame

    def close(self) -> None:
        self._closed = True
        with self._cond:
            self._streaming = False
            self._cond.notify_all()
