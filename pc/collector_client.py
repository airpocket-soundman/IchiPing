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
        "label":   "<dir-name>",                  # required
        "pins":    {"AB": 0, ...},                # optional, otherwise CLEAR PINS
        "pattern": "multiband_default",           # optional, name from patterns.yaml
        "volume":  5,                             # optional integer 0..100 percent
        "repeats": 30                             # required
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
from patterns import PatternLibrary, summary as pattern_summary

SERVO_NAMES = ("a", "b", "c", "AB", "BC")   # short physical-mount labels (matches firmware ICHP_SERVO_NAMES)
DEFAULT_PATTERNS_PATH = Path(__file__).resolve().parent / "patterns.yaml"


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
    and ICHP frames, dispatch to either a queue (default, used by plan
    mode) or a direct callback (set by REPL for real-time display).

    Boundary rule (matches the firmware-side encoding):
      - 4-byte sliding window scans for the literal b"ICHP" magic.
      - On match: read the next 32 header bytes, then n_samples * 2
        payload bytes, then 2 CRC bytes; verify CRC; emit Frame.
      - Bytes that do not contribute to a frame are accumulated as ASCII
        until a CR or LF; emit complete lines (no terminator).

    Callback vs queue: if line_callback is set, ASCII lines go straight
    to it (called from this background thread — caller must be thread-
    safe). Otherwise they accumulate in self.lines for foreground get().
    Same split for frames. REPL uses callbacks so MCU responses appear
    immediately rather than waiting for the next user input; plan mode
    uses queues so it can pace step-by-step.
    """

    def __init__(self, ser: serial.Serial):
        super().__init__(daemon=True)
        self.ser = ser
        self.lines: Queue[str] = Queue()
        self.frames: Queue[Frame] = Queue()
        self.line_callback = None      # set to callable(str) for async display
        self.frame_callback = None     # set to callable(Frame) for async save
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
                # 100 ms read timeout fired with no bytes. The
                # 4-byte trailing window is only there to detect ICHP
                # magic that arrives in one piece — the firmware writes
                # frames atomically, so a stalled window can no longer be
                # the start of a frame. Drain it through the ASCII pipe
                # so trailing "\r\n" of an MCU response finally triggers
                # _flush_line. Without this, the last 4 bytes of every
                # response sit in the window until the user types
                # another command and shifts them out.
                while window:
                    self._flush_one_ascii(window.pop(0))
                continue
            for b in chunk:
                window.append(b)
                if len(window) > 4:
                    self._flush_one_ascii(window.pop(0))
                if bytes(window) == MAGIC:
                    self._flush_line()
                    window.clear()
                    self._read_frame_body()
            # Whatever remains in the window may include the tail of a
            # short ASCII line ("...01\r\n" with len ≤ 4). The next
            # read-timeout iteration above will drain it.

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
                if self.line_callback is not None:
                    self.line_callback(line)
                else:
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
        frame = Frame(
            seq=seq,
            timestamp_ms=ts,
            rate_hz=rate,
            n_samples=n_samp,
            servo_deg=tuple(servo),
            samples=payload,
            crc_ok=(expected == got),
        )
        if self.frame_callback is not None:
            self.frame_callback(frame)
        else:
            self.frames.put(frame)


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


def wait_for_ack(reader: StreamReader, timeout: float = 2.0) -> Optional[str]:
    """Drain `reader.lines` until one starts with 'OK' or 'ERR'. Used for
    pacing the PAT push so the LPUART RX FIFO never overflows."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = reader.lines.get(timeout=0.2)
        except Empty:
            continue
        print(f"  < {line}")
        if line.startswith("OK") or line.startswith("ERR"):
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
    pattern: Optional[str] = None    # name from patterns.yaml
    volume: Optional[int] = None     # 0..100 percent


