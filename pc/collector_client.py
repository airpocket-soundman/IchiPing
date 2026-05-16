"""IchiPing 09_collector — PC-side client.

REPL + plan execution + manual servo + label-aware capture saver. Talks
to firmware/projects/09_collector over the OpenSDA UART, multiplexing
ASCII command/response lines with ICHP binary frames on the same wire.

Wire protocol: see firmware/shared/include/ichp_cmd.h. Frame format:
pc/ichp_frame.py + firmware/shared/include/ichiping_frame.h.

Usage
-----
Interactive REPL:

    python collector_client.py --port COM7 --out ../captures

Plan-driven (JSON list of steps):

    python collector_client.py --port COM7 --plan plan.json --out ../captures

Each plan step supports:
    {
        "label":      "<dir-name>",        # required
        "pins":       {"door_AB": 0, ...}, # optional, otherwise CLEAR PINS
        "excitation": "multiband",         # optional, default current
        "volume":     0.05,                # optional, default current
        "repeats":    30                   # required
    }

Saves WAVs to <out>/<label>/frame_NNNNNN.wav with one CSV row in
<out>/<label>/labels.csv per accepted frame.
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial", file=sys.stderr)
    sys.exit(2)

from ichp_frame import (
    MAGIC,
    HEADER_FMT,
    HEADER_SIZE,
    CRC_SIZE,
    crc16_ccitt,
)

SERVO_NAMES = ("window_a", "window_b", "window_c", "door_AB", "door_BC")
EXCITATIONS = ("chirp", "multiband", "silence")


# ---------------------------------------------------------------------------
# Multiplexed stream reader
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    seq: int
    timestamp_ms: int
    rate_hz: int
    n_samples: int
    servo_deg: tuple
    samples: bytes
    crc_ok: bool


class StreamReader(threading.Thread):
    """Background thread: read bytes from serial, split into ASCII lines
    and ICHP frames, push to queues for the foreground REPL / plan runner.

    Boundary rule (matches the firmware-side encoding):
      - 4-byte sliding window scans for the literal b"ICHP" magic.
      - On match: read the next 32 header bytes, then n_samples * 2
        payload bytes, then 2 CRC bytes; verify CRC; emit Frame.
      - Bytes that do not contribute to a frame are accumulated as ASCII
        until a CR or LF; emit complete lines (no terminator).
    """

    def __init__(self, ser: serial.Serial):
        super().__init__(daemon=True)
        self.ser = ser
        self.lines: Queue[str] = Queue()
        self.frames: Queue[Frame] = Queue()
        self._stop = threading.Event()
        self._line_buf = bytearray()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        window = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(1024)
            except serial.SerialException:
                break
            if not chunk:
                continue
            for b in chunk:
                window.append(b)
                if len(window) > 4:
                    self._flush_one_ascii(window.pop(0))
                if bytes(window) == MAGIC:
                    self._flush_line()
                    window.clear()
                    self._read_frame_body()
            # Whatever remains in the window is not a magic prefix; let
            # the next iteration roll it through.

    def _flush_one_ascii(self, b: int) -> None:
        c = bytes([b])
        if c in (b"\r", b"\n"):
            self._flush_line()
        else:
            self._line_buf.extend(c)

    def _flush_line(self) -> None:
        if self._line_buf:
            try:
                line = self._line_buf.decode("utf-8", errors="replace").rstrip()
            finally:
                self._line_buf.clear()
            if line:
                self.lines.put(line)

    def _read_n(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n and not self._stop.is_set():
            chunk = self.ser.read(n - len(buf))
            if not chunk:
                continue
            buf.extend(chunk)
        return bytes(buf)

    def _read_frame_body(self) -> None:
        # Header without the magic we already consumed.
        remainder = self._read_n(HEADER_SIZE - 4)
        if len(remainder) < HEADER_SIZE - 4:
            return
        header_bytes = bytes(MAGIC) + remainder
        try:
            magic, type_, _rsv, seq, ts, n_samp, rate, *servo = struct.unpack(
                HEADER_FMT, header_bytes
            )
        except struct.error:
            return
        payload_bytes = n_samp * 2
        payload = self._read_n(payload_bytes)
        crc_bytes = self._read_n(CRC_SIZE)
        if len(payload) < payload_bytes or len(crc_bytes) < CRC_SIZE:
            return
        expected = crc16_ccitt(header_bytes + payload)
        got = crc_bytes[0] | (crc_bytes[1] << 8)
        self.frames.put(
            Frame(
                seq=seq,
                timestamp_ms=ts,
                rate_hz=rate,
                n_samples=n_samp,
                servo_deg=tuple(servo),
                samples=payload,
                crc_ok=(expected == got),
            )
        )


# ---------------------------------------------------------------------------
# Capture saver
# ---------------------------------------------------------------------------

class CaptureSaver:
    """Write incoming frames to <out>/<label>/frame_NNNNNN.wav + labels.csv.
    Label is set externally per plan step or via set_label() in REPL."""

    def __init__(self, out_root: Path):
        self.out_root = out_root
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.label: Optional[str] = None
        self.counters: dict[str, int] = {}
        self._csv_handles: dict[str, csv.writer] = {}
        self._csv_files: dict[str, "object"] = {}

    def set_label(self, label: str) -> None:
        self.label = label

    def save(self, frame: Frame) -> Path:
        label = self.label or "unlabeled"
        ldir = self.out_root / label
        ldir.mkdir(parents=True, exist_ok=True)
        idx = self.counters.get(label, 0)
        self.counters[label] = idx + 1

        wav_path = ldir / f"frame_{idx:06d}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(frame.rate_hz)
            wf.writeframes(frame.samples)

        if label not in self._csv_handles:
            csv_path = ldir / "labels.csv"
            new = not csv_path.exists()
            f = csv_path.open("a", newline="", encoding="utf-8")
            self._csv_files[label] = f
            w = csv.writer(f)
            if new:
                w.writerow([
                    "seq", "ts_ms", "rate_hz", "n_samples",
                    *SERVO_NAMES, "wav", "crc_ok",
                ])
            self._csv_handles[label] = w

        self._csv_handles[label].writerow([
            frame.seq, frame.timestamp_ms, frame.rate_hz, frame.n_samples,
            *(f"{v:.1f}" for v in frame.servo_deg),
            wav_path.name, int(frame.crc_ok),
        ])
        self._csv_files[label].flush()
        return wav_path

    def close(self) -> None:
        for f in self._csv_files.values():
            try:
                f.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------

def send(ser: serial.Serial, line: str) -> None:
    ser.write((line + "\r\n").encode("utf-8"))


def wait_for_prefix(reader: StreamReader, prefix: str, timeout: float = 5.0) -> Optional[str]:
    """Drain `reader.lines` until one starts with `prefix`. Returns it or None."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = reader.lines.get(timeout=0.2)
        except Empty:
            continue
        print(f"  < {line}")
        if line.startswith(prefix):
            return line
    return None


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    label: str
    repeats: int
    pins: dict = field(default_factory=dict)
    excitation: Optional[str] = None
    volume: Optional[float] = None


