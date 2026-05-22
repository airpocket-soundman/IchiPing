"""IchiPing — live MCU-triggered inference.

Triggers a Ping on the MCU, receives one ICHP frame, runs the trained
PyTorch model on the audio (PC side), and prints the predicted state.
Uses the same features pipeline as training (training/features.py) so
predictions match what the model was trained on.

Distinct from:
  - pc/inference_client.py  → monitors firmware/10_inference autonomous
                                inference output (read-only, MCU does the
                                inference itself, this script tails ASCII)
  - pc/training/infer.py    → batch inference on captures/<run_id>/sXXXXX/
                                already saved to disk (no serial)

This tool sits in between: PC does the inference (so we can iterate the
model without re-flashing) but the audio comes live from the device.

Modes
-----
  live      Continuous Ping → predict loop. Operator can move servos
            manually between Pings to see predictions change. Ctrl-C to stop.
  single    One Ping, print prediction, exit.
  verify    Drive servos via a plan YAML (like collector_client --plan)
            but instead of saving WAVs, run inference and compare to the
            plan-declared state. Prints overall + per-class accuracy.

Examples
--------
  uv run --extra training python live_infer.py live \\
      --port COM3 --ckpt ../runs/v1/best.pt --pattern chirp_200_6k --interval 1.5

  uv run --extra training python live_infer.py single \\
      --port COM3 --ckpt ../runs/v1/best.pt --pattern chirp_200_6k

  uv run --extra training python live_infer.py verify \\
      --port COM3 --ckpt ../runs/v1/best.pt \\
      --plan plans/full_32_v2.yaml --pattern chirp_200_6k
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from queue import Empty
from typing import Optional

import numpy as np
import serial
import torch

# Local imports — collector_client provides the serial / framing layer
from collector_client import (
    StreamReader, send, wait_for_ack, wait_for_prefix,
    load_plan,
)
from patterns import PatternLibrary

# Training pipeline — same features + model as training
sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from features import samples_to_features                # noqa: E402
from model import IchiPingV1                             # noqa: E402
from model_32cls import IchiPingV1_32cls, idx_to_bits    # noqa: E402
from dataset import class_of, parse_state_label          # noqa: E402


DEFAULT_BAUD = 921_600
DEFAULT_PATTERNS_PATH = Path(__file__).resolve().parent / "patterns.yaml"


# ---------------------------------------------------------------------------
# Inference core
# ---------------------------------------------------------------------------

def load_model(ckpt: Path, device: str, arch: str = "v1"):
    """Load a checkpoint. `arch` is 'v1' (14-class multi-head, default) or
    '32cls' (single 32-way softmax). The two architectures share the same
    backbone so they take the same input features."""
    if arch == "32cls":
        model = IchiPingV1_32cls()
    else:
        model = IchiPingV1()
    state = torch.load(ckpt, map_location=device)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()
    return model.to(device)


def predict_32cls(model: IchiPingV1_32cls, samples: np.ndarray, device: str):
    """32-class softmax model. Returns (5-bit state, dict of raw outputs)."""
    feats = samples_to_features(samples)
    x = torch.from_numpy(feats).float().unsqueeze(0).unsqueeze(0).to(device)
    with torch.no_grad():
        logits = model(x)
    probs = torch.softmax(logits, dim=-1).squeeze(0).cpu().numpy()
    idx = int(probs.argmax())
    bits_list = idx_to_bits(idx)
    bits = np.array(bits_list, dtype=np.int64)
    raw = {
        "class_idx": idx,
        "class_p":   float(probs[idx]),
        "top3":      sorted(enumerate(probs), key=lambda p: -p[1])[:3],
        # Mock the other keys for unified printing
        "any_open_p":   float(bits.sum() > 0),
        "window_a_p":   float(bits[0]),
        "door_AB_p":    float(bits[3]),
        "window_b_cls": int(bits[1]),
        "window_c_cls": int(bits[2]),
        "door_BC_cls":  int(bits[4]),
    }
    return bits, raw


def predict(model, samples: np.ndarray, device: str, arch: str = "v1"):
    """Returns (5-bit state array, dict of raw head outputs).

    `samples` is float32 audio in [-1, +1]. The 5-bit array is ordered
    [window_a, window_b, window_c, door_AB, door_BC] matching the
    sABCDE label convention used everywhere else in the codebase.
    """
    if arch == "32cls":
        return predict_32cls(model, samples, device)
    feats = samples_to_features(samples)        # (1024,)
    x = torch.from_numpy(feats).float().unsqueeze(0).unsqueeze(0).to(device)
    # shape: (batch=1, channel=1, length=1024)

    with torch.no_grad():
        out = model(x)

    def _scalar(t: torch.Tensor) -> float:
        return float(t.squeeze().detach().cpu().numpy())

    def _argmax(t: torch.Tensor) -> int:
        return int(t.squeeze(0).detach().cpu().numpy().argmax())

    # Continuous heads (a, AB): sigmoid → bit at 0.5 threshold
    a_p   = _scalar(out["window_a"]);  a_bit  = int(a_p  > 0.5)
    AB_p  = _scalar(out["door_AB"]);   AB_bit = int(AB_p > 0.5)
    # Multi-class heads (b, c, BC): argmax, collapse to "0 vs >0" for binary
    b_cls  = _argmax(out["window_b"]); b_bit  = int(b_cls  > 0)
    c_cls  = _argmax(out["window_c"]); c_bit  = int(c_cls  > 0)
    BC_cls = _argmax(out["door_BC"]);  BC_bit = int(BC_cls > 0)

    bits = np.array([a_bit, b_bit, c_bit, AB_bit, BC_bit], dtype=np.int64)
    raw = {
        "any_open_p":   _scalar(out["any_open"]),
        "window_a_p":   a_p,
        "door_AB_p":    AB_p,
        "window_b_cls": b_cls,
        "window_c_cls": c_cls,
        "door_BC_cls":  BC_cls,
    }
    return bits, raw


def state_label(bits: np.ndarray) -> str:
    return "s" + "".join(str(b) for b in bits)


# ---------------------------------------------------------------------------
# Board ↔ PC plumbing
# ---------------------------------------------------------------------------

def open_board(port: str, baud: int = DEFAULT_BAUD) -> tuple[serial.Serial, StreamReader]:
    ser = serial.Serial(port, baud, timeout=0.1)
    reader = StreamReader(ser)
    reader.start()
    if wait_for_prefix(reader, "INFO IchiPing", timeout=6.0) is None:
        print("warning: no boot banner in 6 s; assuming MCU is already up",
              file=sys.stderr)
    return ser, reader


def close_board(ser: Optional[serial.Serial], reader: Optional[StreamReader]) -> None:
    if reader: reader.stop()
    time.sleep(0.05)
    if ser:
        try: ser.close()
        except Exception: pass


def setup_mcu(ser, reader, lib: PatternLibrary, pattern_key, eq_on: bool):
    """Push patterns.yaml, select target, configure repeats=1 and EQ."""
    lib.push(send_line=lambda line: send(ser, line),
             wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
             log=None)
    idx, pat = lib.find(pattern_key)
    send(ser, f"PAT SELECT {idx}");   wait_for_ack(reader, timeout=2.0)
    send(ser, "SET REPEATS 1");       wait_for_ack(reader, timeout=2.0)
    send(ser, "EQ ENABLE" if eq_on else "EQ DISABLE")
    wait_for_ack(reader, timeout=2.0)
    return idx, pat.name


def trigger_and_capture(ser, reader, timeout_s: float = 10.0):
    """Issue RUN, return the single ICHP frame (or None on timeout)."""
    send(ser, "RUN")
    if wait_for_prefix(reader, "OK RUN started", timeout=3.0) is None:
        return None
    try:
        frame = reader.frames.get(timeout=timeout_s)
    except Empty:
        return None
    wait_for_prefix(reader, "OK RUN done", timeout=2.0)
    return frame


def set_servo_state(ser, reader, bits: np.ndarray) -> None:
    """Drive 5 servos to the given binary state via SET PIN commands.
    Matches the encoding used by pc/plans/gen32.py:
        0=closed → mechanical 180°, 1=open → mechanical 0°.
    """
    names = ("a", "b", "c", "AB", "BC")
    send(ser, "CLEAR PINS");          wait_for_ack(reader, timeout=2.0)
    for i, n in enumerate(names):
        deg = 0 if bits[i] == 1 else 180
        send(ser, f"SET PIN {n} {deg}")
        wait_for_ack(reader, timeout=2.0)


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _print_header() -> None:
    print(f"{'time':<10} {'pred':<8} {'class':<5} "
          f"{'p_a':>5} {'p_AB':>5} {'b':>3} {'c':>3} {'BC':>3}  note")
    print("-" * 78)


def _print_row(label: str, cls: str, raw: dict, note: str = "") -> None:
    t = time.strftime("%H:%M:%S")
    print(f"{t:<10} {label:<8} {cls:<5} "
          f"{raw['window_a_p']:5.2f} {raw['door_AB_p']:5.2f} "
          f"{raw['window_b_cls']:>3} {raw['window_c_cls']:>3} {raw['door_BC_cls']:>3}  "
          f"{note}")


def _frame_to_samples(frame) -> np.ndarray:
    return np.frombuffer(frame.samples, dtype=np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_live(args) -> int:
    print(f"loading {args.ckpt} on {args.device}")
    model = load_model(args.ckpt, args.device, arch=args.arch)
    lib = PatternLibrary.load_yaml(args.patterns)

    ser = None; reader = None
    try:
        ser, reader = open_board(args.port, args.baud)
        idx, name = setup_mcu(ser, reader, lib, args.pattern, args.eq_on)
        print(f"probing pattern[{idx}]={name}, EQ={'on' if args.eq_on else 'off'}, "
              f"interval={args.interval}s   (Ctrl-C to stop)\n")
        _print_header()

        loops = 0
        while True:
            frame = trigger_and_capture(ser, reader, timeout_s=args.interval + 5.0)
            if frame is None:
                print(f"{'':10} (no frame; retrying)")
                continue
            bits, raw = predict(model, _frame_to_samples(frame), args.device, arch=args.arch)
            _print_row(state_label(bits), class_of(bits), raw)
            loops += 1
            if args.max_loops > 0 and loops >= args.max_loops:
                break
            if args.interval > 0:
                time.sleep(args.interval)
        return 0
    except KeyboardInterrupt:
        print("\nstopped by user")
        return 0
    finally:
        close_board(ser, reader)


def cmd_single(args) -> int:
    model = load_model(args.ckpt, args.device, arch=args.arch)
    lib = PatternLibrary.load_yaml(args.patterns)
    ser = None; reader = None
    try:
        ser, reader = open_board(args.port, args.baud)
        idx, name = setup_mcu(ser, reader, lib, args.pattern, args.eq_on)
        print(f"probing pattern[{idx}]={name}, EQ={'on' if args.eq_on else 'off'}\n")
        _print_header()

        frame = trigger_and_capture(ser, reader, timeout_s=10.0)
        if frame is None:
            print("FAIL: no frame received", file=sys.stderr)
            return 2
        bits, raw = predict(model, _frame_to_samples(frame), args.device)
        _print_row(state_label(bits), class_of(bits), raw)
        return 0
    finally:
        close_board(ser, reader)


def cmd_verify(args) -> int:
    """Run a plan, predicting each step and comparing to plan-declared state."""
    model = load_model(args.ckpt, args.device, arch=args.arch)
    lib = PatternLibrary.load_yaml(args.patterns)
    steps = load_plan(args.plan)
    print(f"loaded {len(steps)} plan steps")

    ser = None; reader = None
    correct = 0; total = 0
    per_class_correct: dict = {}
    per_class_total: dict = {}
    try:
        ser, reader = open_board(args.port, args.baud)
        idx, name = setup_mcu(ser, reader, lib, args.pattern, args.eq_on)
        print(f"probing pattern[{idx}]={name}, EQ={'on' if args.eq_on else 'off'}\n")
        _print_header()

        for step in steps:
            true_bits = parse_state_label(step.label)
            if true_bits is None:
                continue
            set_servo_state(ser, reader, true_bits)
            time.sleep(args.settle)

            frame = trigger_and_capture(ser, reader, timeout_s=10.0)
            if frame is None:
                print(f"  {step.label}: no frame", file=sys.stderr)
                continue
            samples = _frame_to_samples(frame)
            pred_bits, raw = predict(model, samples, args.device, arch=args.arch)
            true_cls = class_of(true_bits)
            pred_cls = class_of(pred_bits)
            ok = (true_cls == pred_cls)
            note = "OK" if ok else f"MISS truth={step.label} cls={true_cls}"
            _print_row(state_label(pred_bits), pred_cls, raw, note)

            total += 1
            per_class_total[true_cls] = per_class_total.get(true_cls, 0) + 1
            if ok:
                correct += 1
                per_class_correct[true_cls] = per_class_correct.get(true_cls, 0) + 1

        print("\n--- summary ---")
        if total:
            print(f"overall accuracy: {correct}/{total} = {correct/total:.3f}")
            print("per-class:")
            for cls in sorted(per_class_total):
                c = per_class_correct.get(cls, 0)
                t = per_class_total[cls]
                print(f"  {cls:<3} {c}/{t} = {c/t:.2f}")
        else:
            print("(no recognised state labels in plan — nothing to verify)")
        return 0
    finally:
        close_board(ser, reader)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _common_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--port",     required=True)
    sp.add_argument("--baud",     type=int, default=DEFAULT_BAUD)
    sp.add_argument("--ckpt",     type=Path, required=True,
                    help="trained PyTorch checkpoint (best.pt)")
    sp.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS_PATH)
    sp.add_argument("--pattern",  default="chirp_200_6k",
                    help="pattern name or index from patterns.yaml")
    sp.add_argument("--eq-on",    action="store_true", dest="eq_on",
                    help="enable speaker EQ before Pinging (default: OFF)")
    sp.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    sp.add_argument("--arch",     choices=["v1", "32cls"], default="v1",
                    help="model architecture: 'v1' (14-class multi-head, default) "
                         "or '32cls' (32-class softmax, experimental)")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="live_infer",
                                 description="IchiPing — live MCU-triggered inference.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("live", help="continuous Ping → predict loop")
    _common_args(s)
    s.add_argument("--interval",  type=float, default=1.0,
                   help="seconds between Pings (default 1.0)")
    s.add_argument("--max-loops", type=int, default=0, dest="max_loops",
                   help="stop after N loops (0 = unlimited)")
    s.set_defaults(func=cmd_live)

    s = sub.add_parser("single", help="one Ping, predict, exit")
    _common_args(s)
    s.set_defaults(func=cmd_single)

    s = sub.add_parser("verify", help="drive servos via plan + verify predictions")
    _common_args(s)
    s.add_argument("--plan",   type=Path, required=True,
                   help="plan YAML (e.g. plans/full_32_v2.yaml)")
    s.add_argument("--settle", type=float, default=0.5,
                   help="seconds to wait after servo move (default 0.5)")
    s.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
