"""ESP32 の遠隔メンテナンス（ファームウェアの書き換え・再起動・Wi-Fi 設定の変更）。

ESP32 を取り外さずに、NAS（またはこのリポジトリを入れた PC）からネットワーク越しに行う。
"""

from __future__ import annotations

import hashlib
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Esp32Config

ESP_IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
CHIP_ID_ESP32S3 = 9
MAX_APP_SIZE = 3 * 1024 * 1024  # パーティション「16M Flash (3MB APP/9.9MB FATFS)」の 1 枠の大きさ


def inspect_image(data: bytes) -> dict:
    """書き込もうとしているファイルが ESP32-S3 用のアプリのイメージか確かめ、版の情報を返す。

    Arduino IDE の「コンパイルしたバイナリを出力」で作られる `cattoy_esp32.ino.bin` を想定している。
    ブートローダーまで含んだ `merged.bin` や、別のチップ用のファイルは受け付けない。
    """
    if len(data) < 256 or data[0] != ESP_IMAGE_MAGIC:
        raise ValueError("ESP32 のファームウェアのファイルではありません（.ino.bin を指定してください）")
    if len(data) > MAX_APP_SIZE:
        raise ValueError(
            f"ファイルが大きすぎます（{len(data):,} バイト）。merged.bin ではなく cattoy_esp32.ino.bin を指定してください"
        )
    chip_id = struct.unpack_from("<H", data, 12)[0]
    if chip_id != CHIP_ID_ESP32S3:
        raise ValueError(f"ESP32-S3 用ではありません（chip id {chip_id}）。ボードの設定を ESP32S3 Dev Module にしてください")
    magic = struct.unpack_from("<I", data, 32)[0]
    if magic != APP_DESC_MAGIC:
        raise ValueError("アプリのイメージではありません（ブートローダーや merged.bin の可能性があります）")

    def text(offset: int, size: int) -> str:
        return data[offset : offset + size].split(b"\0", 1)[0].decode("ascii", "replace")

    return {
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
        "version": text(32 + 16, 32),
        "project": text(32 + 48, 32),
        "built": f"{text(32 + 96, 16)} {text(32 + 80, 16)}",
    }


def _post(cfg: Esp32Config, path: str, body: bytes, headers: dict[str, str], timeout: float) -> str:
    url = f"http://{cfg.host}:{cfg.http_port}{path}"
    req = urllib.request.Request(url, data=body, method="POST", headers={"X-Cattoy-Key": cfg.key, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ESP32 が拒否しました（{e.code}）: {e.read().decode('utf-8', 'replace')}") from e
    except OSError as e:
        raise RuntimeError(f"ESP32（{cfg.host}）に接続できません: {e}") from e


def upload_firmware(cfg: Esp32Config, data: bytes, timeout: float = 120.0) -> dict:
    """ファームウェアを送って書き換えさせる。ESP32 は受け取ったあと自分で再起動する。"""
    info = inspect_image(data)
    _post(
        cfg,
        "/update",
        data,
        {"Content-Type": "application/octet-stream", "X-Cattoy-MD5": info["md5"]},
        timeout,
    )
    return info


def reboot(cfg: Esp32Config) -> None:
    _post(cfg, "/reboot", b"", {}, 10.0)


def set_wifi(cfg: Esp32Config, ssid: str, password: str) -> str:
    body = urllib.parse.urlencode({"ssid": ssid, "pass": password}).encode()
    return _post(cfg, "/wifi", body, {"Content-Type": "application/x-www-form-urlencoded"}, 10.0)


def wait_until_back(status_fn, timeout: float = 90.0, interval: float = 2.0) -> dict | None:
    """再起動した ESP32 が戻ってくるまで待つ（起動直後で uptime が短い状態を確認する）。"""
    end = time.monotonic() + timeout
    time.sleep(3.0)
    while time.monotonic() < end:
        st = status_fn()
        if st is not None and int(st.get("uptime_s", 1e9)) < timeout + 10:
            return st
        time.sleep(interval)
    return None
