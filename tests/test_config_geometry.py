import datetime as dt
from pathlib import Path

import pytest

from cattoy.config import is_active_time, load_config
from cattoy.geometry import clamp_to_polygon, convex_hull, point_in_polygon

ROOT = Path(__file__).resolve().parent.parent


def test_example_config_loads():
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg.camera.width == 640
    assert cfg.base_dir == ROOT


def test_unknown_key_is_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[play]\nsesion_max_s = 10\n")
    with pytest.raises(ValueError, match="sesion_max_s"):
        load_config(p)


@pytest.mark.parametrize(
    "spec,time,expected",
    [
        ("07:00-23:00", "06:59", False),
        ("07:00-23:00", "12:00", True),
        ("07:00-23:00", "23:00", False),
        ("22:00-02:00", "23:30", True),
        ("22:00-02:00", "01:00", True),
        ("22:00-02:00", "12:00", False),
        ("", "03:00", True),
    ],
)
def test_active_hours(spec, time, expected):
    assert is_active_time(spec, dt.time.fromisoformat(time)) is expected


def test_polygon_helpers():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon((5, 5), sq)
    assert not point_in_polygon((15, 5), sq)
    q = clamp_to_polygon((15, 5), sq, inset=0.5)
    assert point_in_polygon(q, sq) and q[0] == pytest.approx(9.5, abs=0.1)
    hull = convex_hull([(0, 0), (10, 0), (5, 5), (10, 10), (0, 10), (3, 3)])
    assert sorted(hull) == sorted(sq)


def test_env_overrides(tmp_path):
    from cattoy.config import Config, apply_env

    cfg = Config(base_dir=tmp_path)
    env = {
        "CATTOY_ESP32_HOST": "192.168.1.77",
        "CATTOY_ESP32_KEY": "neko7Laser2026",
        "CATTOY_SERVO_PAN_MIN": "45",
        "CATTOY_PLAY_PERSON_SAFETY": "false",
        "CATTOY_PLAY_PLAY_AREA": "[[0, 0], [1, 0], [1, 1]]",
        "CATTOY_CAMERA_DEVICE": "/data/cat.mp4",
        "UNRELATED": "x",
    }
    applied = apply_env(cfg, env)
    assert len(applied) == 6
    assert cfg.esp32.host == "192.168.1.77" and cfg.esp32.key == "neko7Laser2026"
    assert cfg.servo.pan_min == 45.0 and isinstance(cfg.servo.pan_min, float)
    assert cfg.play.person_safety is False
    assert cfg.play.play_area == [[0, 0], [1, 0], [1, 1]]
    assert cfg.camera.device == "/data/cat.mp4"
    with pytest.raises(ValueError, match="CATTOY_RUNTIME_WEB_PORT"):
        apply_env(cfg, {"CATTOY_RUNTIME_WEB_PORT": "abc"})


def test_precedence_file_env_ui(tmp_path):
    from cattoy.config import load_full_config, save_ui_settings

    (tmp_path / "config.toml").write_text('[esp32]\nhost = "10.0.0.1"\n[servo]\npan_min = 30\ntilt_min = 30\n')
    env = {"CATTOY_ESP32_HOST": "10.0.0.2", "CATTOY_SERVO_PAN_MIN": "40"}
    cfg = load_full_config(tmp_path / "config.toml", env)
    assert cfg.esp32.host == "10.0.0.2" and cfg.servo.pan_min == 40  # 環境変数が設定ファイルより優先
    save_ui_settings(cfg, "servo", {"pan_min": 50})
    cfg = load_full_config(tmp_path / "config.toml", env)
    assert cfg.servo.pan_min == 50 and cfg.servo.tilt_min == 30  # 操作画面で保存した値が最優先
    with pytest.raises(ValueError):
        save_ui_settings(cfg, "esp32", {"host": "x"})  # 操作画面から変えてよい項目は限る
    # 設定ファイルがなくても動く（コンテナ用）
    cfg = load_full_config(tmp_path / "none" / "config.toml", {"CATTOY_ESP32_HOST": "10.0.0.3"})
    assert cfg.esp32.host == "10.0.0.3" and cfg.base_dir == tmp_path / "none"
