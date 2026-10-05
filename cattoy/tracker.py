"""猫の追跡。検出結果（数 fps）から位置・速度を推定し、検出の合間は速度で外挿する。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .geometry import Point, dist


@dataclass
class Detection:
    label: str
    conf: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2（ピクセル）

    @property
    def center(self) -> Point:
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def size(self) -> float:
        x1, y1, x2, y2 = self.box
        return max(x2 - x1, y2 - y1)


@dataclass
class CatState:
    center: Point
    velocity: Point  # ピクセル/秒
    size: float  # 体長の目安（外接矩形の長辺, ピクセル）
    box: tuple[float, float, float, float]
    visible_for: float  # 途切れずに見えている秒数
    age: float  # 最後に検出してからの秒数

    @property
    def speed(self) -> float:
        return (self.velocity[0] ** 2 + self.velocity[1] ** 2) ** 0.5


class CatTracker:
    """1 匹を追う α-β フィルタ。複数匹いるときは直前の位置に近い個体を優先する。"""

    def __init__(
        self,
        lost_timeout_s: float = 4.0,
        alpha: float = 0.6,
        beta: float = 0.3,
        gate: float = 3.0,
        max_extrapolate_s: float = 0.5,
        gap_reset_s: float = 1.0,
    ):
        self.lost_timeout_s = lost_timeout_s
        self.alpha = alpha
        self.beta = beta
        self.gate = gate
        self.max_extrapolate_s = max_extrapolate_s
        self.gap_reset_s = gap_reset_s
        self._lock = threading.Lock()
        self._center: Point | None = None
        self._velocity: Point = (0.0, 0.0)
        self._size = 0.0
        self._wh = (0.0, 0.0)
        self._first_seen = 0.0
        self._last_seen = 0.0

    def update(self, t: float, detections: list[Detection]) -> None:
        with self._lock:
            if not detections:
                if self._center is not None and t - self._last_seen > self.lost_timeout_s:
                    self._center = None
                return
            have_track = self._center is not None and t - self._last_seen <= self.lost_timeout_s
            if have_track:
                det = min(detections, key=lambda d: dist(d.center, self._predict(t)))
            else:
                det = max(detections, key=lambda d: d.conf)
            c = det.center
            x1, y1, x2, y2 = det.box
            wh = (x2 - x1, y2 - y1)
            if have_track:
                dt = max(t - self._last_seen, 1e-3)
                pred = self._predict(t)
                if dist(c, pred) > self.gate * max(self._size, det.size):
                    have_track = False  # 別の個体か誤検出に乗り換え: 作り直す
            if not have_track:
                self._center = c
                self._velocity = (0.0, 0.0)
                self._size = det.size
                self._wh = wh
                self._first_seen = t
            else:
                rx, ry = c[0] - pred[0], c[1] - pred[1]
                self._center = (pred[0] + self.alpha * rx, pred[1] + self.alpha * ry)
                vx, vy = self._velocity
                self._velocity = (vx + self.beta * rx / dt, vy + self.beta * ry / dt)
                self._size += 0.3 * (det.size - self._size)
                self._wh = (self._wh[0] + 0.3 * (wh[0] - self._wh[0]), self._wh[1] + 0.3 * (wh[1] - self._wh[1]))
                if t - self._last_seen > self.gap_reset_s:
                    self._first_seen = t
            self._last_seen = t

    def _predict(self, t: float) -> Point:
        assert self._center is not None
        dt = min(max(t - self._last_seen, 0.0), self.max_extrapolate_s)
        return (self._center[0] + self._velocity[0] * dt, self._center[1] + self._velocity[1] * dt)

    def state_at(self, t: float) -> CatState | None:
        with self._lock:
            if self._center is None:
                return None
            age = t - self._last_seen
            if age > self.lost_timeout_s:
                return None
            c = self._predict(t)
            hw, hh = self._wh[0] / 2, self._wh[1] / 2
            return CatState(
                center=c,
                # 長く見えていない猫は止まっているものとして扱う
                velocity=self._velocity if age <= self.max_extrapolate_s else (0.0, 0.0),
                size=self._size,
                box=(c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh),
                visible_for=self._last_seen - self._first_seen,
                age=max(age, 0.0),
            )
