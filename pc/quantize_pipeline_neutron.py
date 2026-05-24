"""Neutron arch (Conv2D ベース) 専用の量子化パイプライン。

quantize_pipeline.py (旧 Conv1D arch 用) との違い:

  1. 学習済み IchiPingV1_32clsNeutron を eval + fold_bn_inplace してから ONNX export
     (BN を Conv2D 内部に畳み込んで TFLite 変換時に不要な op を排除)
  2. ONNX 入力名は "spectrum" 固定 (旧 pipeline と同じ)
  3. PC eval は PyTorch/ONNX FP32/ONNX INT8 を 3 環境 (eval_quiet/noise_low/noise_high)
     で比較し、旧 Conv1D 版の数字と直接見比べやすい summary を吐く
  4. INT8 ONNX 出力ファイルはこの後 NXP onnx2tflite + neutron-converter に投げる

Usage:
    cd pc
    uv run --extra training python quantize_pipeline_neutron.py \\
        --ckpt runs/neutron_v1234_32cls_ambient_XL/best.pt \\
        --out runs/quantize_neutron_v1234_32cls_XL \\
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
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from dataset import IchiPingDataset, CLASS_ORDER_14, class_of
from model_32cls_neutron import (
    IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig, idx_to_bits,
)


class NeutronDeployWrapper(nn.Module):
    """Export 用ラッパー: 入出力とも 4D 化して内部 Unsqueeze/Squeeze を消す。

    通常モデルは forward 冒頭の `x.unsqueeze(2)` と末尾の `.squeeze(-1).squeeze(-1)` で
    Reshape op を 3 個生む。Neutron NPU はこの Reshape を CPU に落とすため NPU 比率が
    下がる。Export 時だけ 4D in/4D out にすればグラフ内 Reshape が消え、NPU 比率が
    上がる (実機側で入力を (1,1,1,1024) で渡し、出力 (1,32,1,1) を受ければ良い)。
    """

    def __init__(self, base: IchiPingV1_32clsNeutron) -> None:
        super().__init__()
        # 元モデルの submodule を共有 (fold_bn 済み前提)
        self.conv1 = base.conv1; self.bn1 = base.bn1
        self.conv2 = base.conv2; self.bn2 = base.bn2
        self.conv3 = base.conv3; self.bn3 = base.bn3
        self.conv4 = base.conv4; self.bn4 = base.bn4
        self.avgpool = base.avgpool
        self.classifier = base.classifier

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, 1, 1, 1024) → (B, 32, 1, 1)、Reshape 一切無し
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = F.relu(self.bn4(self.conv4(h)))
        h = self.avgpool(h)
        return self.classifier(h)


def export_onnx(ckpt: Path, out_onnx: Path, size: str) -> None:
    """Neutron arch checkpoint → ONNX FP32 (BN fold 済み)"""
    cfg = IchiPingV1_32clsNeutronConfig(size=size)
    model = IchiPingV1_32clsNeutron(cfg)
    ckpt_dict = torch.load(ckpt, map_location="cpu", weights_only=False)
    state = ckpt_dict["state_dict"] if "state_dict" in ckpt_dict else ckpt_dict
    model.load_state_dict(state)
    model.eval()
    # BN を Conv2D に畳む (export 後の TFLite で BN op が消える)
    model.fold_bn_inplace()

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
    print(f"  exported FP32 ONNX (BN folded) -> {out_onnx} "
          f"({out_onnx.stat().st_size/1024:.1f} KB)")


def quantize_int8(onnx_fp32: Path, onnx_int8: Path, captures: list[Path],
                  n_calib: int = 200) -> None:
    """ONNX FP32 -> INT8 (onnxruntime PTQ, per-channel weights + activations)"""
    from onnxruntime.quantization import quantize_static, QuantType

    ds = IchiPingDataset(captures_dirs=captures, feature_mode="noise_diff")
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
    print(f"  exported INT8 ONNX -> {onnx_int8} "
          f"({onnx_int8.stat().st_size/1024:.1f} KB)")


def evaluate_onnx(onnx_path: Path, captures: Path, baseline_override: Path) -> dict:
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
    cfg = IchiPingV1_32clsNeutronConfig(size=size)
    model = IchiPingV1_32clsNeutron(cfg)
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
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--eval-sets", type=Path, nargs="+", required=True)
    ap.add_argument("--size", default="XL")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    onnx_fp32 = args.out / "model_fp32.onnx"
    onnx_int8 = args.out / "model_int8.onnx"

    print("== 1. PyTorch (Neutron arch) -> ONNX FP32 (BN folded) ==")
    export_onnx(args.ckpt, onnx_fp32, args.size)

    print("\n== 2. ONNX FP32 -> ONNX INT8 (PTQ, per-channel) ==")
    quantize_int8(onnx_fp32, onnx_int8, args.captures, n_calib=200)

    print("\n== 3. Evaluation on eval sets ==")
    results = {}
    for eval_dir in args.eval_sets:
        print(f"\n  {eval_dir.name}:")
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
        "ckpt": str(args.ckpt), "size": args.size, "arch": "neutron",
        "onnx_fp32_size_kb": onnx_fp32.stat().st_size / 1024,
        "onnx_int8_size_kb": onnx_int8.stat().st_size / 1024,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  summary -> {args.out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
