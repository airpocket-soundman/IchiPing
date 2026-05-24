"""TFLite INT8 モデルを 3 環境で評価 (PINTO onnx2tf 出力の精度確認用)。

INT8 TFLite は input/output が int8 量子化されているので、PyTorch/ONNX FP32 と
直接比較するときは scale/zero_point で復元してから argmax を取る。

Usage:
    cd pc
    uv run python eval_tflite_int8.py \\
        --tflite runs/neutron_v1234_32cls_ambient_XL/deploy4d/pinto_final/model_fp32_4d_full_integer_quant.tflite \\
        --eval-sets captures/eval_quiet captures/eval_noise_low captures/eval_noise_high
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from dataset import IchiPingDataset, CLASS_ORDER_14, class_of
from model_32cls_neutron import idx_to_bits


def load_interpreter(tflite_path: Path):
    """tf.lite or ai_edge_litert どちらでも動くように選択。"""
    try:
        from ai_edge_litert.interpreter import Interpreter  # type: ignore
        return Interpreter(model_path=str(tflite_path))
    except ImportError:
        import tensorflow as tf  # type: ignore
        return tf.lite.Interpreter(model_path=str(tflite_path))


def eval_one(tflite_path: Path, captures: Path, baseline_override: Path) -> dict:
    """TFLite INT8 を 1 captures dir に対して評価。"""
    interp = load_interpreter(tflite_path)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    in_scale, in_zp = inp["quantization"]
    out_scale, out_zp = out["quantization"]
    # 入力 shape は NHWC: (1, 1, 1024, 1)
    in_shape = list(inp["shape"])
    print(f"  input: {inp['name']} shape={in_shape} dtype={inp['dtype']} "
          f"q=(scale={in_scale:.6f}, zp={in_zp})")

    ds = IchiPingDataset(captures_dirs=[captures], feature_mode="noise_diff",
                         baseline_override_dir=baseline_override)
    cls_idx = {c: i for i, c in enumerate(CLASS_ORDER_14)}
    correct32 = 0; correct14 = 0; total = 0
    for i in range(len(ds)):
        item = ds[i]
        x = item["x"].numpy()           # (1, 1024) fp32
        if x.ndim == 2:
            x = x[None, :, :]           # (1, 1, 1024)
        # NCHW (1,1,1,1024) → NHWC (1,1,1024,1)
        x_nhwc = x[:, None, :, :].transpose(0, 2, 3, 1).astype(np.float32)
        # 量子化: q = round(x/scale) + zp
        x_int8 = np.round(x_nhwc / in_scale).astype(np.int32) + int(in_zp)
        x_int8 = np.clip(x_int8, -128, 127).astype(inp["dtype"])
        interp.set_tensor(inp["index"], x_int8)
        interp.invoke()
        y_int8 = interp.get_tensor(out["index"])  # NHWC (1,1,1,32)
        # 量子化解除して fp32 logits 化
        y_fp = (y_int8.astype(np.float32) - int(out_zp)) * out_scale
        # argmax は scale > 0 なので量子化空間でも同じ
        pred32 = int(y_int8.reshape(-1).argmax())
        true32 = int(item["state_idx"].item())
        if pred32 == true32:
            correct32 += 1
        true_bits = np.array(idx_to_bits(true32), dtype=np.int64)
        pred_bits = np.array(idx_to_bits(pred32), dtype=np.int64)
        if cls_idx[class_of(true_bits)] == cls_idx[class_of(pred_bits)]:
            correct14 += 1
        total += 1
    return {"acc_32cls": correct32 / total, "acc_14cls": correct14 / total, "n": total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tflite", type=Path, required=True)
    ap.add_argument("--eval-sets", type=Path, nargs="+", required=True,
                    help="各 dir の s00000 を baseline として self-eval")
    ap.add_argument("--out", type=Path, default=None,
                    help="summary.json 出力先 (デフォルト: tflite と同じ dir)")
    args = ap.parse_args()

    if args.out is None:
        args.out = args.tflite.parent / "tflite_int8_eval.json"

    print(f"TFLite: {args.tflite} ({args.tflite.stat().st_size/1024:.1f} KB)")
    results = {}
    for eval_dir in args.eval_sets:
        print(f"\n  {eval_dir.name}:")
        r = eval_one(args.tflite, eval_dir, eval_dir)
        print(f"    INT8 TFLite: 14cls={r['acc_14cls']:.4f}, "
              f"32cls={r['acc_32cls']:.4f}  (n={r['n']})")
        results[eval_dir.name] = r

    args.out.write_text(json.dumps({
        "tflite": str(args.tflite),
        "tflite_kb": args.tflite.stat().st_size / 1024,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  summary -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
