"""本物の onnxruntime で、YOLO と同じ形の出力を返す小さなモデルを動かす。"""

import numpy as np
import pytest

ort = pytest.importorskip("onnxruntime")
onnx = pytest.importorskip("onnx")
pytest.importorskip("cv2")

from onnx import TensorProto, helper, numpy_helper  # noqa: E402

from cattoy.detector import YoloOnnxDetector, choose_providers  # noqa: E402


def make_model(path):
    out = np.zeros((1, 84, 2100), np.float32)
    out[0, :4, 0] = (160, 160, 40, 20)  # 320x320 の入力上での猫（cx, cy, w, h）
    out[0, 4 + 15, 0] = 0.9
    const = numpy_helper.from_array(out, "C")
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["m"], keepdims=0),
        helper.make_node("Mul", ["m", "zero"], ["z"]),
        helper.make_node("Add", ["C", "z"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes,
        "fake_yolo",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 320, 320])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84, 2100])],
        [const, numpy_helper.from_array(np.array(0, np.float32), "zero")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, path)


def test_detector_runs_on_cpu(tmp_path):
    path = tmp_path / "fake.onnx"
    make_model(path)
    det = YoloOnnxDetector(path, device="auto")
    assert det.provider == "CPU"  # このテスト環境には GPU がない
    dets = det.detect(np.zeros((480, 640, 3), np.uint8))
    assert [d.label for d in dets] == ["cat"]
    assert dets[0].box == pytest.approx((280, 220, 360, 260))


def test_choose_providers():
    gpu = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert choose_providers(gpu, "auto")[0] == "CUDAExecutionProvider"
    assert choose_providers(gpu, "cpu") == ["CPUExecutionProvider"]
    assert choose_providers(["CPUExecutionProvider"], "cuda") == ["CPUExecutionProvider"]
    with pytest.raises(ValueError):
        choose_providers(gpu, "tpu")