def load_plan(path: Path) -> list[PlanStep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    steps: list[PlanStep] = []
    for i, entry in enumerate(data):
        try:
            steps.append(PlanStep(
                label=entry["label"],
                repeats=int(entry["repeats"]),
                pins={k: float(v) for k, v in entry.get("pins", {}).items()},
                pattern=entry.get("pattern"),
                volume=(int(entry["volume"]) if "volume" in entry else None),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"plan step {i} malformed: {exc}")
    return steps


def run_plan(plan: list[PlanStep], ser: serial.Serial, reader: StreamReader,
             saver: CaptureSaver, lib: PatternLibrary) -> None:
    for step in plan:
        print(f"\n=== step: label={step.label} repeats={step.repeats} ===")
        if step.volume is not None:
            send(ser, f"SET VOLUME {step.volume}")
            wait_for_prefix(reader, "OK", timeout=2)
        if step.pattern is not None:
            try:
                idx, _ = lib.find(step.pattern)
            except KeyError as exc:
                raise SystemExit(f"plan step {step.label!r}: {exc}")
            send(ser, f"PAT SELECT {idx}")
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
  GET CONFIG / GET HOME / GET OPEN / GET PINS
  SET VOLUME <0..100>          (integer percent)
  SET REPEATS <N>
  SET PIN <servo> <deg>        /  CLEAR PIN <servo>  /  CLEAR PINS
  SET HOME <servo> <deg>       /  SET OPEN <servo> <deg>  /  SAVE HOME
  SERVO <servo> <deg>          (move, 0.3 s, auto-OFF)  /  SERVO <servo> OFF  /  SERVO ALL OFF
  OPEN <servo>                 /  CLOSE <servo>      (move to open/home, 0.3 s, auto-OFF)
  OPEN ALL                     (a→b→c→AB→BC, 0.3 s each)
  CLOSE ALL                    (BC→AB→c→b→a reverse, 0.3 s each)
  PAT INFO                     /  PAT SELECT <idx>
  EMIT <idx>                   (test-play current pattern, no recording)
  RUN                          /  STOP

Pattern playback test:
  EMIT <idx>         Play pattern at that index once (use :patterns for idx)
                     The REPL blocks on the MCU's OK EMIT reply so back-to-
                     back EMITs don't race during the play.

Local helpers (MUST start with ":" — keeps the MCU verb space clean):

  :label <name>      Set the capture label (frames go to <out>/<name>/)
  :patterns          List patterns loaded from patterns.yaml (PC cache)
  :select <name|idx> PAT SELECT helper (resolves name to idx, sets RUN source)
  :reload            Re-read patterns.yaml and re-push to the MCU
  :help              Show this help
  :quit  /  :exit    Exit

Servos: a b c AB BC   (windows: a b c, doors: AB BC; case-insensitive)
"""


def _emit_blocking(ser: "serial.Serial", lib: "PatternLibrary",
                   reader: "StreamReader", idx: int) -> None:
    """Send EMIT <idx> and block until the MCU's OK EMIT reply (or timeout).

    The block prevents a follow-up command from racing into the LPUART RX
    FIFO while the MCU is stuck in sai_speaker_play_blocking; without it
    the trailing CR/LF of the next command gets dropped and the firmware
    desynchronises. Detaches reader.line_callback so wait_for_ack can read
    from the queue, then restores it."""
    # Compute timeout from the cached pattern duration if known, otherwise
    # default to the firmware's max window (2 s) plus margin.
    timeout = 4.0
    if 0 <= idx < len(lib.patterns):
        timeout = max(2.0, lib.patterns[idx].total_ms() / 1000.0 + 1.0)
    saved_cb = reader.line_callback
    reader.line_callback = None
    try:
        send(ser, f"EMIT {idx}")
        wait_for_ack(reader, timeout=timeout)
    finally:
        reader.line_callback = saved_cb


def _handle_local(cmd: str, saver: "CaptureSaver",
                  ser: "serial.Serial", lib: "PatternLibrary",
                  reader: "StreamReader") -> str:
    """Try to handle `cmd` as a local REPL command.

    Returns one of:
      "handled" — cmd was local and we processed it; caller should continue
      "quit"    — user asked to exit; caller should return
      "forward" — cmd is not local; caller should send it to the MCU
    """
    if not cmd.startswith(":"):
        return "forward"

    head, *rest = cmd[1:].split(maxsplit=1)
    head = head.lower()

    if head in ("quit", "exit"):
        return "quit"
    if head == "help":
        print(REPL_HELP)
        return "handled"
    if head == "label":
        label = rest[0].strip() if rest else ""
        if not label:
            print("  usage: :label <name>")
            return "handled"
        saver.set_label(label)
        print(f"  label set to {label!r} (next frames -> <out>/{label}/)")
        return "handled"
    if head == "patterns":
        if not lib.patterns:
            print("  (no patterns loaded — check patterns.yaml)")
        else:
            for i, p in enumerate(lib.patterns):
                print(f"  [{i}] {pattern_summary(p)}")
        return "handled"
    if head == "select":
        key = rest[0].strip() if rest else ""
        if not key:
            print("  usage: :select <name|idx>")
            return "handled"
        try:
            idx, p = lib.find(key)
        except KeyError as exc:
            print(f"  {exc}")
            return "handled"
        # Block on the OK PAT select reply (same rationale as EMIT).
        print(f"  -> PAT SELECT {idx}  ({p.name})")
        saved_cb = reader.line_callback
        reader.line_callback = None
        try:
            send(ser, f"PAT SELECT {idx}")
            wait_for_ack(reader, timeout=2.0)
        finally:
            reader.line_callback = saved_cb
        return "handled"
    if head == "reload":
        try:
            lib.reload()
        except Exception as exc:
            print(f"  reload failed: {exc}")
            return "handled"
        print(f"  patterns.yaml reloaded ({len(lib.patterns)} entries); pushing to MCU...")
        # Temporarily detach the REPL line callback so wait_for_ack can see
        # the OK replies via reader.lines. Without this the callback steals
        # the lines and the wait would time out, leaving us without flow
        # control on the push (which overflows the LPUART RX FIFO).
        saved_cb = reader.line_callback
        reader.line_callback = None
        try:
            lib.push(
                send_line=lambda line: send(ser, line),
                wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
                log=lambda line: print(f"  > {line}"),
            )
            if lib.patterns:
                send(ser, "PAT SELECT 0")
                wait_for_ack(reader, timeout=2.0)
        finally:
            reader.line_callback = saved_cb
        print(f"  reload complete. use :patterns to verify.")
        return "handled"

    print(f"  unknown local command: {cmd}")
    return "handled"


def run_repl(ser: serial.Serial, reader: StreamReader, saver: CaptureSaver,
             lib: PatternLibrary) -> None:
    print(REPL_HELP)

    # Print lock so async background prints (MCU lines, saved frames) do
    # not interleave with one another mid-line.
    out_lock = threading.Lock()

    # Auto re-push patterns when the MCU emits its ready banner mid-session
    # (i.e. after a reset / reflash). Without this the user sees PAT INFO
    # count=0 and has to run :reload manually. Guard with a non-blocking
    # lock so we never queue a second push while one is in flight.
    auto_push_lock = threading.Lock()

    def trigger_auto_push() -> None:
        if not auto_push_lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                with out_lock:
                    sys.stdout.write("\n  ! MCU reset — re-pushing patterns...\n")
                    sys.stdout.flush()
                # Let the rest of the boot banner (and any OK lines from a
                # racing PAT command, if any) drain before we take the queue.
                time.sleep(0.3)
                saved_cb = reader.line_callback
                reader.line_callback = None
                try:
                    lib.push(
                        send_line=lambda line: send(ser, line),
                        wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
                        log=None,
                    )
                    if lib.patterns:
                        send(ser, "PAT SELECT 0")
                        wait_for_ack(reader, timeout=2.0)
                finally:
                    reader.line_callback = saved_cb
                with out_lock:
                    sys.stdout.write(
                        f"  ! re-pushed {len(lib.patterns)} patterns; PAT SELECT 0\n> ")
                    sys.stdout.flush()
            finally:
                auto_push_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def on_line(line: str) -> None:
        # Detect the boot-complete banner that the firmware emits at the
        # very end of init ("INFO IchiPing 09_collector ready"). The "BOOT"
        # lines earlier in boot start with "INFO BOOT" so this prefix only
        # matches the ready signal.
        if line.startswith("INFO IchiPing"):
            trigger_auto_push()
        # `\n` ensures the response starts on a fresh line even if the
        # user has typed a partial command; their typing is unaffected
        # (still in the readline buffer) but visually scrolls.
        with out_lock:
            sys.stdout.write(f"\n  < {line}\n> ")
            sys.stdout.flush()

    def on_frame(frame: Frame) -> None:
        with out_lock:
            if not frame.crc_ok:
                sys.stdout.write(f"\n  ! frame seq={frame.seq} CRC BAD\n> ")
            else:
                path = saver.save(frame)
                sys.stdout.write(f"\n  > saved {path.name} (seq={frame.seq})\n> ")
            sys.stdout.flush()

    # Wire the reader to push lines + frames straight to stdout in real
    # time, instead of buffering them in the queue. The queue path is
    # still used by plan mode (which does its own pacing).
    reader.line_callback = on_line
    reader.frame_callback = on_frame

    try:
        while True:
            try:
                cmd = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not cmd:
                continue
            decision = _handle_local(cmd, saver, ser, lib, reader)
            if decision == "quit":
                return
            if decision == "handled":
                continue
            # decision == "forward"
            # Special-case EMIT <idx>: the MCU enters sai_speaker_play_blocking
            # for ~1-2 s and stops polling UART RX during the play. Without a
            # block here a follow-up command races into the RX FIFO and gets
            # its CR/LF clipped (the FIFO is 8 bytes deep on MCXN947 LPUART).
            # Other MCU commands are quick enough to pass through normally.
            parts = cmd.split()
            if (len(parts) == 2 and parts[0].upper() == "EMIT"
                    and parts[1].lstrip("-").isdigit()):
                _emit_blocking(ser, lib, reader, int(parts[1]))
            else:
                send(ser, cmd)
            # on_line will print MCU responses asynchronously when they
            # arrive, including the fresh "> " prompt for the next command.
    finally:
        # Detach callbacks so a subsequent plan run (if any) can use the
        # queue path again.
        reader.line_callback = None
        reader.frame_callback = None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_oneshot(ser: serial.Serial, reader: StreamReader,
                commands: list[str], timeout: float = 5.0) -> int:
    """Send a sequence of wire-protocol commands, one per line; wait for
    each ack (or RUN-done block). Designed for non-interactive scripting
    (AI agents, shell pipelines) where no REPL prompt is needed.

    Returns 0 on success, 2 on any command that produced ERR or timed out.
    Each line is sent verbatim; no local REPL helpers (:label, :open) are
    recognised — only the wire protocol verbs documented in
    docs/collector_protocol.md / ichp_cmd.h.

    Special-case: a command that returns ``OK RUN started`` is followed
    by zero or more ICHP frames (dropped here; use the calibrator
    subcommands or the REPL for frame capture) until ``OK RUN done`` or
    ``OK RUN aborted``.
    """
    exit_code = 0
    for raw in commands:
        cmd = raw.strip()
        if not cmd or cmd.startswith("#"):
            continue
        print(f"> {cmd}")
        send(ser, cmd)
        ack = wait_for_ack(reader, timeout=timeout)
        if ack is None:
            print(f"  FAIL: no ack in {timeout:.1f} s for `{cmd}`", file=sys.stderr)
            exit_code = 2
            continue
        if ack.startswith("OK RUN started"):
            # Drain frames until RUN done/aborted. Frames go through
            # reader.frames; we just consume the ASCII line that ends the run.
            end = wait_for_prefix(reader, "OK RUN ", timeout=60.0)
            if end is None:
                print("  FAIL: RUN block timed out", file=sys.stderr)
                exit_code = 2
            # Drain any remaining frames from the queue without saving.
            while True:
                try:
                    reader.frames.get_nowait()
                except Empty:
                    break
        elif ack.startswith("ERR"):
            exit_code = 2
    return exit_code


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
    p.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS_PATH,
                   help="YAML file describing the excitation pattern library "
                        "(default: pc/patterns.yaml)")
    p.add_argument("--once", action="append", default=None,
                   help="send a single wire-protocol command, wait for ack, exit. "
                        "Repeatable; commands run in order. Skips REPL/plan. "
                        "Example: --once 'EQ DISABLE' --once 'PAT NOISE w 3000 30 0'")
    p.add_argument("--script", type=Path, default=None,
                   help="read wire-protocol commands from FILE (one per line, "
                        "'#' comments OK), execute non-interactively, exit. "
                        "Mutually exclusive with --plan / REPL.")
    p.add_argument("--no-push-patterns", action="store_true",
                   help="skip auto-pushing pc/patterns.yaml at startup. Useful "
                        "for --once/--script flows that register patterns themselves")
    args = p.parse_args()

    try:
        lib = PatternLibrary.load_yaml(args.patterns)
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL loading {args.patterns}: {exc}", file=sys.stderr)
        return 2

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
    print(f"loaded {len(lib.patterns)} patterns from {args.patterns}")

    # Wait for the MCU boot to finish before pushing patterns. The firmware
    # emits "INFO IchiPing 09_collector ready" at the very end of init; up
    # to that point the command loop is not yet running and any PAT lines
    # we send sit in the RX FIFO until they overflow it, losing CR/LF and
    # leaving the firmware out of sync. Opening the OpenSDA port can also
    # toggle DTR and reset the MCU, so we must always honour this gate.
    print("waiting for MCU boot...")
    ready = wait_for_prefix(reader, "INFO IchiPing", timeout=6.0)
    if ready is None:
        print("  (no boot banner seen in 6 s; assuming MCU was already up)")

    # Push the YAML library to the MCU. After reset the firmware's pattern
    # library is empty, so RUN won't work until this completes. Pace each
    # command with wait_for_ack — without it the MCU's TX echo outlasts the
    # incoming byte rate and overflows the LPUART RX FIFO.
    #
    # Skipped when --no-push-patterns or when running --once / --script
    # (those typically manage patterns themselves and pushing the YAML lib
    # first would clobber their state).
    skip_push = args.no_push_patterns or args.once or args.script
    if not skip_push:
        def _push_log(line: str) -> None:
            print(f"  > {line}")
        lib.push(
            send_line=lambda line: send(ser, line),
            wait_ack=lambda: wait_for_ack(reader, timeout=2.0),
            log=_push_log,
        )
        # Auto-select pattern 0 so RUN works without an explicit :select.
        if lib.patterns:
            send(ser, "PAT SELECT 0")
            wait_for_ack(reader, timeout=2.0)

    try:
        if args.once or args.script:
            commands: list[str] = []
            if args.once:
                commands.extend(args.once)
            if args.script:
                commands.extend(args.script.read_text(encoding="utf-8").splitlines())
            return run_oneshot(ser, reader, commands)
        elif args.plan:
            plan = load_plan(args.plan)
            run_plan(plan, ser, reader, saver, lib)
        else:
            run_repl(ser, reader, saver, lib)
    finally:
        reader.stop()
        saver.close()
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
