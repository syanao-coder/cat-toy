import math
import random

from cattoy.behavior import Mode, Move, PlayBehavior
from cattoy.config import PlayConfig
from cattoy.geometry import dist, move_towards, point_in_polygon
from cattoy.tracker import CatState

W, H = 640, 480
AREA = [(40.0, 60.0), (600.0, 60.0), (620.0, 460.0), (20.0, 460.0)]
CAT = 60.0  # 体長（ピクセル）
DT = 0.02


def cat_at(c, v=(0.0, 0.0), visible_for=10.0):
    hw, hh = CAT / 2, CAT / 3
    return CatState(center=c, velocity=v, size=CAT, box=(c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh), visible_for=visible_for, age=0.0)


def in_keepout(p, cat, margin):
    m = margin * CAT
    x1, y1, x2, y2 = cat.box
    return x1 - m <= p[0] <= x2 + m and y1 - m <= p[1] <= y2 + m


def simulate(cfg, seconds, seed=0, person=lambda t: False, active=lambda t: True, present=lambda t: True):
    """レーザー点を追いかける猫のシミュレーション。各ステップの (時刻, 猫, 指令) を返す。"""
    rng = random.Random(seed)
    b = PlayBehavior(cfg, AREA, (W, H), rng=random.Random(seed))
    c = (320.0, 260.0)
    v = (0.0, 0.0)
    log = []
    t = 0.0
    while t < seconds:
        cat = cat_at(c, v) if present(t) else None
        cmd = b.step(t, cat, person(t), active(t))
        log.append((t, cat, cmd, b))
        # 猫: 点灯中の点を追う（ときどき休む）
        if cmd.laser_on and cmd.target is not None and rng.random() > 0.002:
            nc = move_towards(c, cmd.target, 4.0 * CAT * DT)
        else:
            nc = c
        v = ((nc[0] - c[0]) / DT, (nc[1] - c[1]) / DT)
        c = nc
        t += DT
    return log


def test_never_lights_near_cat_or_outside_area():
    cfg = PlayConfig(session_max_s=120, start_delay_s=0.5)
    for seed in range(5):
        log = simulate(cfg, 130, seed=seed)
        lit = [(cat, cmd) for _, cat, cmd, _ in log if cmd.laser_on]
        assert len(lit) > 1000  # ちゃんと遊んでいる
        for cat, cmd in lit:
            assert not in_keepout(cmd.target, cat, cfg.keepout_margin)
            assert point_in_polygon(cmd.target, AREA)


def test_uses_variety_of_moves():
    log = simulate(PlayConfig(session_max_s=300, start_delay_s=0.5), 300, seed=1)
    moves = {cmd.move for _, _, cmd, _ in log if cmd.mode is Mode.PLAY}
    assert {Move.LEAD, Move.LURE, Move.DART, Move.FREEZE, Move.HIDE} <= moves


def test_waits_start_delay():
    b = PlayBehavior(PlayConfig(start_delay_s=1.5), AREA, (W, H), rng=random.Random(0))
    assert b.step(0.0, cat_at((300, 300), visible_for=1.0)).mode is Mode.IDLE
    assert b.step(0.1, cat_at((300, 300), visible_for=1.6)).mode is Mode.PLAY


def test_session_limit_then_cooldown():
    cfg = PlayConfig(session_max_s=30, cooldown_s=60, start_delay_s=0.5)
    log = simulate(cfg, 120)
    start = next(t for t, _, cmd, _ in log if cmd.mode is Mode.PLAY)
    end = next(t for t, _, cmd, _ in log if cmd.mode is Mode.COOLDOWN)
    assert 29 <= end - start <= 31
    for t, _, cmd, _ in log:
        if end <= t < end + 59.9:
            assert cmd.mode is Mode.COOLDOWN and not cmd.laser_on
    assert any(cmd.mode is Mode.PLAY for t, _, cmd, _ in log if t > end + 61)


def test_finish_point_guides_then_cooldown():
    cfg = PlayConfig(session_max_s=20, start_delay_s=0.5)
    b = PlayBehavior(cfg, AREA, (W, H), finish_point=(500.0, 400.0), rng=random.Random(0))
    t, cmd = 0.0, None
    finish = []
    while t < 60:
        cmd = b.step(t, cat_at((150.0, 150.0)))
        if cmd.mode is Mode.FINISH and cmd.laser_on:
            finish.append(cmd.target)
        t += DT
    assert cmd.mode is Mode.COOLDOWN
    assert finish and dist(finish[-1], (500.0, 400.0)) < cfg.wiggle_amplitude * CAT * 1.5


def test_person_turns_laser_off():
    log = simulate(PlayConfig(start_delay_s=0.5), 60, person=lambda t: 20 < t < 40)
    for t, _, cmd, _ in log:
        if 20 < t < 40:
            assert not cmd.laser_on
    assert any(cmd.laser_on for t, _, cmd, _ in log if t > 41)


def test_inactive_hours_do_not_start():
    log = simulate(PlayConfig(start_delay_s=0.5), 30, active=lambda t: False)
    assert not any(cmd.laser_on for _, _, cmd, _ in log)


def test_cat_lost_stops():
    log = simulate(PlayConfig(start_delay_s=0.5), 40, present=lambda t: t < 20)
    for t, _, cmd, _ in log:
        if t >= 20:
            assert not cmd.laser_on and cmd.mode is Mode.IDLE


def test_lead_runs_ahead_of_running_cat():
    cfg = PlayConfig()
    b = PlayBehavior(cfg, AREA, (W, H), rng=random.Random(0))
    b.mode = Mode.PLAY
    b.dot = (260.0, 300.0)
    b.move, b.move_until = Move.LEAD, math.inf
    c = (150.0, 300.0)
    v = (2.0 * CAT, 0.0)
    t = 0.0
    for _ in range(100):
        cmd = b.step(t, cat_at(c, v))
        c = (c[0] + v[0] * DT, c[1])
        t += DT
    assert cmd.move is Move.LEAD and cmd.laser_on
    assert cmd.target[0] - c[0] > CAT  # 猫の前方（進行方向）にいる
