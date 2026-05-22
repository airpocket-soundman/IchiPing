"""Export a trained PyTorch checkpoint to ONNX (opset 13, fixed shape).

Output ONNX is then consumed by ``quantize.py`` and ultimately by NXP's
``eiq-onnx2tflite`` + eIQ Toolkit Neutron Converter. See
``docs/nn_review.html §1.A`` for the full pipeline.

Constraints baked in to match the MCXN947 NPU pipeline:
  - opset_version = 13 (proven path through eiq-onnx2tflite)
  - input shape is FIXED (no dynamic axes); batch dimension is 1
  - model must be in eval() mode so BatchNorm folds correctly

Usage:
    python -m training.export_onnx --ckpt runs/v1/best.pt --out runs/v1/best.onnx
    python -m training.export_onnx --ckpt runs/v1/best.pt --out runs/v1/best.onnx \\
        --input-len 1024
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch

try:
    from model import IchiPingV1
except ImportError:
    from .model import IchiPingV1  # type: ignore


def export(ckpt: Path, out: Path, input_len: int = 1024,
           opset: int = 13, verbose: bool = True) -> None:
    model = IchiPingV1()
    state = torch.load(ckpt, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    # Dummy input: batch=1, 1 channel, input_len bins.
    # (Conv2D-with-H=1 implementations may need a different shape — adapt
    # input_names/dummy to match model.py's forward signature.)
    dummy = torch.randn(1, 1, input_len, dtype=torch.float32)

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(out),
        input_names=["spectrum"],
        output_names=["any_open", "window_a", "window_b",
                      "window_c", "door_AB", "door_BC"],
        opset_version=opset,
        do_constant_folding=True,
        dynamic_axes=None,           # fully static, MCU-deployable
    )
    if verbose:
        print(f"exported ONNX (opset {opset}, input [1,1,{input_len}]) → {out}")

    # Quick smoke test: load with onnxruntime and check outputs match.
    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
        dummy_np = dummy.numpy()
        with torch.no_grad():
            torch_out = model(dummy)
        ort_outs = sess.run(None, {"spectrum": dummy_np})
        ort_names = [o.name for o in sess.get_outputs()]
        ort_map = dict(zip(ort_names, ort_outs))
        ok = True
        for name, t_v in torch_out.items():
            o_v = ort_map.get(name)
            if o_v is None: continue
            diff = float(np.abs(t_v.numpy() - o_v).max())
            if diff > 1e-3:
                print(f"  WARN: {name} max diff {diff:.4g} (PyTorch vs ORT)")
                ok = False
        if ok and verbose:
            print("  smoke test passed — PyTorch ≈ ONNXRuntime within 1e-3")
    except ImportError:
        if verbose:
            print("  (onnxruntime not installed; skipping numeric smoke test)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export PyTorch checkpoint to ONNX.")
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--input-len", type=int, default=1024,
                    dest="input_len",
                    help="length of the 1D spectrum input (default 1024)")
    ap.add_argument("--opset", type=int, default=13)
    args = ap.parse_args(argv)
    export(args.ckpt, args.out, input_len=args.input_len, opset=args.opset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
