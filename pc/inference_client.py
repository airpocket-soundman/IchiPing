"""IchiPing 10_inference — PC-side monitor (separate from test tools).

Read-only companion to firmware/projects/10_inference. The device runs
autonomously (TFT shows the classification result); this script tails the
debug UART and pretty-prints the machine-readable RESULT lines, optionally
logging to CSV for offline accuracy evaluation.

Distinct from:
  - receiver.py / verify.py / emulator.py  → ICHP audio-frame plumbing (test path)
  - collector_client.py                    → bidirectional data-collection REPL (training data path)

Why a separate tool: this one talks to the *inference* firmware which:
  - never emits ICHP binary frames (audio stays on-device)
  - emits ASCII RESULT / INFO lines at 115200 bps (not 921600)
  - takes no commands (read-only monitor)

Usage
-----
Pretty-print every inference result as it arrives:

    python inference_client.py --port COM7

Also log to CSV (one row per inference) for offline analysis:

    python inference_client.py --port COM7 --csv ../runs/inference_log.csv

Optionally write each result with its wall-clock label by typing the
ground-truth label on stdin (`:label door_closed` etc.) — useful when
manually moving the model between fixed states to measure per-class
accuracy without modifying the firmware.

Output format from firmware (one line per inference):

    INFO IchiPing 10_inference build May 16 2026 18:42:11
    INFO classes: door_closed door_half door_open amb_silence
    INFO state=RUNNING
    RESULT seq=1 class=door_closed prob=0.812 cap_ms=2010 infer_ms=4

Lines starting with INFO are surfaced verbatim; RESULT lines are parsed.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: uv sync (or pip install pyserial)",
          file=sys.stderr)
    sys.exit(2)


RESULT_RE = re.compile(
    r"RESULT\s+"
    r"seq=(?P<seq>\d+)\s+"
    r"class=(?P<cls>\S+)\s+"
    r"prob=(?P<prob>[-+0-9.eE]+)\s+"
    r"cap_ms=(?P<cap>\d+)\s+"
    r"infer_ms=(?P<inf>\d+)"
)


@dataclass
class Result:
    seq: int
    class_name: str
    prob: float
    cap_ms: int
    infer_ms: int
    received_at: datetime


# ---------------------------------------------------------------------------
# Reader thread
# ---------------------------------------------------------------------------

class LineReader(threading.Thread):
    def __init__(self, ser: serial.Serial):
        super().__init__(daemon=True)
        self.ser = ser
        self.lines: Queue[str] = Queue()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except serial.SerialException:
                break
            if not chunk:
                continue
            for b in chunk:
                if b in (0x0A, 0x0D):
                    if buf:
                        try:
                            line = buf.decode("utf-8", errors="replace").rstrip()
                        finally:
                            buf.clear()
                        if line:
                            self.lines.put(line)
                else:
                    buf.append(b)


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------

def parse_result(line: str) -> Optional[Result]:
    m = RESULT_RE.match(line)
    if not m:
        return None
    return Result(
        seq=int(m.group("seq")),
        class_name=m.group("cls"),
        prob=float(m.group("prob")),
        cap_ms=int(m.group("cap")),
        infer_ms=int(m.group("inf")),
        received_at=datetime.now(),
    )


def fmt_result(r: Result, current_label: Optional[str]) -> str:
    """One-liner for stdout. Highlights mismatch when a ground-truth label is set."""
    correct = ""
    if current_label is not None:
        correct = "  ✓" if current_label == r.class_name else f"  ✗ (truth={current_label})"
    return (f"[{r.received_at.strftime('%H:%M:%S')}] "
            f"seq={r.seq:4d}  {r.class_name:<14s}  p={r.prob:.2f}  "
            f"cap={r.cap_ms}ms infer={r.infer_ms}ms{correct}")


class CsvLogger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._f = path.open("a", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow([
                "wall_ts", "seq", "predicted", "truth_label",
                "prob", "cap_ms", "infer_ms",
            ])

    def append(self, r: Result, truth: Optional[str]) -> None:
        self._w.writerow([
            r.received_at.isoformat(timespec="milliseconds"),
            r.seq, r.class_name, truth if truth is not None else "",
            f"{r.prob:.4f}", r.cap_ms, r.infer_ms,
        ])
        self._f.flush()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stdin label helper
# ---------------------------------------------------------------------------

class LabelInput(threading.Thread):
    """Read stdin for `:label <name>` lines and update the shared ground-truth."""

    def __init__(self):
        super().__init__(daemon=True)
        self.current: Optional[str] = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        print("(type `:label <name>` to tag subsequent results; `:label none` to clear)")
        try:
            for line in sys.stdin:
                if self._stop.is_set():
                    return
                line = line.strip()
                if not line.startswith(":"):
                    continue
                parts = line[1:].split(maxsplit=1)
                if not parts:
                    continue
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""
                if cmd == "label":
                    self.current = None if arg in ("", "none") else arg
                    print(f"-- truth label: {self.current or '(none)'}")
                elif cmd in ("quit", "exit"):
                    self._stop.set()
                    return
        except (EOFError, KeyboardInterrupt):
            return


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing 10_inference monitor")
    p.add_argument("--port", required=True, help="serial port, e.g. COM7 or /dev/ttyACM0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--csv", type=Path, default=None,
                   help="append each inference result to this CSV")
    p.add_argument("--max", type=int, default=0,
                   help="exit after N results (0 = unlimited)")
    args = p.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"FAIL opening {args.port}: {exc}", file=sys.stderr)
        return 2

    reader = LineReader(ser); reader.start()
    label_in = LabelInput(); label_in.start()
    logger = CsvLogger(args.csv) if args.csv else None

    print(f"connected {args.port} @ {args.baud} bps")
    if args.csv:
        print(f"logging to {args.csv}")

    count = 0
    try:
        while True:
            try:
                line = reader.lines.get(timeout=0.5)
            except Empty:
                continue
            if line.startswith("RESULT"):
                r = parse_result(line)
                if r is None:
                    print(f"?? could not parse: {line}")
                    continue
                print(fmt_result(r, label_in.current))
                if logger is not None:
                    logger.append(r, label_in.current)
                count += 1
                if args.max and count >= args.max:
                    print(f"reached --max {args.max}, exiting")
                    break
            elif line.startswith("INFO") or line.startswith("ERR"):
                print(f"  {line}")
            else:
                # Stray text (e.g. from PRINTF lines we don't recognise)
                print(f"  | {line}")
    except KeyboardInterrupt:
        print()
    finally:
        reader.stop()
        label_in.stop()
        if logger:
            logger.close()
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
