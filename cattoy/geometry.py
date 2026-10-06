"""2次元の幾何ユーティリティ（numpy / OpenCV に依存しない純 Python 実装）。

座標はすべてカメラ画像のピクセル座標 (x, y) を想定する。
"""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]
Polygon = Sequence[Point]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def norm(v: Point) -> float:
    return math.hypot(v[0], v[1])


def unit(v: Point, fallback: Point = (1.0, 0.0)) -> Point:
    n = norm(v)
    if n < 1e-9:
        return fallback
    return (v[0] / n, v[1] / n)


def move_towards(p: Point, target: Point, max_step: float) -> Point:
    """p から target へ最大 max_step だけ近づけた点を返す。"""
    d = dist(p, target)
    if d <= max_step or d < 1e-9:
        return target
    r = max_step / d
    return (p[0] + (target[0] - p[0]) * r, p[1] + (target[1] - p[1]) * r)


def point_in_polygon(p: Point, poly: Polygon) -> bool:
    """レイキャスティング法による内外判定（境界上は不定）。"""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _closest_on_segment(p: Point, a: Point, b: Point) -> Point:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return a
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2
    t = max(0.0, min(1.0, t))
    return (ax + t * dx, ay + t * dy)


def centroid(poly: Polygon) -> Point:
    n = len(poly)
    return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)


def clamp_to_polygon(p: Point, poly: Polygon, inset: float = 1.0) -> Point:
    """p が多角形の外なら、境界上の最近点を inset ピクセルだけ内側へ寄せて返す。"""
    if len(poly) < 3 or point_in_polygon(p, poly):
        return p
    best = None
    best_d = math.inf
    n = len(poly)
    for i in range(n):
        q = _closest_on_segment(p, poly[i], poly[(i + 1) % n])
        d = dist(p, q)
        if d < best_d:
            best, best_d = q, d
    assert best is not None
    c = centroid(poly)
    return move_towards(best, c, inset)


def convex_hull(points: Sequence[Point]) -> list[Point]:
    """Andrew の monotone chain 法。反時計回り（画像座標では時計回りに見える）で返す。"""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def shrink_polygon(poly: Polygon, ratio: float) -> list[Point]:
    """重心に向かって ratio（0〜1）だけ縮める。凸多角形向けの簡易版。"""
    c = centroid(poly)
    return [(c[0] + (x - c[0]) * (1 - ratio), c[1] + (y - c[1]) * (1 - ratio)) for x, y in poly]
