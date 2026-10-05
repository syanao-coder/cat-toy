"""カメラ画像の座標 ⇔ サーボ角度 の対応付け（キャリブレーション）。

天井からカメラとレーザーで床を見下ろす場合、床（平面）上の点について
「画像のピクセル座標」と「その点を照らすパン・チルト角」は 1 対 1 に対応する。
この対応は tan / atan を含む非線形な関係なので、実測点から多項式で近似する。

カメラとレーザーの取り付け位置・向き・サーボの回転方向の違いは
すべて実測で吸収されるため、取り付けの精度は問わない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .geometry import Point, convex_hull, shrink_polygon


def _poly_terms(degree: int) -> list[tuple[int, int]]:
    return [(i, j) for total in range(degree + 1) for i in range(total + 1) for j in [total - i]]


def n_terms(degree: int) -> int:
    return (degree + 1) * (degree + 2) // 2


@dataclass
class PolyMap:
    """2 入力 → 2 出力の多項式写像。入力は内部で [-1, 1] 程度に正規化する。"""

    degree: int
    in_offset: list[float]
    in_scale: list[float]
    coef: list[list[float]]  # shape: (n_terms, 2)

    def _features(self, xy: np.ndarray) -> np.ndarray:
        x = (xy[:, 0] - self.in_offset[0]) / self.in_scale[0]
        y = (xy[:, 1] - self.in_offset[1]) / self.in_scale[1]
        return np.stack([x**i * y**j for i, j in _poly_terms(self.degree)], axis=1)

    @classmethod
    def fit(cls, src: np.ndarray, dst: np.ndarray, degree: int) -> "PolyMap":
        src = np.asarray(src, dtype=np.float64)
        dst = np.asarray(dst, dtype=np.float64)
        lo, hi = src.min(axis=0), src.max(axis=0)
        offset = (lo + hi) / 2
        scale = np.maximum((hi - lo) / 2, 1e-9)
        m = cls(degree, offset.tolist(), scale.tolist(), [])
        a = m._features(src)
        coef, *_ = np.linalg.lstsq(a, dst, rcond=None)
        m.coef = coef.tolist()
        return m

    def apply(self, xy: np.ndarray) -> np.ndarray:
        xy = np.atleast_2d(np.asarray(xy, dtype=np.float64))
        return self._features(xy) @ np.asarray(self.coef)

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        r = self.apply(np.array([[x, y]]))[0]
        return float(r[0]), float(r[1])

    def to_dict(self) -> dict:
        return {"degree": self.degree, "in_offset": self.in_offset, "in_scale": self.in_scale, "coef": self.coef}

    @classmethod
    def from_dict(cls, d: dict) -> "PolyMap":
        return cls(int(d["degree"]), list(d["in_offset"]), list(d["in_scale"]), [list(r) for r in d["coef"]])


@dataclass
class Calibration:
    image_size: tuple[int, int]
    pix_to_servo: PolyMap
    servo_to_pix: PolyMap
    points: list[tuple[float, float, float, float]] = field(default_factory=list)  # (x, y, pan, tilt)
    rms_px: float = 0.0
    rms_deg: float = 0.0

    def pixel_to_angles(self, x: float, y: float) -> tuple[float, float]:
        return self.pix_to_servo(x, y)

    def angles_to_pixel(self, pan: float, tilt: float) -> tuple[float, float]:
        return self.servo_to_pix(pan, tilt)

    def default_play_area(self, shrink: float = 0.08) -> list[Point]:
        """キャリブレーションで実際にレーザーが写った範囲（凸包）を少し縮めたもの。"""
        hull = convex_hull([(p[0], p[1]) for p in self.points])
        if len(hull) < 3:
            w, h = self.image_size
            return [(0, 0), (w, 0), (w, h), (0, h)]
        return shrink_polygon(hull, shrink)

    @classmethod
    def fit(
        cls,
        points: list[tuple[float, float, float, float]],
        image_size: tuple[int, int],
        degree: int = 5,
        outlier_factor: float = 4.0,
    ) -> "Calibration":
        """実測点から写像を求める。

        点が少なければ次数を自動で下げる。家具に当たった点などの外れ値は、
        誤差が最大の点を 1 つずつ除いて再計算する（最大で全体の 2 割まで）。
        """
        if len(points) < 4:
            raise ValueError(f"キャリブレーション点が少なすぎます（{len(points)} 点）。最低 4 点必要です。")
        while degree > 1 and len(points) < n_terms(degree) * 1.5:
            degree -= 1
        pts = np.asarray(points, dtype=np.float64)
        max_remove = len(pts) // 5
        while True:
            pix, ang = pts[:, :2], pts[:, 2:]
            p2s = PolyMap.fit(pix, ang, degree)
            s2p = PolyMap.fit(ang, pix, degree)
            err_px = np.linalg.norm(s2p.apply(ang) - pix, axis=1)
            err_deg = np.linalg.norm(p2s.apply(pix) - ang, axis=1)
            worst = int(err_px.argmax())
            limit = max(outlier_factor * float(np.median(err_px)), 3.0)
            if err_px[worst] <= limit or max_remove == 0 or len(pts) - 1 < n_terms(degree) * 1.5:
                break
            pts = np.delete(pts, worst, axis=0)
            max_remove -= 1
        return cls(
            image_size=(int(image_size[0]), int(image_size[1])),
            pix_to_servo=p2s,
            servo_to_pix=s2p,
            points=[tuple(map(float, p)) for p in pts],
            rms_px=float(np.sqrt(np.mean(err_px**2))),
            rms_deg=float(np.sqrt(np.mean(err_deg**2))),
        )

    @classmethod
    def linear_fallback(
        cls, image_size: tuple[int, int], pan_range: tuple[float, float], tilt_range: tuple[float, float]
    ) -> "Calibration":
        """実機がない動作確認用。画像の左右→パン、上下→チルトに線形に割り当てる。"""
        w, h = image_size
        pts = [
            (x * w, y * h, pan_range[0] + x * (pan_range[1] - pan_range[0]), tilt_range[0] + y * (tilt_range[1] - tilt_range[0]))
            for x in (0.0, 0.5, 1.0)
            for y in (0.0, 0.5, 1.0)
        ]
        return cls.fit(pts, image_size, degree=1)

    def save(self, path: str | Path) -> None:
        data = {
            "image_size": list(self.image_size),
            "pix_to_servo": self.pix_to_servo.to_dict(),
            "servo_to_pix": self.servo_to_pix.to_dict(),
            "points": [list(p) for p in self.points],
            "rms_px": self.rms_px,
            "rms_deg": self.rms_deg,
        }
        Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Calibration":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            image_size=(int(d["image_size"][0]), int(d["image_size"][1])),
            pix_to_servo=PolyMap.from_dict(d["pix_to_servo"]),
            servo_to_pix=PolyMap.from_dict(d["servo_to_pix"]),
            points=[tuple(p) for p in d.get("points", [])],
            rms_px=float(d.get("rms_px", 0.0)),
            rms_deg=float(d.get("rms_deg", 0.0)),
        )


def find_laser_dot(
    frame_off: np.ndarray,
    frame_on: np.ndarray,
    min_intensity: int = 60,
    max_area_ratio: float = 0.01,
    radius: int = 12,
) -> tuple[float, float] | None:
    """レーザー消灯時と点灯時の画像の差分から、レーザー点の位置（サブピクセル）を求める。

    見つからない・照明が変わった等で信頼できない場合は None。
    """
    if frame_off.shape != frame_on.shape:
        raise ValueError("画像サイズが一致しません")
    diff = frame_on.astype(np.int16) - frame_off.astype(np.int16)
    score = np.clip(diff, 0, None).astype(np.int32)
    if score.ndim == 3:
        # 赤いレーザーを想定して赤チャンネル（BGR の 2 番）を重く見る
        score = score[:, :, 0] + score[:, :, 1] + 2 * score[:, :, 2]
    peak = int(score.max())
    if peak < min_intensity:
        return None
    mask = score >= peak * 0.5
    if mask.sum() > max_area_ratio * mask.size:
        return None  # 画面全体の明るさが変わった（照明・露出の変化）
    py, px = np.unravel_index(int(score.argmax()), score.shape)
    y0, y1 = max(py - radius, 0), min(py + radius + 1, score.shape[0])
    x0, x1 = max(px - radius, 0), min(px + radius + 1, score.shape[1])
    win = np.where(mask[y0:y1, x0:x1], score[y0:y1, x0:x1], 0).astype(np.float64)
    total = win.sum()
    if total <= 0:
        return None
    ys, xs = np.mgrid[y0:y1, x0:x1]
    return float((xs * win).sum() / total), float((ys * win).sum() / total)


def grid_angles(pan_range: tuple[float, float], tilt_range: tuple[float, float], n: int) -> list[tuple[float, float]]:
    """往復（蛇行）順の格子点。サーボの移動量を小さくするため。"""
    pans = np.linspace(pan_range[0], pan_range[1], n)
    tilts = np.linspace(tilt_range[0], tilt_range[1], n)
    out = []
    for k, t in enumerate(tilts):
        row = pans if k % 2 == 0 else pans[::-1]
        out.extend((float(p), float(t)) for p in row)
    return out

