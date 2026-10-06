import numpy as np
import pytest

from cattoy.detector import decode_yolo


def make_output(rows, n=2100, nc=80):
    out = np.zeros((1, 4 + nc, n), dtype=np.float32)
    for i, (cx, cy, w, h, cls, conf) in enumerate(rows):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + cls, i] = conf
    return out


def test_decode_undoes_letterbox_and_filters_classes():
    # 640x480 → 320x320: scale 0.5, 上下に 40px の余白
    out = make_output([
        (160, 160, 40, 20, 15, 0.9),  # cat
        (161, 160, 40, 20, 15, 0.7),  # 同じ猫の重複 → NMS で消える
        (50, 100, 20, 60, 0, 0.8),  # person
        (250, 250, 30, 30, 16, 0.95),  # dog → 対象外
        (100, 250, 30, 30, 15, 0.2),  # 信頼度不足
    ])
    dets = decode_yolo(out, 0.35, 0.45, scale=0.5, pad=(0, 40))
    cats = [d for d in dets if d.label == "cat"]
    people = [d for d in dets if d.label == "person"]
    assert len(cats) == 1 and len(people) == 1 and len(dets) == 2
    assert cats[0].conf == pytest.approx(0.9)
    assert cats[0].box == pytest.approx((280, 220, 360, 260))


def test_decode_empty():
    assert decode_yolo(make_output([]), 0.35, 0.45, 1.0, (0, 0)) == []
