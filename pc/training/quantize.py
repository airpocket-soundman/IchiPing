"""INT8 quantization for IchiPing — PC-side step before MCU deploy.

Two paths supported:

  A. eiq-onnx2tflite (NXP, recommended)
     ONNX → TFLite with INT8 weights & activations in a single call.
     The output TFLite is then handed to eIQ Toolkit's Neutron Converter
     (manual step on Windows) to produce the MCXN947-ready file.

  B. onnxruntime PTQ (fallback, no NXP repo access)
     Pure ONNX → quantized ONNX with INT8 weights. Useful for PC-side
     sanity checks but NOT directly deployable to Neutron NPU.

Calibration data: a few hundred representative captures from the
training set. Read via ``IchiPingDataset`` with no augmentation so the
calibration histogram matches deployment statistics.

Usage:
    # Path A (preferred): install via pip from NXP index first
    #   pip install --index-url https://eiq.nxp.com/repository/ eiq-onnx2tflite
    python -m training.quantize --onnx runs/v1/best.onnx \\
        --out runs/v1/best_int8.tflite \\
        --captures captures/full_32_v2

    # Path B (no NXP tools)
    python -m training.quantize --onnx runs/v1/best.onnx \\
        --out runs/v1/best_int8.onnx --backend onnxruntime \\
        --captures captures/full_32_v2
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

import numpy as np


# ---------------------------------------------------------------------------
# Calibration data reader
# ---------------------------------------------------------------------------

def _collect_calibration(captures: List[Path], n_samples: int = 200) -> np.ndarray:
    """Pull up to n_samples spectrum tensors for activation calibration."""
    try:
        from dataset import IchiPingDataset
    except ImportError:
        from .dataset import IchiPingDataset  # type: ignore

    ds = IchiPingDataset(captures_dirs=captures)
    if len(ds) == 0:
        raise RuntimeError(f"no calibration data found in {captures}")
    n = min(n_samples, len(ds))
    print(f"  collecting {n} calibration samples from {len(ds)} examples")
    arrays = []
    for i in range(n):
        item = ds[i]
        # Shape: [channels, length] or [length]; we want [1, 1, length]
        x = item["x"].numpy()
        if x.ndim == 1:
            x = x[None, None, :]
        elif x.ndim == 2:
            x = x[None, :, :]
        arrays.append(x)
    return np.concatenate(arrays, axis=0).astype(np.float32)


# ---------------------------------------------------------------------------
# Path A: NXP eiq-onnx2tflite
# ---------------------------------------------------------------------------

def _quantize_eiq(onnx_path: Path, out_path: Path, captures: List[Path],
                  n_calib: int = 200, per_channel: bool = True) -> None:
    """Call eiq-onnx2tflite to produce an INT8 TFLite suitable for Neutron."""
    if shutil.which("eiq-onnx2tflite") is None:
        raise RuntimeError(
            "eiq-onnx2tflite not in PATH. Install via:\n"
            "  pip install --index-url https://eiq.nxp.com/repository/ eiq-onnx2tflite\n"
            "or use --backend onnxruntime for the fallback path."
        )

    # Dump calibration npz that eiq-onnx2tflite will read.
    calib_data = _collect_calibration(captures, n_calib)
    calib_npz = out_path.with_suffix(".calib.npz")
    np.savez_compressed(calib_npz, spectrum=calib_data)
    print(f"  calibration data: {calib_data.shape} → {calib_npz}")

    cmd = [
        "eiq-onnx2tflite",
        "--cast-int64-to-int32",
        "--symbolic-dimension-into-static",
        "--calibration-input", str(calib_npz),
        str(onnx_path),
        str(out_path),
    ]
    if per_channel:
        cmd.insert(1, "--per-channel")
    print(f"  running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"  wrote {out_path}")
    print()
    print("Next steps (manual, on Windows):")
    print("  1. Open eIQ Toolkit CLI environment")
    print(f"  2. eiq-converter --plugin eiq-converter-neutron \\")
    print(f'                   --custom-options "target mcxn94x" \\')
    print(f"                   {out_path.name} {out_path.stem}_neutron.tflite")
    print("  3. xxd -i <neutron.tflite> > model_data.h  (for firmware include)")


# ---------------------------------------------------------------------------
# Path B: onnxruntime fallback
# ---------------------------------------------------------------------------

class _ONNXCalibrationDataReader:
    """Adapter so onnxruntime.quantization can pull calibration arrays."""

    def __init__(self, calib_data: np.ndarray, input_name: str) -> None:
        self.it = iter(calib_data)
        self.input_name = input_name

    def get_next(self):
        try:
            arr = next(self.it)
        except StopIteration:
            return None
        return {self.input_name: arr[None, ...]}


def _quantize_ort(onnx_path: Path, out_path: Path, captures: List[Path],
                  n_calib: int = 200) -> None:
    """PTQ via onnxruntime.quantization. Output is ONNX (NOT a TFLite file).

    Useful for PC sanity checks only — Neutron pipeline requires path A.
    """
    try:
        from onnxruntime.quantization import quantize_static, QuantType
    except ImportError as e:
        raise RuntimeError(
            "onnxruntime.quantization not installed. "
            "Run: pip install onnxruntime"
        ) from e

    calib_data = _collect_calibration(captures, n_calib)
    print(f"  calibration data: {calib_data.shape}")

    # Discover the input name from the ONNX model.
    import onnx
    m = onnx.load(str(onnx_path))
    input_name = m.graph.input[0].name
    print(f"  input tensor name: {input_name}")

    reader = _ONNXCalibrationDataReader(calib_data, input_name)
    quantize_static(
        model_input=str(onnx_path),
        model_output=str(out_path),
        calibration_data_reader=reader,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    print(f"  wrote INT8 ONNX → {out_path}")
    print("  (this file is for PC sanity check only — for MCU use path A)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="INT8 quantize a trained ONNX.")
    ap.add_argument("--onnx", type=Path, required=True,
                    help="input ONNX (from export_onnx.py)")
    ap.add_argument("--out", type=Path, required=True,
                    help="output file (.tflite for eiq, .onnx for ort)")
    ap.add_argument("--captures", type=Path, nargs="+", required=True,
                    help="capture dirs to draw calibration data from")
    ap.add_argument("--n-calib", type=int, default=200, dest="n_calib")
    ap.add_argument("--backend", choices=["eiq", "onnxruntime"], default="eiq",
                    help="quantization backend (default: eiq via NXP tool)")
    ap.add_argument("--per-channel", action="store_true", default=True,
                    dest="per_channel")
    args = ap.parse_args(argv)

    print(f"quantizing {args.onnx}  ({args.backend})")
    if args.backend == "eiq":
        _quantize_eiq(args.onnx, args.out, args.captures,
                      n_calib=args.n_calib, per_channel=args.per_channel)
    else:
        _quantize_ort(args.onnx, args.out, args.captures, n_calib=args.n_calib)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
