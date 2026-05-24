"""ベストモデル (v1234+ambient 32cls XL) を INT8 量子化して精度劣化を測定。

フロー:
  1. PyTorch ckpt → ONNX (FP32) export
  2. ONNX (FP32) → INT8 ONNX (onnxruntime PTQ, calibration = 200 sample)
  3. 同じ eval セットで PyTorch FP32 / ONNX FP32 / ONNX INT8 を比較
  4. 14cls collapse / 32cls 直接の劣化を表示

Usage:
    cd pc
    uv run --extra training python quantize_pipeline.py \\
        --ckpt runs/sweep_v1234_32cls_ambient/size_XL_seed_0/best.pt \\
        --out runs/quantize_v1234_ambient_32cls_XL \\
        --captures captures/full_32_train_v1 captures/full_32_train_v2 \\
                   captures/full_32_train_v3 captures/full_32_train_v4 \\
        --eval-sets captures/eval_quiet captures/eval_noise_low captures/eval_noise_high
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from dataset import IchiPingDataset, CLASS_ORDER_14, class_of
from model_32cls import IchiPingV1_32cls, IchiPingV1_32clsConfig, idx_to_bits


def export_onnx(ckpt: Path, out_onnx: Path, size: str) -> None:
    """32cls model checkpoint → ONNX FP32"""
    cfg = IchiPingV1_32clsConfig(size=size)
    model = IchiPingV1_32cls(cfg)
    ckpt_dict = torch.load(ckpt, map_location="cpu", weights_only=False)
    state = ckpt_dict["state_dict"] if "state_dict" in ckpt_dict else ckpt_dict
    model.load_state_dict(state)
    model.eval()

    dummy = torch.randn(1, 1, 1024, dtype=torch.float32)
    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(out_onnx),
        input_names=["spectrum"],
        output_names=["logits_32"],
        opset_version=13,
        do_constant_folding=True,
        dynamic_axes=None,
    )
    print(f"  exported FP32 ONNX -> {out_onnx} ({out_onnx.stat().st_size/1024:.1f} KB)")


def quantize_int8(onnx_fp32: Path, onnx_int8: Path, captures: list[Path],
                  n_calib: int = 200, baseline_override: Path | None = None) -> None:
    """ONNX FP32 -> INT8 (onnxruntime PTQ)"""
    from onnxruntime.quantization import quantize_static, QuantType

    # Calibration: train data の一部 (200 sample)
    ds = IchiPingDataset(captures_dirs=captures, feature_mode="noise_diff",
                         baseline_override_dir=baseline_override)
    print(f"  calibration source: {len(ds)} samples, using first {n_calib}")
    calib_arrays = []
    for i in range(min(n_calib, len(ds))):
        x = ds[i]["x"].numpy()
        if x.ndim == 2:
            x = x[None, :, :]
        calib_arrays.append(x)
    calib_data = np.concatenate(calib_arrays, axis=0).astype(np.float32)

    class _Reader:
        def __init__(self, data, name):
            self.it = iter(data)
            self.name = name
        def get_next(self):
            try:
                return {self.name: next(self.it)[None, ...]}
            except StopIteration:
                return None

    import onnx
    m = onnx.load(str(onnx_fp32))
    input_name = m.graph.input[0].name
    reader = _Reader(calib_data, input_name)
    quantize_static(
        model_input=str(onnx_fp32),
        model_output=str(onnx_int8),
        calibration_data_reader=reader,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    print(f"  exported INT8 ONNX -> {onnx_int8} ({onnx_int8.stat().st_size/1024:.1f} KB)")


def evaluate_onnx(onnx_path: Path, captures: Path, baseline_override: Path) -> dict:
    """ONNX 推論で eval set 評価。14cls collapse + 32cls 直接の acc を返す。"""
    import onnxruntime as ort
    ds = IchiPingDataset(captures_dirs=[captures], feature_mode="noise_diff",
                         baseline_override_dir=baseline_override)
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    correct32 = 0; correct14 = 0; total = 0
    cls_idx = {c: i for i, c in enumerate(CLASS_ORDER_14)}
    for i in range(len(ds)):
        item = ds[i]
        x = item["x"].numpy()
        if x.ndim == 2:
            x = x[None, :, :]
        out = sess.run(None, {input_name: x.astype(np.float32)})[0]
        pred32 = int(out.argmax(axis=-1)[0])
        true32 = int(item["state_idx"].item())
        if pred32 == true32:
            correct32 += 1
        true_bits = np.array(idx_to_bits(true32), dtype=np.int64)
        pred_bits = np.array(idx_to_bits(pred32), dtype=np.int64)
        if cls_idx[class_of(true_bits)] == cls_idx[class_of(pred_bits)]:
            correct14 += 1
        total += 1
    return {"acc_32cls": correct32 / total, "acc_14cls": correct14 / total, "n": total}


def evaluate_pytorch(ckpt: Path, captures: Path, baseline_override: Path,
                     size: str) -> dict:
    """PyTorch FP32 推論で同じ eval set 評価 (比較リファレンス)"""
    cfg = IchiPingV1_32clsConfig(size=size)
    model = IchiPingV1_32cls(cfg)
    ckpt_dict = torch.load(ckpt, map_location="cpu", weights_only=False)
    state = ckpt_dict["state_dict"] if "state_dict" in ckpt_dict else ckpt_dict
    model.load_state_dict(state)
    model.eval()

    ds = IchiPingDataset(captures_dirs=[captures], feature_mode="noise_diff",
                         baseline_override_dir=baseline_override)
    cls_idx = {c: i for i, c in enumerate(CLASS_ORDER_14)}
    correct32 = 0; correct14 = 0; total = 0
    with torch.no_grad():
        for i in range(len(ds)):
            item = ds[i]
            x = item["x"].unsqueeze(0)
            logits = model(x)
            pred32 = int(logits.argmax(dim=-1).item())
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
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="出力ディレクトリ")
    ap.add_argument("--captures", type=Path, nargs="+", required=True,
                    help="calibration 用 train captures")
    ap.add_argument("--eval-sets", type=Path, nargs="+", required=True,
                    help="評価する captures dirs (各 dir の s00000 を自己 baseline に使う)")
    ap.add_argument("--size", default="XL")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    onnx_fp32 = args.out / "model_fp32.onnx"
    onnx_int8 = args.out / "model_int8.onnx"

    print("== 1. PyTorch -> ONNX FP32 ==")
    export_onnx(args.ckpt, onnx_fp32, args.size)

    print("\n== 2. ONNX FP32 -> ONNX INT8 (PTQ) ==")
    # calibration は最初の captures dir の s00000 を baseline として使う
    quantize_int8(onnx_fp32, onnx_int8, args.captures, n_calib=200)

    print("\n== 3. Evaluation on eval sets ==")
    results = {}
    for eval_dir in args.eval_sets:
        print(f"\n  {eval_dir.name}:")
        # PyTorch FP32 (matched baseline = eval set 自身の s00000)
        pt = evaluate_pytorch(args.ckpt, eval_dir, eval_dir, args.size)
        print(f"    PyTorch FP32: 14cls={pt['acc_14cls']:.4f}, 32cls={pt['acc_32cls']:.4f}")
        fp32 = evaluate_onnx(onnx_fp32, eval_dir, eval_dir)
        print(f"    ONNX FP32   : 14cls={fp32['acc_14cls']:.4f}, 32cls={fp32['acc_32cls']:.4f}")
        int8 = evaluate_onnx(onnx_int8, eval_dir, eval_dir)
        print(f"    ONNX INT8   : 14cls={int8['acc_14cls']:.4f}, 32cls={int8['acc_32cls']:.4f}")
        print(f"    INT8 delta  : 14cls={int8['acc_14cls']-pt['acc_14cls']:+.4f}, "
              f"32cls={int8['acc_32cls']-pt['acc_32cls']:+.4f}")
        results[eval_dir.name] = {
            "pytorch_fp32": pt, "onnx_fp32": fp32, "onnx_int8": int8,
            "delta_14cls": int8["acc_14cls"] - pt["acc_14cls"],
            "delta_32cls": int8["acc_32cls"] - pt["acc_32cls"],
        }

    (args.out / "summary.json").write_text(json.dumps({
        "ckpt": str(args.ckpt), "size": args.size,
        "onnx_fp32_size_kb": onnx_fp32.stat().st_size / 1024,
        "onnx_int8_size_kb": onnx_int8.stat().st_size / 1024,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  summary -> {args.out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
