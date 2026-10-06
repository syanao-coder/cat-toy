"""設定ファイル（TOML）の読み込み。

すべての項目に既定値があり、config.toml には変更したい項目だけを書けばよい。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger(__name__)

@dataclass
class Esp32Config:
    """カメラ付き ESP32（firmware/cattoy_esp32）との接続。"""

    host: str = ""  # ESP32 の IP アドレス（ルーターで固定しておく）
    http_port: int = 80  # 静止画・状態
    stream_port: int = 81  # 映像（MJPEG）
    udp_port: int = 4210  # サーボ・レーザーの指令
    key: str = ""  # ファームウェアの CATTOY_KEY と同じ文字列（空なら照合しない）


@dataclass
class CameraConfig:
    backend: str = "esp32"  # "esp32" | "picamera2" | "opencv"
    device: str | int = 0  # opencv のときのデバイス番号 or 動画ファイル
    width: int = 640
    height: int = 480
    hflip: bool = False
    vflip: bool = False


@dataclass
class DetectorConfig:
    model_path: str = "models/yolo11n.onnx"
    input_size: int = 320
    conf_threshold: float = 0.35
    iou_threshold: float = 0.45
    threads: int = 4
    device: str = "auto"  # "auto"（GPU があれば GPU）| "cuda" | "cpu"


@dataclass
class ServoConfig:
    backend: str = "esp32"  # "esp32" | "pca9685" | "gpio" | "mock"
    pan_channel: int = 0  # PCA9685 のチャンネル
    tilt_channel: int = 1
    pan_gpio: int = 12  # backend="gpio" のときの GPIO 番号 (BCM)
    tilt_gpio: int = 13
    pan_min: float = 30.0  # 可動範囲（度）。ハードウェアテストで確認して狭める
    pan_max: float = 150.0
    tilt_min: float = 30.0
    tilt_max: float = 150.0
    pan_home: float = 90.0
    tilt_home: float = 90.0
    min_pulse_us: int = 500
    max_pulse_us: int = 2500
    actuation_range: float = 180.0
    release_after_s: float = 3.0  # レーザー消灯が続いたらサーボの保持を解除（ジッタ・発熱対策）


@dataclass
class LaserConfig:
    backend: str = "esp32"  # "esp32" | "gpio" | "mock"
    gpio: int = 17  # backend="gpio" のときの GPIO 番号 (BCM)
    active_high: bool = True
    max_on_s: float = 900.0  # 連続点灯の上限（安全装置）


@dataclass
class CalibrationConfig:
    path: str = "calibration.json"
    grid: int = 9  # パン・チルトそれぞれの格子点数（9×9=81 点で約 2 分）
    settle_s: float = 0.5  # サーボ移動後の待ち時間
    poly_degree: int = 5  # 点が少ないときは自動で下げる
    min_dot_intensity: int = 60  # レーザー点灯・消灯の差分の最小値（0〜765）


@dataclass
class PlayConfig:
    # --- セッション ---
    session_max_s: float = 600.0  # 1回の遊びの最大時間
    cooldown_s: float = 1800.0  # 遊んだ後の休憩時間
    start_delay_s: float = 1.5  # 猫がこの秒数見え続けたら開始（誤検出対策）
    lost_timeout_s: float = 4.0  # 猫を見失ってこの秒数でレーザー停止
    reset_after_s: float = 300.0  # 猫がこの秒数いなければ遊んだ時間をリセット
    active_hours: str = "07:00-23:00"  # 動作する時間帯。空文字なら常時
    person_safety: bool = True  # 人を検出している間はレーザー消灯

    # --- 距離（猫の体長を 1 とした倍率）---
    keepout_margin: float = 0.35  # 猫の外接矩形をこれだけ広げた範囲にはレーザーを当てない
    lead_distance: float = 1.6  # 走っている猫の前方どれだけ先に置くか
    lure_distance: float = 2.0  # 止まっている猫の前方どれだけ先で誘うか
    dart_distance: float = 3.0  # 素早く逃げるときの距離
    wiggle_amplitude: float = 0.25  # その場でちょろちょろ動く振幅

    # --- 速さ（体長/秒）---
    creep_speed: float = 0.8
    move_speed: float = 2.5
    dart_speed: float = 7.0
    chase_threshold: float = 0.8  # 猫がこれ以上の速さなら「追いかけ中」とみなす
    flee_probability: float = 0.7  # 猫が飛びかかってきたとき逃げる確率（残りはわざと捕まえさせる）

    # --- 領域（画像サイズで正規化した 0〜1 の座標）---
    play_area: list[list[float]] = field(default_factory=list)  # 空ならキャリブレーション範囲から自動
    finish_point: list[float] = field(default_factory=list)  # 終了時にここへ誘導（おもちゃ・おやつの場所など）


@dataclass
class RuntimeConfig:
    control_hz: float = 50.0
    web_port: int = 8080  # 操作画面（スマホ用）のポート。0 なら無効
    standby_interval_s: float = 1.0  # 猫がいない間は、この間隔でだけ認識する（負荷を下げる）
    state_path: str = "state.json"  # ON/OFF と遊んだ記録の保存先
    settings_path: str = "settings.json"  # 操作画面で変えた設定（サーボの可動範囲など）の保存先


@dataclass
class Config:
    esp32: Esp32Config = field(default_factory=Esp32Config)
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    servo: ServoConfig = field(default_factory=ServoConfig)
    laser: LaserConfig = field(default_factory=LaserConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    play: PlayConfig = field(default_factory=PlayConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    base_dir: Path = field(default_factory=Path.cwd)

    def resolve(self, path: str) -> Path:
        """設定ファイルからの相対パスを解決する。"""
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.base_dir / p


_SECTIONS: dict[str, type] = {
    "esp32": Esp32Config,
    "camera": CameraConfig,
    "detector": DetectorConfig,
    "servo": ServoConfig,
    "laser": LaserConfig,
    "calibration": CalibrationConfig,
    "play": PlayConfig,
    "runtime": RuntimeConfig,
}


def _fill(cls: type, data: dict[str, Any], section: str) -> Any:
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"[{section}] に不明な設定項目があります: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _SECTIONS:
            raise ValueError(f"不明なセクションです: [{key}]")
        kwargs[key] = _fill(_SECTIONS[key], value, key)
    cfg = Config(**kwargs, base_dir=path.resolve().parent)
    parse_active_hours(cfg.play.active_hours)  # 書式チェック
    return cfg


def _coerce(value: str, current: Any, name: str) -> Any:
    try:
        if isinstance(current, bool):
            v = value.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
            raise ValueError(value)
        if isinstance(current, int):
            return int(value)
        if isinstance(current, float):
            return float(value)
        if isinstance(current, list):
            return json.loads(value)
    except ValueError as e:
        raise ValueError(f"環境変数 {name} の値が不正です: {value!r}") from e
    return value


def apply_env(cfg: Config, environ: Mapping[str, str]) -> list[str]:
    """環境変数 CATTOY_<セクション>_<項目>（例: CATTOY_ESP32_HOST）で設定を上書きする。

    コンテナ（QNAP Container Station のアプリケーションの YAML など）から設定ファイルなしで動かすため。
    """
    applied = []
    for section, cls in _SECTIONS.items():
        obj = getattr(cfg, section)
        for f in dataclasses.fields(cls):
            name = f"CATTOY_{section}_{f.name}".upper()
            if name in environ:
                current = getattr(obj, f.name)
                if section == "camera" and f.name == "device":  # 数字ならデバイス番号、それ以外はファイル名・URL
                    value: Any = int(environ[name]) if environ[name].isdigit() else environ[name]
                else:
                    value = _coerce(environ[name], current, name)
                setattr(obj, f.name, value)
                applied.append(name)
    return applied


# 操作画面から変えてよい設定（セクション → 項目）
UI_EDITABLE = {"servo": ("pan_min", "pan_max", "tilt_min", "tilt_max", "pan_home", "tilt_home")}


def apply_ui_settings(cfg: Config) -> None:
    """操作画面で保存した設定（settings.json）を反映する。設定ファイル・環境変数より優先する。"""
    path = cfg.resolve(cfg.runtime.settings_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning("%s を読めません（無視します）: %s", path, e)
        return
    for section, values in data.items():
        for key, value in values.items():
            if key in UI_EDITABLE.get(section, ()):
                setattr(getattr(cfg, section), key, type(getattr(getattr(cfg, section), key))(value))


def save_ui_settings(cfg: Config, section: str, values: Mapping[str, Any]) -> None:
    """操作画面で変えた設定を settings.json に保存し、cfg にも反映する。"""
    allowed = UI_EDITABLE.get(section, ())
    bad = set(values) - set(allowed)
    if bad:
        raise ValueError(f"操作画面からは変えられない項目です: {', '.join(sorted(bad))}")
    path = cfg.resolve(cfg.runtime.settings_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    obj = getattr(cfg, section)
    for key, value in values.items():
        value = type(getattr(obj, key))(value)
        setattr(obj, key, value)
        data.setdefault(section, {})[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_full_config(path: str | Path, environ: Mapping[str, str] | None = None) -> Config:
    """設定ファイル（なければ既定値）→ 環境変数 → 操作画面の設定 の順に重ねて読み込む。"""
    path = Path(path)
    environ = os.environ if environ is None else environ
    if path.exists():
        cfg = load_config(path)
    else:
        cfg = Config(base_dir=path.resolve().parent)
    applied = apply_env(cfg, environ)
    if applied:
        log.info("環境変数で設定しました: %s", ", ".join(applied))
    apply_ui_settings(cfg)
    parse_active_hours(cfg.play.active_hours)
    return cfg


def parse_active_hours(spec: str) -> tuple[_dt.time, _dt.time] | None:
    """"07:00-23:00" 形式を解析する。空文字なら None（常時動作）。"""
    spec = spec.strip()
    if not spec:
        return None
    try:
        start_s, end_s = spec.split("-")
        start = _dt.time.fromisoformat(start_s.strip())
        end = _dt.time.fromisoformat(end_s.strip())
    except ValueError as e:
        raise ValueError(f"active_hours の書式が不正です（例: \"07:00-23:00\"）: {spec!r}") from e
    return start, end


def is_active_time(spec: str, now: _dt.time) -> bool:
    hours = parse_active_hours(spec)
    if hours is None:
        return True
    start, end = hours
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # 日をまたぐ指定（例: 22:00-02:00）
