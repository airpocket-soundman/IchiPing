"""Neutron arch を 4D in / 4D out で ONNX export + 各種 TFLite 変換比較。

通常版 (3D in / 2D out) は内部 Unsqueeze/Squeeze 由来の Reshape が 3 個残って
NPU 比率を下げる。4D in / 4D out なら Reshape ゼロで Conv2D + AvgPool2D + 1×1 Conv
だけのクリーンなグラフになる。

変換 2 路線を試して NPU 比率を比較する:

  A. NXP 純正: onnx2quant + onnx2tflite
  B. PINTO0309/onnx2tf: NCHW→NHWC を Transpose で散らかさず融合する派

どちらも最後は neutron-converter に投げて NPU/Total op 比率を見る。

Usage:
    cd pc
    uv run python export_neutron_4d.py \\
        --ckpt runs/neutron_v1234_32cls_ambient_XL/best.pt \\
        --out runs/neutron_v1234_32cls_ambient_XL/deploy4d \\
        --captures captures/full_32_train_v1 captures/full_32_train_v2 \\
                   captures/full_32_train_v3 captures/full_32_train_v4 \\
        --size XL --backend both
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from dataset import IchiPingDataset
from model_32cls_neutron import (
    IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig,
)


class NeutronDeploy4D(nn.Module):
    """4D in (B,1,1,1024) → 4D out (B,32,1,1)。Reshape 無しのクリーン版。"""

    def __init__(self, base: IchiPingV1_32clsNeutron) -> None:
        super().__init__()
        self.conv1 = base.conv1; self.bn1 = base.bn1
        self.conv2 = base.conv2; self.bn2 = base.bn2
        self.conv3 = base.conv3; self.bn3 = base.bn3
        self.conv4 = base.conv4; self.bn4 = base.bn4
        self.avgpool = base.avgpool
        self.classifier = base.classifier

    def forward(self, x):
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = F.relu(self.bn4(self.conv4(h)))
        h = self.avgpool(h)
        return self.classifier(h)


def export_onnx_4d(ckpt: Path, out_onnx: Path, size: str) -> None:
    base = IchiPingV1_32clsNeutron(IchiPingV1_32clsNeutronConfig(size=size))
    state = torch.load(ckpt, map_location="cpu", weights_only=False)
    sd = state["state_dict"] if "state_dict" in state else state
    base.load_state_dict(sd)
    base.eval()
    base.fold_bn_inplace()

    deploy = NeutronDeploy4D(base)
    deploy.eval()

    dummy = torch.randn(1, 1, 1, 1024, dtype=torch.float32)
    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        deploy, dummy, str(out_onnx),
        input_names=["spectrum_4d"],
        output_names=["logits_4d"],
        opset_version=13,
        do_constant_folding=True,
        dynamic_axes=None,
    )
    print(f"  exported 4D ONNX (BN folded) -> {out_onnx} "
          f"({out_onnx.stat().st_size/1024:.1f} KB)")


def collect_calibration_4d(captures: list[Path], n: int = 200,
                           out_npy: Path | None = None) -> np.ndarray:
    """noise_diff feature を (n, 1, 1, 1024) で保存。"""
    ds = IchiPingDataset(captures_dirs=captures, feature_mode="noise_diff")
    arrays = []
    for i in range(min(n, len(ds))):
        x = ds[i]["x"].numpy()  # (1, 1024)
        if x.ndim == 2:
            x = x[None, :, :]   # → (1, 1, 1024)
        x = x[:, None, :, :]    # → (1, 1, 1, 1024) — onnx2tf calibration 形式
        arrays.append(x)
    data = np.concatenate(arrays, axis=0).astype(np.float32)  # (n, 1, 1, 1024)
    if out_npy is not None:
        np.save(out_npy, data)
        print(f"  saved calibration: {data.shape} -> {out_npy}")
    return data


def quantize_nxp(onnx_fp32: Path, out_tflite: Path, calib_npy_dir: Path) -> None:
    """NXP onnx2quant + onnx2tflite path。"""
    quant_cli  = shutil.which("onnx2quant")
    tflite_cli = shutil.which("onnx2tflite")
    qdq_path = out_tflite.with_suffix(".qdq.onnx")

    cmd_quant = [quant_cli, "-o", str(qdq_path), "--per-channel",
                 str(onnx_fp32), "-c", f"spectrum_4d;{calib_npy_dir}"]
    print(f"  [NXP] onnx2quant: {' '.join(cmd_quant)}")
    subprocess.run(cmd_quant, check=True)

    cmd_tflite = [tflite_cli, "--qdq-aware-conversion", "--cast-int64-to-int32",
                  "-o", str(out_tflite), str(qdq_path)]
    print(f"  [NXP] onnx2tflite: {' '.join(cmd_tflite)}")
    subprocess.run(cmd_tflite, check=True)
    print(f"  [NXP] -> {out_tflite} ({out_tflite.stat().st_size/1024:.1f} KB)")


def quantize_pinto(onnx_fp32: Path, out_dir: Path, calib_npy: Path) -> Path:
    """PINTO0309/onnx2tf path: ONNX → TFLite INT8 (per-channel)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["uv", "run", "onnx2tf",
           "-i", str(onnx_fp32),
           "-o", str(out_dir),
           "-oiqt", "-qt", "per-channel",
           "-cind", "spectrum_4d", str(calib_npy), "[[[[0.0]]]]", "[[[[1.0]]]]",
           # ↑ mean=0, std=1 (実データは noise_diff で平均約 0 だが、calibration
           #   そのものを使うので mean/std による正規化は不要として渡している)
           ]
    print(f"  [PINTO] onnx2tf: {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:]); print(r.stderr[-1500:])
        raise RuntimeError("onnx2tf failed")
    # onnx2tf は <stem>_full_integer_quant.tflite を吐く
    cands = list(out_dir.glob("*_full_integer_quant.tflite"))
    if not cands:
        cands = list(out_dir.glob("*_integer_quant.tflite"))
    if not cands:
        cands = sorted(out_dir.glob("*.tflite"))
        print(f"  [PINTO] candidates: {[c.name for c in cands]}")
        raise RuntimeError("no INT8 tflite found")
    out_tflite = cands[0]
    print(f"  [PINTO] -> {out_tflite} ({out_tflite.stat().st_size/1024:.1f} KB)")
    return out_tflite


def run_neutron_converter(in_tflite: Path, out_tflite: Path) -> str:
    neutron = Path("d:/workspace/eIQ/bin/neutron-converter.exe")
    if not neutron.exists():
        raise RuntimeError(f"neutron-converter not at {neutron}")
    cmd = [str(neutron), "--input", str(in_tflite),
           "--output", str(out_tflite), "--target", "mcxn94x"]
    print(f"  [NEUTRON] {' '.join(cmd)}")
    r = subprocess.run(cmd, check=True, capture_output=True, text=True)
    # NPU 比率を抽出
    lines = r.stdout.splitlines()
    for ln in lines:
        if "Operator conversion ratio" in ln:
            print(f"  [NEUTRON] {ln.strip()}")
    if out_tflite.exists():
        print(f"  [NEUTRON] -> {out_tflite} ({out_tflite.stat().st_size/1024:.1f} KB)")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--size", default="XL")
    ap.add_argument("--backend", choices=("nxp", "pinto", "both"), default="both")
    ap.add_argument("--n-calib", type=int, default=200, dest="n_calib")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    onnx_fp32 = args.out / "model_fp32_4d.onnx"

    print("== 1. Export 4D ONNX (Reshape-free) ==")
    export_onnx_4d(args.ckpt, onnx_fp32, args.size)

    print("\n== 2. Collect calibration data ==")
    calib_npy = args.out / "calib.npy"
    calib_dir = args.out / "calib_npy"
    calib_dir.mkdir(exist_ok=True)
    for old in calib_dir.glob("*.npy"):
        old.unlink()
    data = collect_calibration_4d(args.captures, args.n_calib, calib_npy)
    # NXP path 用に個別 npy も書き出し
    for i, arr in enumerate(data):
        np.save(calib_dir / f"calib_{i:04d}.npy", arr[None, :, :, :])
    print(f"  + {len(data)} npy files in {calib_dir}")

    summary = {}

    if args.backend in ("nxp", "both"):
        print("\n== 3a. NXP onnx2quant + onnx2tflite ==")
        nxp_tflite = args.out / "model_int8_nxp.tflite"
        quantize_nxp(onnx_fp32, nxp_tflite, calib_dir)
        nxp_neutron = args.out / "model_int8_nxp_neutron.tflite"
        nxp_log = run_neutron_converter(nxp_tflite, nxp_neutron)
        summary["nxp"] = {"tflite_kb": nxp_tflite.stat().st_size / 1024,
                          "neutron_kb": nxp_neutron.stat().st_size / 1024
                                         if nxp_neutron.exists() else None,
                          "log_tail": nxp_log[-2000:]}

    if args.backend in ("pinto", "both"):
        print("\n== 3b. PINTO0309 onnx2tf ==")
        pinto_dir = args.out / "pinto"
        pinto_tflite = quantize_pinto(onnx_fp32, pinto_dir, calib_npy)
        pinto_neutron = args.out / "model_int8_pinto_neutron.tflite"
        pinto_log = run_neutron_converter(pinto_tflite, pinto_neutron)
        summary["pinto"] = {"tflite_kb": pinto_tflite.stat().st_size / 1024,
                            "neutron_kb": pinto_neutron.stat().st_size / 1024
                                           if pinto_neutron.exists() else None,
                            "log_tail": pinto_log[-2000:]}

    print("\n== Summary ==")
    for k, v in summary.items():
        print(f"  {k}: tflite={v['tflite_kb']:.1f} KB, "
              f"neutron={v['neutron_kb']:.1f if v['neutron_kb'] else 'N/A'} KB")
        # NPU 比率を再表示
        for ln in v["log_tail"].splitlines():
            if "conversion ratio" in ln.lower():
                print(f"    {ln.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
