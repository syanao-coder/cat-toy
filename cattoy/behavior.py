"""遊び方（レーザー点の動かし方）を決めるステートマシン。

ハードウェアには依存せず、「今どこを照らすか / 点灯するか」だけを返す。

全体の状態 (Mode)
    IDLE      猫を待つ（消灯）
    PLAY      遊ぶ
    FINISH    終了の演出（finish_point へ誘導して消灯）
    COOLDOWN  休憩（消灯）

遊び中の動き (Move) ― 獲物（虫やネズミ）らしさを意識している
    LEAD      走っている猫の少し前方を逃げる
    LURE      止まっている猫の前方でちょろちょろ動いて誘う
    DART      素早く別の場所へ逃げる（到着したら FREEZE）
    FREEZE    ぴたっと止まる（飛びかかりを誘う）
    HIDE      一瞬消えて別の場所に現れる（物陰に隠れるイメージ）

安全のため、猫の外接矩形を広げた範囲（keep-out）と、人を検出している間は必ず消灯する。
猫がレーザー点に飛びついて keep-out に入ったら「捕まえた」とみなし、HIDE で別の場所へ逃がす。
飛びかかってくる猫からは flee_probability の確率で逃げ、残りはわざと捕まえさせる
（捕まえられないままだと猫がストレスを溜めるため）。
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from enum import Enum

from .config import PlayConfig
from .geometry import Point, centroid, clamp_to_polygon, dist, move_towards, point_in_polygon, unit
from .tracker import CatState

log = logging.getLogger(__name__)


class Mode(str, Enum):
    IDLE = "idle"
    PLAY = "play"
    FINISH = "finish"
    COOLDOWN = "cooldown"


class Move(str, Enum):
    LEAD = "lead"
    LURE = "lure"
    DART = "dart"
    FREEZE = "freeze"
    HIDE = "hide"


@dataclass
class Command:
    laser_on: bool
    target: Point | None  # 照準（画像ピクセル座標）。None ならサーボを動かさない
    mode: Mode
    move: Move | None = None
    reason: str = ""


# 各動きの継続時間（秒）の範囲
_DURATIONS: dict[Move, tuple[float, float]] = {
    Move.LEAD: (1.5, 4.0),
    Move.LURE: (2.0, 5.0),
    Move.DART: (1.5, 1.5),  # 到着しなかったときの打ち切り時間
    Move.FREEZE: (0.5, 2.0),
    Move.HIDE: (0.6, 2.0),
}

# 次の動きを選ぶ重み
_WEIGHTS_CHASING = {Move.LEAD: 0.65, Move.DART: 0.2, Move.FREEZE: 0.1, Move.HIDE: 0.05}
_WEIGHTS_STILL = {Move.LURE: 0.45, Move.DART: 0.3, Move.FREEZE: 0.15, Move.HIDE: 0.1}

_FINISH_WIGGLE_S = 3.0
_FINISH_TIMEOUT_S = 20.0


class PlayBehavior:
    def __init__(
        self,
        cfg: PlayConfig,
        area: list[Point],
        image_size: tuple[int, int],
        finish_point: Point | None = None,
        rng: random.Random | None = None,
        latency_s: float = 0.0,
    ):
        w, h = image_size
        if len(area) < 3:
            area = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
        self.cfg = cfg
        self.area = list(area)
        self.image_size = image_size
        self.finish_point = finish_point
        self.rng = rng or random.Random()
        self.latency_s = latency_s
        self._size_lo, self._size_hi = 0.02 * w, 0.5 * w

        self.mode = Mode.IDLE
        self.mode_since = 0.0
        self.move: Move | None = None
        self.move_until = 0.0
        self.anchor: Point | None = None
        self.dot: Point = centroid(self.area)
        self.heading: Point = (1.0, 0.0)
        self.size = 0.1 * w
        self.played_s = 0.0
        self.last_cat_seen = -math.inf
        self.cooldown_until = -math.inf
        self._finish_reached_at: float | None = None
        self._last_t: float | None = None
        self._phase = 0.0
        self._reacted = False

    # ------------------------------------------------------------------ 外部 API

    def step(self, now: float, cat: CatState | None, person: bool = False, active_time: bool = True) -> Command:
        cfg = self.cfg
        dt = 0.0 if self._last_t is None else min(max(now - self._last_t, 0.0), 0.2)
        self._last_t = now
        self._phase += dt

        if cat is not None:
            self.last_cat_seen = now
            self.size = min(max(cat.size, self._size_lo), self._size_hi)
            if cat.speed > 0.3 * self.size:
                self.heading = unit(cat.velocity)
        elif now - self.last_cat_seen > cfg.reset_after_s:
            self.played_s = 0.0

        if self.mode is Mode.COOLDOWN:
            if now < self.cooldown_until:
                return self._off("休憩中")
            self._set_mode(Mode.IDLE, now)

        if self.mode is Mode.IDLE:
            if cat is None:
                return self._off("猫を待っています")
            if cat.visible_for < cfg.start_delay_s:
                return self._off("猫を確認中")
            if not active_time:
                return self._off("動作時間外")
            if person and cfg.person_safety:
                return self._off("人がいるため待機")
            self._set_mode(Mode.PLAY, now)
            self._begin(Move.HIDE, now, cat, duration=0.6)  # 消灯したまま最初の位置へ移動

        if self.mode is Mode.PLAY:
            if cat is None:
                self._set_mode(Mode.IDLE, now)
                return self._off("猫を見失いました")
            self.played_s += dt
            if not active_time or self.played_s >= cfg.session_max_s:
                self._set_mode(Mode.FINISH, now)
            else:
                return self._safety(self._play(now, dt, cat), cat, person)

        # Mode.FINISH
        return self._safety(self._finish(now, dt, cat), cat, person)

    # ------------------------------------------------------------------ 内部

    def _off(self, reason: str) -> Command:
        return Command(False, None, self.mode, None, reason)

    def _set_mode(self, mode: Mode, now: float) -> None:
        if mode is not self.mode:
            log.info("mode: %s -> %s (遊んだ時間 %.0f 秒)", self.mode.value, mode.value, self.played_s)
        self.mode = mode
        self.mode_since = now
        self.move = None
        self._finish_reached_at = None

    def _enter_cooldown(self, now: float) -> Command:
        self._set_mode(Mode.COOLDOWN, now)
        self.cooldown_until = now + self.cfg.cooldown_s
        self.played_s = 0.0
        return self._off("セッション終了")

    def _in_keepout(self, p: Point, cat: CatState) -> bool:
        # 映像が遅れて届く分、猫の位置は先読み（外挿）しているが、その誤差を見込んで広げる
        m = self.cfg.keepout_margin * self.size + 0.5 * cat.speed * self.latency_s
        x1, y1, x2, y2 = cat.box
        return x1 - m <= p[0] <= x2 + m and y1 - m <= p[1] <= y2 + m

    def _safety(self, cmd: Command, cat: CatState | None, person: bool) -> Command:
        if not cmd.laser_on or cmd.target is None:
            return cmd
        if person and self.cfg.person_safety:
            cmd.laser_on, cmd.reason = False, "人を検出したため消灯"
        elif cat is not None and self._in_keepout(cmd.target, cat):
            cmd.laser_on, cmd.reason = False, "猫に近すぎるため消灯"
        return cmd

    def _pick_point(self, cat: CatState, distance: float, spread_deg: float = 70.0, min_from_dot: float = 0.0) -> Point:
        """猫の前方（向いている方向）を中心に、距離 distance（体長倍）の点を選ぶ。"""
        base = math.atan2(self.heading[1], self.heading[0])
        best: Point | None = None
        best_score = -math.inf
        for i in range(40):
            spread = math.radians(spread_deg) * (1 + i / 10)  # 見つからなければ徐々に広げる
            ang = base + self.rng.uniform(-spread, spread)
            r = distance * self.size * self.rng.uniform(0.8, 1.2)
            p = (cat.center[0] + r * math.cos(ang), cat.center[1] + r * math.sin(ang))
            q = clamp_to_polygon(p, self.area, inset=2.0)
            if self._in_keepout(q, cat):
                continue
            too_close = dist(q, self.dot) < min_from_dot
            if point_in_polygon(p, self.area) and not too_close:
                return q
            score = -dist(p, q) - (1e6 if too_close else 0.0)
            if score > best_score:
                best, best_score = q, score
        if best is not None:
            return best
        # どこも猫に近い（狭い領域いっぱいに猫がいる）: 猫から最も遠い頂点
        return max(self.area, key=lambda v: dist(v, cat.center))

    def _begin(self, move: Move, now: float, cat: CatState, duration: float | None = None) -> None:
        cfg = self.cfg
        lo, hi = _DURATIONS[move]
        self.move = move
        self.move_until = now + (duration if duration is not None else self.rng.uniform(lo, hi))
        self.anchor = None
        self._reacted = False
        if move is Move.LURE:
            self.anchor = self._pick_point(cat, cfg.lure_distance)
        elif move is Move.DART:
            self.anchor = self._pick_point(cat, cfg.dart_distance, spread_deg=150, min_from_dot=1.5 * self.size)
        elif move is Move.HIDE:
            self.anchor = self._pick_point(cat, cfg.lure_distance, spread_deg=120)

    def _next_move(self, now: float, cat: CatState) -> None:
        prev = self.move
        if prev is Move.DART:
            self._begin(Move.FREEZE, now, cat)
            return
        chasing = cat.speed > self.cfg.chase_threshold * self.size
        weights = dict(_WEIGHTS_CHASING if chasing else _WEIGHTS_STILL)
        if prev in (Move.FREEZE, Move.HIDE):
            weights.pop(prev, None)  # 止まる・隠れるを連続させない
        moves = list(weights)
        self._begin(self.rng.choices(moves, weights=[weights[m] for m in moves])[0], now, cat)

    def _pouncing(self, cat: CatState) -> bool:
        """猫がレーザー点に向かって近づいてきているか。"""
        to_dot = (self.dot[0] - cat.center[0], self.dot[1] - cat.center[1])
        u = unit(to_dot)
        approach = cat.velocity[0] * u[0] + cat.velocity[1] * u[1]
        return approach > self.cfg.chase_threshold * self.size and dist(self.dot, cat.center) < 2.5 * self.size

    def _play(self, now: float, dt: float, cat: CatState) -> Command:
        cfg, s = self.cfg, self.size
        if self.move is None or now >= self.move_until:
            self._next_move(now, cat)
        if self.move is not Move.HIDE and self._in_keepout(self.dot, cat):
            self._begin(Move.HIDE, now, cat, duration=self.rng.uniform(0.5, 1.2))  # 捕まった: 消えて別の場所へ
        elif self.move in (Move.LURE, Move.FREEZE) and not self._reacted and self._pouncing(cat):
            # 飛びかかってきた: たいていは逃げるが、ときどきわざと捕まえさせる（達成感のため）
            self._reacted = True
            if self.rng.random() < cfg.flee_probability:
                self._begin(self.rng.choice([Move.LEAD, Move.DART]), now, cat)

        move = self.move
        visible = True
        if move is Move.LEAD:
            d = unit(cat.velocity, self.heading)
            perp = (-d[1], d[0])
            z = cfg.wiggle_amplitude * s * math.sin(2 * math.pi * 1.2 * self._phase)  # 小さくジグザグ
            lead = cfg.lead_distance * s
            target = (cat.center[0] + d[0] * lead + perp[0] * z, cat.center[1] + d[1] * lead + perp[1] * z)
            speed = max(cfg.move_speed * s, cat.speed * 1.3)
        elif move is Move.LURE:
            assert self.anchor is not None
            a = cfg.wiggle_amplitude * s
            w = 2 * math.pi * 1.5 * self._phase
            target = (self.anchor[0] + a * math.sin(w), self.anchor[1] + a * math.sin(1.7 * w))
            speed = (cfg.creep_speed if dist(self.dot, self.anchor) > 2 * a else cfg.move_speed) * s
        elif move is Move.DART:
            assert self.anchor is not None
            target, speed = self.anchor, cfg.dart_speed * s
        elif move is Move.FREEZE:
            target, speed = self.dot, 0.0
        else:  # HIDE: 消灯中にサーボを次の位置へ動かしておく
            assert self.anchor is not None
            target, speed, visible = self.anchor, math.inf, False

        target = clamp_to_polygon(target, self.area)
        nxt = target if math.isinf(speed) else move_towards(self.dot, target, speed * dt)
        self.dot = clamp_to_polygon(nxt, self.area)
        if move is Move.DART and self.anchor is not None and dist(self.dot, self.anchor) < 1.0:
            self._begin(Move.FREEZE, now, cat)
        return Command(visible, self.dot, self.mode, move)

    def _finish(self, now: float, dt: float, cat: CatState | None) -> Command:
        if self.finish_point is None or cat is None or now - self.mode_since > _FINISH_TIMEOUT_S:
            return self._enter_cooldown(now)
        fp = clamp_to_polygon(self.finish_point, self.area)
        s = self.size
        if self._finish_reached_at is None:
            self.dot = move_towards(self.dot, fp, self.cfg.move_speed * s * dt)
            if dist(self.dot, fp) < 1.0:
                self._finish_reached_at = now
        elif now - self._finish_reached_at > _FINISH_WIGGLE_S:
            return self._enter_cooldown(now)
        else:
            a = self.cfg.wiggle_amplitude * s
            w = 2 * math.pi * 1.5 * self._phase
            self.dot = clamp_to_polygon((fp[0] + a * math.sin(w), fp[1] + a * math.sin(1.7 * w)), self.area)
        return Command(True, self.dot, self.mode, None, "終了の誘導中")
