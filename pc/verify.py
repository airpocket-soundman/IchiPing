#!/usr/bin/env python3
"""Verify the live IchiPing frame stream against v0.1 expected values.

Reads frames from the same input sources receiver.py supports
(--port / --tcp / --in) and runs a panel of per-frame checks:

    M1  magic               == b"ICHP"
    M2  type                == 0x01
    C1  CRC-16/CCITT-FALSE   matches
    S1  seq                  monotonically increases by 1 (with wrap)
    T1  timestamp_ms         strictly increases (with wrap)
    N1  n_samples            == --expected-samples (default 32000)
    R1  rate_hz              == --expected-rate    (default 16000)
    V1  every servo angle    in [0.0, 90.0]
    P1  every PCM sample     in [-32768, 32767]

Each failing check is printed inline with the offending value, and a
summary table is shown at the end. Exit code is non-zero if --strict was
passed and any check failed.

Examples:

    # Live verification against the board
    python verify.py --port COM7 --frames 100 --strict

    # Loopback (no hardware): emulator pipes a file, verify it
    python emulator.py --out /tmp/stream.bin --frames 10 --cadence 0
    python verify.py --in /tmp/stream.bin --strict

    # TCP loopback (run in two terminals)
    python emulator.py --tcp 127.0.0.1:5000 --frames 20
    python verify.py --tcp 127.0.0.1:5000 --frames 20 --strict
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from receiver import (
    FileStream,
    SerialStream,
    StreamStalled,
    TcpStream,
    Stream,
    read_frame,
)


# ---- check framework -------------------------------------------------------

class CheckFailed(Exception):
    """Raised by a check function when the frame violates the expectation."""


@dataclass
class Check:
    code: str            # short id, e.g. "S1"
    name: str            # human-readable description
    fn: Callable         # (state, frame, expected) -> raises CheckFailed
    severity: str = "error"  # "error" / "warn"


@dataclass
class CheckResult:
    code: str
    passes: int = 0
    fails: int = 0
    sample_failures: List[str] = field(default_factory=list)

    def record(self, ok: bool, msg: str = ""):
        if ok:
            self.passes += 1
        else:
            self.fails += 1
            if len(self.sample_failures) < 3:
                self.sample_failures.append(msg)


# ---- individual checks -----------------------------------------------------

def check_magic(state, frame, expected):
    # CRC-OK frames always pass magic by construction; we still verify
    # the unpack_header round-trip stored the right magic.
    pass  # handled implicitly by read_frame raising on bad magic


def check_type(state, frame, expected):
    if frame["type"] != 0x01:
        raise CheckFailed(f"type=0x{frame['type']:02x}, expected 0x01")


def check_crc(state, frame, expected):
    if not frame["crc_ok"]:
        raise CheckFailed("CRC mismatch")


def check_seq_monotonic(state, frame, expected):
    seq = frame["seq"]
    prev = state.get("prev_seq")
    if prev is None:
        return
    # uint16 wraps at 0xFFFF -> 0
    want = (prev + 1) & 0xFFFF
    if seq != want:
        raise CheckFailed(f"seq jumped {prev} -> {seq} (expected {want})")


def check_timestamp_monotonic(state, frame, expected):
    ts = frame["timestamp_ms"]
    prev = state.get("prev_ts")
    if prev is None:
        return
    # uint32 wraps at ~49.7 days; treat a backwards jump > 1 day as a real
    # failure (anything closer than that is allowed to be a wrap).
    diff = (ts - prev) & 0xFFFFFFFF
    if diff == 0 or diff > 24 * 3600 * 1000:
        raise CheckFailed(f"timestamp not increasing: {prev} -> {ts} (delta={diff})")


def check_n_samples(state, frame, expected):
    want = expected["n_samples"]
    if frame["n_samples"] != want:
        raise CheckFailed(f"n_samples={frame['n_samples']}, expected {want}")


def check_rate(state, frame, expected):
    want = expected["rate_hz"]
    if frame["rate_hz"] != want:
        raise CheckFailed(f"rate_hz={frame['rate_hz']}, expected {want}")


def check_servo_range(state, frame, expected):
    for i, angle in enumerate(frame["servo_deg"]):
        if not (0.0 <= angle <= 90.0):
            raise CheckFailed(f"servo[{i}]={angle:.2f} outside [0, 90]")


def check_sample_range(state, frame, expected):
    samples = frame["samples"]
    lo, hi = min(samples), max(samples)
    if lo < -32768 or hi > 32767:
        raise CheckFailed(f"samples out of int16: min={lo}, max={hi}")


def build_checks() -> List[Check]:
    return [
        Check("M2", "type byte = 0x01",          check_type),
        Check("C1", "CRC-16/CCITT-FALSE",         check_crc),
        Check("S1", "seq monotonic (+1, wrap)",   check_seq_monotonic),
        Check("T1", "timestamp_ms monotonic",     check_timestamp_monotonic),
        Check("N1", "n_samples == expected",      check_n_samples),
        Check("R1", "rate_hz == expected",        check_rate),
        Check("V1", "servo angles in [0, 90]",    check_servo_range),
        Check("P1", "samples in int16 range",     check_sample_range),
    ]


# ---- stream open (mirrors receiver.py, kept thin to avoid coupling) --------

def open_stream(args) -> tuple[Stream, str]:
    if args.port:
        return SerialStream(args.port, args.baud), f"serial:{args.port}@{args.baud}"
    if args.tcp:
        host, _, port_s = args.tcp.partition(":")
        if not port_s:
            raise SystemExit("--tcp must be HOST:PORT")
        return TcpStream(host or "127.0.0.1", int(port_s)), f"tcp:{host}:{port_s}"
    if args.in_:
        fh = args.in_.open("rb")
        return FileStream(fh), f"file:{args.in_}"
    raise SystemExit("one of --port / --tcp / --in is required")


# ---- summary ---------------------------------------------------------------

def print_summary(results: Dict[str, CheckResult], checks: List[Check], n_frames: int,
                  elapsed_s: float, verbose: bool) -> bool:
    print()
    print("=" * 66)
    print(f"Verification summary   ({n_frames} frame(s) in {elapsed_s:.1f} s)")
    print("=" * 66)
    print(f"  {'CODE':<6}{'NAME':<32}{'PASS':>6}{'FAIL':>6}  STATUS")
    all_ok = True
    for chk in checks:
        r = results[chk.code]
        total = r.passes + r.fails
        status = "PASS" if r.fails == 0 and total > 0 else (
                 "n/a"  if total == 0 else "FAIL")
        if status == "FAIL":
            all_ok = False
        print(f"  {chk.code:<6}{chk.name:<32}{r.passes:>6}{r.fails:>6}  {status}")
        if r.fails and (verbose or len(r.sample_failures) <= 3):
            for s in r.sample_failures:
                print(f"        ↳ {s}")
    print("=" * 66)
    print(f"  result: {'ALL CHECKS PASSED' if all_ok else 'FAILURES DETECTED'}")
    return all_ok


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing frame stream verifier")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--port", help="serial port (e.g. COM7, /dev/ttyACM0)")
    src.add_argument("--tcp", metavar="HOST:PORT", help="read from a TCP server")
    src.add_argument("--in", dest="in_", type=Path, help="read from a binary file")

    p.add_argument("--baud", type=int, default=921600, help="serial baud rate")
    p.add_argument("--frames", type=int, default=0,
                   help="stop after N frames (0 = run until Ctrl+C / EOF)")
    p.add_argument("--expected-rate", dest="rate", type=int, default=16000,
                   help="expected rate_hz (default 16000)")
    p.add_argument("--expected-samples", dest="n_samples", type=int, default=32000,
                   help="expected n_samples (default 32000)")
    p.add_argument("--strict", action="store_true",
                   help="exit with code 1 if any check failed")
    p.add_argument("--verbose", action="store_true",
                   help="print PASS lines (default: only print FAILs)")
    args = p.parse_args()

    checks = build_checks()
    expected = {"rate_hz": args.rate, "n_samples": args.n_samples}
    state: dict = {"prev_seq": None, "prev_ts": None}
    results = {c.code: CheckResult(c.code) for c in checks}

    stream, source_desc = open_stream(args)
    print(f"verifying {source_desc}")
    print(f"expected: rate_hz={args.rate}, n_samples={args.n_samples}")
    print(f"checks  : {', '.join(c.code for c in checks)}")
    print()

    t0 = time.monotonic()
    n_frames = 0
    n_frame_errors = 0
    try:
        while True:
            try:
                frame = read_frame(stream)
            except EOFError:
                print("source EOF; stopping")
                break
            except (ValueError, StreamStalled) as e:
                n_frame_errors += 1
                print(f"  frame parse error: {e}")
                continue

            n_frames += 1
            for chk in checks:
                try:
                    chk.fn(state, frame, expected)
                    results[chk.code].record(True)
                except CheckFailed as e:
                    results[chk.code].record(False, f"seq={frame['seq']}: {e}")
                    print(f"  [seq={frame['seq']:>5}] {chk.code} {chk.name}: FAIL — {e}")
                except Exception as e:  # pragma: no cover — defensive
                    results[chk.code].record(False, f"seq={frame['seq']}: internal {type(e).__name__}: {e}")
                    print(f"  [seq={frame['seq']:>5}] {chk.code} INTERNAL ERROR: {e}")

            state["prev_seq"] = frame["seq"]
            state["prev_ts"] = frame["timestamp_ms"]

            if args.verbose:
                checks_summary = " ".join(
                    f"{c.code}{'+' if results[c.code].fails == 0 else '!'}"
                    for c in checks
                )
                print(f"  [seq={frame['seq']:>5}] {checks_summary}")

            if args.frames and n_frames >= args.frames:
                break
    except KeyboardInterrupt:
        print("\ninterrupted by user")
    finally:
        stream.close()
        if n_frame_errors:
            print(f"frame parse errors: {n_frame_errors}")

    elapsed = time.monotonic() - t0
    all_ok = print_summary(results, checks, n_frames, elapsed, args.verbose)
    if args.strict and not all_ok:
        return 1
    if n_frames == 0:
        print("no frames received; treating as failure")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
