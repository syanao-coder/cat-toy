"""YOLO（ONNX 形式）による猫・人の検出。

Ultralytics の YOLOv8 / YOLO11 を ONNX に書き出したモデルを onnxruntime で動かす。
COCO データセットで学習済みのモデルがそのまま使える（cat = 15, person = 0）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .tracker import Detection

COCO_LABELS = {0: "person", 15: "cat"}


def letterbox(img: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """縦横比を保って size×size に縮小し、余白をグレーで埋める。"""
    import cv2

    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    out = np.full((size, size, 3), 114, dtype=np.uint8)
    px, py = (size - nw) // 2, (size - nh) // 2
    out[py : py + nh, px : px + nw] = resized
    return out, scale, (px, py)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


def decode_yolo(
    output: np.ndarray,
    conf_threshold: float,
    iou_threshold: float,
    scale: float,
    pad: tuple[int, int],
    labels: dict[int, str] = COCO_LABELS,
) -> list[Detection]:
    """YOLOv8/11 の出力 (1, 4+クラス数, N) を元画像座標の検出結果に変換する。"""
    pred = np.asarray(output)[0]
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T  # → (N, 4+クラス数)
    cls_scores = pred[:, 4:]
    cls = cls_scores.argmax(axis=1)
    conf = cls_scores[np.arange(len(cls)), cls]
    wanted = np.isin(cls, list(labels)) & (conf >= conf_threshold)
    if not wanted.any():
        return []
    xywh, cls, conf = pred[wanted, :4], cls[wanted], conf[wanted]
    boxes = np.empty_like(xywh)
    boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad[0]) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad[1]) / scale
    out: list[Detection] = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in nms(boxes[idx], conf[idx], iou_threshold):
            i = idx[k]
            out.append(Detection(labels[int(c)], float(conf[i]), tuple(float(v) for v in boxes[i])))
    return out


class YoloOnnxDetector:
    def __init__(self, model_path: str | Path, input_size: int = 320, conf_threshold: float = 0.35, iou_threshold: float = 0.45, threads: int = 4):
        import onnxruntime as ort

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"モデルファイルがありません: {model_path}\n"
                "README の「検出モデルの準備」に従って ONNX モデルを用意してください。"
            )
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        shape = inp.shape
        self.input_size = shape[2] if isinstance(shape[2], int) else input_size
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold

    def detect(self, bgr: np.ndarray) -> list[Detection]:
        img, scale, pad = letterbox(bgr, self.input_size)
        blob = np.ascontiguousarray(img[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        output = self.session.run(None, {self.input_name: blob})[0]
        return decode_yolo(output, self.conf_threshold, self.iou_threshold, scale, pad)
