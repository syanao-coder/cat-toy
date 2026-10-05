"""カメラ・サーボ（パン・チルト）・レーザーの制御。

実機用の実装と、PC での動作確認用のモック実装を持つ。
実機用ライブラリ（picamera2, adafruit_servokit, gpiozero）は使うときにだけ import する。
カメラ付き ESP32 をネットワーク越しに使う実装は esp32.py にある。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from .config import CameraConfig, Esp32Config, LaserConfig, ServoConfig

log = logging.getLogger(__name__)


# ====================================================================== カメラ


class Camera:
    size: tuple[int, int]

    def read(self) -> np.ndarray:
        """最新のフレーム（BGR）を返す。"""
        raise NotImplementedError

    def read_with_time(self) -> tuple[np.ndarray, float]:
        """フレームと、それを受け取った時刻（time.monotonic）を返す。"""
        frame = self.read()
        return frame, time.monotonic()

    def set_streaming(self, on: bool) -> None:
        """連続取得の ON/OFF（ネットワークカメラで通信と負荷を減らすため）。既定では何もしない。"""

    def read_fresh(self) -> np.ndarray:
        """バッファに溜まった古いフレームを捨てて、今の様子を写したフレームを返す。"""
        for _ in range(3):
            self.read()
        return self.read()

    def close(self) -> None:
        pass


class Picamera2Camera(Camera):
    def __init__(self, cfg: CameraConfig):
        from libcamera import Transform
        from picamera2 import Picamera2

        self.cam = Picamera2()
        config = self.cam.create_video_configuration(
            main={"size": (cfg.width, cfg.height), "format": "RGB888"},  # RGB888 は BGR の並び（OpenCV と同じ）
            transform=Transform(hflip=cfg.hflip, vflip=cfg.vflip),
        )
        self.cam.configure(config)
        self.cam.start()
        time.sleep(1.0)  # 自動露出が落ち着くまで
        self.size = (cfg.width, cfg.height)

    def read(self) -> np.ndarray:
        return self.cam.capture_array()

    def close(self) -> None:
        self.cam.stop()
        self.cam.close()


class OpenCVCamera(Camera):
    """USB カメラや動画ファイル用。"""

    def __init__(self, cfg: CameraConfig):
        import cv2

        self._cv2 = cv2
        device = int(cfg.device) if str(cfg.device).isdigit() else cfg.device
        self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            raise RuntimeError(f"カメラを開けません: {cfg.device}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        self.cfg = cfg
        self.is_file = isinstance(device, str) and Path(device).is_file()
        self.size = (cfg.width, cfg.height)

    def read(self) -> np.ndarray:
        cv2 = self._cv2
        ok, frame = self.cap.read()
        if not ok and self.is_file:  # 動画ファイルは繰り返し再生
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("カメラから画像を取得できません")
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            frame = cv2.resize(frame, self.size)
        if self.cfg.hflip or self.cfg.vflip:
            code = -1 if (self.cfg.hflip and self.cfg.vflip) else (1 if self.cfg.hflip else 0)
            frame = cv2.flip(frame, code)
        if self.is_file:
            time.sleep(1 / 30)
        return frame

    def close(self) -> None:
        self.cap.release()


def make_camera(cfg: CameraConfig, esp32: Esp32Config | None = None) -> Camera:
    if cfg.backend == "esp32":
        from .esp32 import Esp32Camera

        return Esp32Camera(cfg, esp32 or Esp32Config())
    if cfg.backend == "picamera2":
        return Picamera2Camera(cfg)
    if cfg.backend == "opencv":
        return OpenCVCamera(cfg)
    raise ValueError(f"camera.backend が不正です: {cfg.backend}")


# ====================================================================== サーボ


class PanTilt:
    def __init__(self, cfg: ServoConfig):
        self.cfg = cfg
        self.pan = cfg.pan_home
        self.tilt = cfg.tilt_home
        self.attached = False

    def clip(self, pan: float, tilt: float) -> tuple[float, float]:
        c = self.cfg
        return min(max(pan, c.pan_min), c.pan_max), min(max(tilt, c.tilt_min), c.tilt_max)

    def move(self, pan: float, tilt: float) -> None:
        pan, tilt = self.clip(pan, tilt)
        if self.attached and abs(pan - self.pan) < 0.05 and abs(tilt - self.tilt) < 0.05:
            return  # 変化が小さいときは送らない（バスの負荷とジッタ対策）
        self.pan, self.tilt = pan, tilt
        self._write(pan, tilt)
        self.attached = True

    def home(self) -> None:
        self.move(self.cfg.pan_home, self.cfg.tilt_home)

    def release(self) -> None:
        """パルスを止めて保持を解除する（静止中のジッタ音・発熱を防ぐ）。"""
        if self.attached:
            self._release()
            self.attached = False

    def close(self) -> None:
        self.release()

    def _write(self, pan: float, tilt: float) -> None:
        raise NotImplementedError

    def _release(self) -> None:
        pass


class Pca9685PanTilt(PanTilt):
    """PCA9685（I2C 接続の 16ch PWM ドライバ）。ハードウェア PWM なのでジッタが少なく推奨。"""

    def __init__(self, cfg: ServoConfig):
        super().__init__(cfg)
        from adafruit_servokit import ServoKit

        self.kit = ServoKit(channels=16)
        for ch in (cfg.pan_channel, cfg.tilt_channel):
            self.kit.servo[ch].set_pulse_width_range(cfg.min_pulse_us, cfg.max_pulse_us)
            self.kit.servo[ch].actuation_range = cfg.actuation_range

    def _write(self, pan: float, tilt: float) -> None:
        self.kit.servo[self.cfg.pan_channel].angle = pan
        self.kit.servo[self.cfg.tilt_channel].angle = tilt

    def _release(self) -> None:
        self.kit.servo[self.cfg.pan_channel].angle = None
        self.kit.servo[self.cfg.tilt_channel].angle = None


class GpioPanTilt(PanTilt):
    """Raspberry Pi の GPIO から直接駆動（追加部品なし。ソフトウェア PWM のため多少ジッタが出る）。"""

    def __init__(self, cfg: ServoConfig):
        super().__init__(cfg)
        from gpiozero import AngularServo

        kw = dict(
            min_angle=0,
            max_angle=cfg.actuation_range,
            min_pulse_width=cfg.min_pulse_us / 1e6,
            max_pulse_width=cfg.max_pulse_us / 1e6,
            initial_angle=None,
        )
        self.pan_servo = AngularServo(cfg.pan_gpio, **kw)
        self.tilt_servo = AngularServo(cfg.tilt_gpio, **kw)

    def _write(self, pan: float, tilt: float) -> None:
        self.pan_servo.angle = pan
        self.tilt_servo.angle = tilt

    def _release(self) -> None:
        self.pan_servo.detach()
        self.tilt_servo.detach()

    def close(self) -> None:
        super().close()
        self.pan_servo.close()
        self.tilt_servo.close()


class MockPanTilt(PanTilt):
    def _write(self, pan: float, tilt: float) -> None:
        log.debug("servo pan=%.1f tilt=%.1f", pan, tilt)


def make_pantilt(cfg: ServoConfig, esp32: Esp32Config | None = None) -> PanTilt:
    if cfg.backend == "esp32":
        from .esp32 import Esp32PanTilt

        return Esp32PanTilt(cfg, esp32 or Esp32Config())
    if cfg.backend == "pca9685":
        return Pca9685PanTilt(cfg)
    if cfg.backend == "gpio":
        return GpioPanTilt(cfg)
    if cfg.backend == "mock":
        return MockPanTilt(cfg)
    raise ValueError(f"servo.backend が不正です: {cfg.backend}")


# ====================================================================== レーザー


class Laser:
    def __init__(self, cfg: LaserConfig):
        self.cfg = cfg
        self.is_on = False
        self._on_since = 0.0
        self._blocked_until = 0.0

    def set(self, on: bool) -> None:
        now = time.monotonic()
        if on and self.is_on and now - self._on_since > self.cfg.max_on_s:
            log.warning("連続点灯が %.0f 秒を超えたため 60 秒間消灯します", self.cfg.max_on_s)
            self._blocked_until = now + 60.0
        if now < self._blocked_until:
            on = False
        if on == self.is_on:
            return
        if on:
            self._on_since = now
        self.is_on = on
        self._write(on)

    def on(self) -> None:
        self.set(True)

    def off(self) -> None:
        self.set(False)

    def close(self) -> None:
        self.off()

    def _write(self, on: bool) -> None:
        raise NotImplementedError


class GpioLaser(Laser):
    def __init__(self, cfg: LaserConfig):
        super().__init__(cfg)
        from gpiozero import OutputDevice

        self.dev = OutputDevice(cfg.gpio, active_high=cfg.active_high, initial_value=False)

    def _write(self, on: bool) -> None:
        self.dev.value = on

    def close(self) -> None:
        super().close()
        self.dev.close()


class MockLaser(Laser):
    def _write(self, on: bool) -> None:
        log.debug("laser %s", "ON" if on else "OFF")


def make_laser(cfg: LaserConfig, esp32: Esp32Config | None = None) -> Laser:
    if cfg.backend == "esp32":
        from .esp32 import Esp32Laser

        return Esp32Laser(cfg, esp32 or Esp32Config())
    if cfg.backend == "gpio":
        return GpioLaser(cfg)
    if cfg.backend == "mock":
        return MockLaser(cfg)
    raise ValueError(f"laser.backend が不正です: {cfg.backend}")