def load_plan(path: Path) -> list[PlanStep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    steps: list[PlanStep] = []
    for i, entry in enumerate(data):
        try:
            steps.append(PlanStep(
                label=entry["label"],
                repeats=int(entry["repeats"]),
                pins={k: float(v) for k, v in entry.get("pins", {}).items()},
                excitation=entry.get("excitation"),
                volume=(float(entry["volume"]) if "volume" in entry else None),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"plan step {i} malformed: {exc}")
    return steps


def run_plan(plan: list[PlanStep], ser: serial.Serial, reader: StreamReader,
             saver: CaptureSaver) -> None:
    for step in plan:
        print(f"\n=== step: label={step.label} repeats={step.repeats} ===")
        if step.volume is not None:
            send(ser, f"SET VOLUME {step.volume}")
            wait_for_prefix(reader, "OK", timeout=2)
        if step.excitation is not None:
            if step.excitation not in EXCITATIONS:
                raise SystemExit(f"unknown excitation: {step.excitation}")
            send(ser, f"SET EXCITATION {step.excitation}")
            wait_for_prefix(reader, "OK", timeout=2)
        send(ser, "CLEAR PINS")
        wait_for_prefix(reader, "OK", timeout=2)
        for sname, deg in step.pins.items():
            if sname not in SERVO_NAMES:
                raise SystemExit(f"unknown servo: {sname}")
            send(ser, f"SET PIN {sname} {deg}")
            wait_for_prefix(reader, "OK", timeout=2)
        send(ser, f"SET REPEATS {step.repeats}")
        wait_for_prefix(reader, "OK", timeout=2)

        saver.set_label(step.label)
        send(ser, f"INFO label={step.label}")  # echo-only, not parsed by MCU
        send(ser, "RUN")
        wait_for_prefix(reader, "OK RUN started", timeout=5)

        # Pump frames until "OK RUN done" / "OK RUN aborted".
        done = False
        while not done:
            try:
                frame = reader.frames.get(timeout=0.2)
                if not frame.crc_ok:
                    print(f"  ! frame seq={frame.seq} CRC BAD, skipping")
                    continue
                path = saver.save(frame)
                print(f"  > saved {path.name} (seq={frame.seq})")
            except Empty:
                pass
            try:
                line = reader.lines.get_nowait()
                print(f"  < {line}")
                if line.startswith("OK RUN done") or line.startswith("OK RUN aborted"):
                    done = True
            except Empty:
                pass


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

REPL_HELP = """
Commands forwarded to the MCU (case-insensitive verb):

  PING
  GET CONFIG / GET HOME / GET PINS
  SET VOLUME <0..1>
  SET EXCITATION chirp|multiband|silence
  SET REPEATS <N>
  SET PIN <servo> <deg>     /  CLEAR PIN <servo>  /  CLEAR PINS
  SET HOME <servo> <deg>    /  SAVE HOME
  SERVO <servo> <deg>       /  SERVO ALL OFF
  RUN                       /  STOP

Local helpers (do not reach the MCU):

  :label <name>     Set the capture label (frames go to <out>/<name>/)
  :help             Show this help
  :quit             Exit

Servos: window_a window_b window_c door_AB door_BC
"""


def run_repl(ser: serial.Serial, reader: StreamReader, saver: CaptureSaver) -> None:
    print(REPL_HELP)

    def drain():
        while True:
            try:
                line = reader.lines.get_nowait()
                print(f"  < {line}")
            except Empty:
                break
            try:
                frame = reader.frames.get_nowait()
                if not frame.crc_ok:
                    print(f"  ! frame seq={frame.seq} CRC BAD")
                else:
                    path = saver.save(frame)
                    print(f"  > saved {path.name} (seq={frame.seq})")
            except Empty:
                pass

    while True:
        drain()
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not cmd:
            continue
        if cmd.startswith(":"):
            head, *rest = cmd[1:].split(maxsplit=1)
            head = head.lower()
            if head == "quit" or head == "exit":
                return
            if head == "help":
                print(REPL_HELP)
                continue
            if head == "label":
                label = rest[0] if rest else ""
                if not label:
                    print("  usage: :label <name>")
                    continue
                saver.set_label(label)
                print(f"  label set to {label!r} (next frames -> <out>/{label}/)")
                continue
            print(f"  unknown local command: {cmd}")
            continue
        send(ser, cmd)
        # Brief drain wave so the response shows up before the next prompt.
        time.sleep(0.15)
        drain()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing 09_collector client")
    p.add_argument("--port", required=True, help="serial port (e.g. COM7 or /dev/ttyACM0)")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--out", type=Path, default=Path("./captures"),
                   help="output root directory (per-label subdirs are created)")
    p.add_argument("--plan", type=Path, default=None,
                   help="JSON plan file; if omitted, run interactive REPL")
    p.add_argument("--label", default=None,
                   help="initial label for REPL (default: 'unlabeled')")
    args = p.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"FAIL opening {args.port}: {exc}", file=sys.stderr)
        return 2

    reader = StreamReader(ser)
    reader.start()
    saver = CaptureSaver(args.out)
    if args.label:
        saver.set_label(args.label)

    print(f"connected {args.port} @ {args.baud} bps, output -> {args.out}")

    try:
        if args.plan:
            plan = load_plan(args.plan)
            run_plan(plan, ser, reader, saver)
        else:
            run_repl(ser, reader, saver)
    finally:
        reader.stop()
        saver.close()
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
