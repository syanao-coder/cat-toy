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
