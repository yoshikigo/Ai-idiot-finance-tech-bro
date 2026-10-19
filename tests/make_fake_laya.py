"""Build a tiny stand-in Laya ONNX bundle (same inputs/outputs as receptron/laya-onnx) for offline tests.

    python tests/make_fake_laya.py <out_dir> <laya repo checkout>

The graph is NOT the model: logits are a deterministic function of the tokens and marker positions,
so the real @receptron/laya code path (tokenizer, sequence layout, calibration, answer shapes) runs
end to end without the 1.7 GB weights. Needs `pip install onnx numpy`.
"""
import json
import shutil
import sys
from pathlib import Path

import onnx
from onnx import TensorProto, helper


def build(out: Path, repo: Path):
    out.mkdir(parents=True, exist_ok=True)
    f = TensorProto.FLOAT
    c = lambda name, v: helper.make_node("Constant", [], [name], value=helper.make_tensor(name + "_t", f, [], [v]))  # noqa: E731
    axes1 = helper.make_node("Constant", [], ["ax1"], value=helper.make_tensor("ax1_t", TensorProto.INT64, [1], [1]))
    nodes = [
        axes1, c("k1", 0.0007), c("k2", 0.013), c("k3", 3.0), c("neg", -1e4),
        helper.make_node("Constant", [], ["s0"], value=helper.make_tensor("s0_t", TensorProto.INT64, [1], [0])),
        helper.make_node("Constant", [], ["s2"], value=helper.make_tensor("s2_t", TensorProto.INT64, [1], [2])),
        helper.make_node("Cast", ["input_ids"], ["ids_f"], to=f),
        helper.make_node("Cast", ["attention_mask"], ["am_f"], to=f),
        helper.make_node("Mul", ["ids_f", "am_f"], ["ids_m"]),
        helper.make_node("ReduceSum", ["ids_m", "ax1"], ["s"], keepdims=1),
        helper.make_node("ReduceSum", ["am_f", "ax1"], ["n"], keepdims=1),
        helper.make_node("Div", ["s", "n"], ["mean"]),
        helper.make_node("Cast", ["marker_pos"], ["mp"], to=f),
        helper.make_node("Mul", ["mp", "mean"], ["a"]),
        helper.make_node("Mul", ["a", "k1"], ["a1"]),
        helper.make_node("Mul", ["mp", "k2"], ["b1"]),
        helper.make_node("Add", ["a1", "b1"], ["z"]),
        helper.make_node("Sin", ["z"], ["sz"]),
        helper.make_node("Mul", ["sz", "k3"], ["raw"]),
        helper.make_node("Where", ["marker_mask", "raw", "neg"], ["logits"]),
        helper.make_node("Slice", ["logits", "s0", "s2", "ax1"], ["top2"]),
        helper.make_node("Softmax", ["top2"], ["act_probs"], axis=1),
    ]
    g = helper.make_graph(
        nodes, "fake_laya",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["B", "L"]),
         helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["B", "L"]),
         helper.make_tensor_value_info("marker_pos", TensorProto.INT64, ["B", "K"]),
         helper.make_tensor_value_info("marker_mask", TensorProto.BOOL, ["B", "K"]),
         helper.make_tensor_value_info("qtype", TensorProto.INT64, ["B"])],
        [helper.make_tensor_value_info("logits", f, ["B", "K"]),
         helper.make_tensor_value_info("act_probs", f, ["B", 2])])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.checker.check_model(m)
    onnx.save(m, str(out / "laya.onnx"))
    (out / "laya.onnx.data").write_bytes(b"")
    shutil.copy(repo / "onnx/laya_config.json", out / "laya_config.json")
    shutil.copytree(repo / "onnx/tokenizer", out / "tokenizer", dirs_exist_ok=True)
    (out / "FAKE").write_text(json.dumps({"note": "test stand-in, not the Laya model"}))


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
    print("ok")
