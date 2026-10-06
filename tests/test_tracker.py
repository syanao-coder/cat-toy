import pytest

from cattoy.tracker import CatTracker, Detection


def det(cx, cy, w=60, h=40, conf=0.8, label="cat"):
    return Detection(label, conf, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))


def test_estimates_velocity_and_extrapolates():
    tr = CatTracker()
    for k in range(30):  # 10 fps で右へ 100 px/s
        t = k * 0.1
        tr.update(t, [det(100 + 100 * t, 200)])
    st = tr.state_at(2.9)
    assert st.velocity[0] == pytest.approx(100, rel=0.1)
    assert abs(st.velocity[1]) < 5
    ahead = tr.state_at(3.1)
    assert ahead.center[0] == pytest.approx(st.center[0] + 20, abs=3)
    assert st.visible_for == pytest.approx(2.9)
    assert st.size == pytest.approx(60, rel=0.05)


def test_lost_after_timeout():
    tr = CatTracker(lost_timeout_s=2.0)
    tr.update(0.0, [det(100, 100)])
    assert tr.state_at(1.9) is not None
    assert tr.state_at(2.1) is None
    tr.update(2.5, [])
    assert tr.state_at(2.5) is None


def test_prefers_nearest_cat_when_two():
    tr = CatTracker()
    tr.update(0.0, [det(100, 100)])
    tr.update(0.1, [det(400, 300, conf=0.95), det(105, 100, conf=0.5)])
    assert tr.state_at(0.1).center[0] < 150


def test_jump_resets_track():
    tr = CatTracker()
    for k in range(5):
        tr.update(k * 0.1, [det(100, 100)])
    tr.update(0.5, [det(500, 400)])
    st = tr.state_at(0.5)
    assert st.center == pytest.approx((500, 400))
    assert st.visible_for == 0
