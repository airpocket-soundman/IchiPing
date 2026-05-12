#!/usr/bin/env python3
"""IchiPing frame receiver.

Reads framed audio data emitted by the MCU firmware skeleton
(firmware/projects/01_dummy_emitter/main.c) and saves each frame as:

  - WAV  : <out>/frame_<seq>.wav     (16-bit mono PCM at frame's sample_rate)
  - CSV  : <out>/labels.csv          (one row per frame; servo angles + meta)

Frame format is defined in firmware/shared/include/ichiping_frame.h and
pc/ichp_frame.py (PC-side mirror). Keep them in sync — the unittest in
pc/test_frame_format.py guards against drift.

Input sources (mutually exclusive):
  --port COM7          read from a serial port (production use)
  --tcp HOST:PORT      read from a TCP server (used with emulator.py --tcp)
  --in PATH            read from a file (offline replay or fuzz fodder)

Usage:
  python receiver.py --port COM7 --baud 921600 --out ../captures
  python receiver.py --tcp 127.0.0.1:5000 --out ../captures --max-frames 5
  python receiver.py --in fake_stream.bin --out ../captures
"""
from __future__ import annotations

import argparse
import csv
import socket
import struct
import sys
import time
import wave
from pathlib import Path
from typing import BinaryIO, Optional, Tuple

from ichp_frame import (
    MAGIC,
    HEADER_SIZE,
    crc16_ccitt,
    unpack_header,
)


# ---- input source abstraction ---------------------------------------------

class Stream:
    """Minimal `read(n) -> bytes` interface; returns b"" only at true EOF."""

    def read(self, n: int) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SerialStream(Stream):
    def __init__(self, port: str, baud: int):
        import serial  # imported lazily so non-serial sources work without pyserial
        self.ser = serial.Serial(port, baud, timeout=0.5)

    def read(self, n: int) -> bytes:
        return self.ser.read(n)

    def close(self) -> None:
        self.ser.close()


class TcpStream(Stream):
    def __init__(self, host: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock.settimeout(0.5)

    def read(self, n: int) -> bytes:
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b""

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class FileStream(Stream):
    """Wraps a binary file. EOF is signalled via raising EOFError, so the
    rest of the receiver loop can break cleanly."""

    def __init__(self, fh: BinaryIO):
        self.fh = fh

    def read(self, n: int) -> bytes:
        chunk = self.fh.read(n)
        if not chunk:
            raise EOFError("end of file")
        return chunk

    def close(self) -> None:
        self.fh.close()


# ---- framing ---------------------------------------------------------------

class StreamStalled(TimeoutError):
    """Raised when a read has not produced any new bytes in `stall_timeout`s."""


def read_exact(stream: Stream, n: int, stall_timeout: float = 5.0) -> bytes:
    """Read exactly n bytes. Stalls (no progress) longer than stall_timeout
    raise StreamStalled. EOF on a FileStream bubbles up as EOFError so the
    caller can stop the loop cleanly."""
    buf = bytearray()
    deadline = time.monotonic() + stall_timeout
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if chunk:
            buf.extend(chunk)
            deadline = time.monotonic() + stall_timeout
        else:
            if time.monotonic() > deadline:
                raise StreamStalled(f"only got {len(buf)}/{n} bytes")
            # Yield the CPU so we do not hot-spin while the source is idle.
            # The wait length is proportional to how recently we made progress.
            time.sleep(0.005)
    return bytes(buf)


def sync_to_magic(stream: Stream, stall_timeout: float = 5.0) -> None:
    """Byte-scan until the 4-byte magic 'ICHP' has been consumed."""
    window = b""
    deadline = time.monotonic() + stall_timeout
    while True:
        b = stream.read(1)
        if b:
            window = (window + b)[-4:]
            if window == MAGIC:
                return
            deadline = time.monotonic() + stall_timeout
        else:
            if time.monotonic() > deadline:
                raise StreamStalled("no bytes seen while searching for ICHP magic")
            time.sleep(0.005)


def read_frame(stream: Stream) -> dict:
    sync_to_magic(stream)
    rest = read_exact(stream, HEADER_SIZE - 4)
    header_bytes = MAGIC + rest

    h = unpack_header(header_bytes)
    n_samples = h["n_samples"]

    payload_bytes = read_exact(stream, n_samples * 2)
    crc_lo, crc_hi = read_exact(stream, 2)
    crc_recv = crc_lo | (crc_hi << 8)
    crc_calc = crc16_ccitt(header_bytes + payload_bytes)
    crc_ok = (crc_recv == crc_calc)

    samples = struct.unpack(f"<{n_samples}h", payload_bytes)

    return {**h, "samples": samples, "crc_ok": crc_ok}


# ---- WAV save --------------------------------------------------------------

def save_wav(path: Path, samples: Tuple[int, ...], rate_hz: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate_hz)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


# ---- CLI -------------------------------------------------------------------

def open_stream(args: argparse.Namespace) -> Tuple[Stream, str]:
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


def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing frame receiver")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--port", help="serial port (e.g. COM7, /dev/ttyACM0)")
    src.add_argument("--tcp", metavar="HOST:PORT", help="read from a TCP server")
    src.add_argument("--in", dest="in_", type=Path, help="read from a binary file")

    p.add_argument("--baud", type=int, default=921600,
                   help="baud rate (default: 921600; serial only)")
    p.add_argument("--out", type=Path, default=Path("captures"),
                   help="output directory (default: ./captures)")
    p.add_argument("--max-frames", type=int, default=0,
                   help="stop after N frames (0 = run until Ctrl+C / EOF)")
    p.add_argument("--keep-bad-crc", action="store_true",
                   help="save frames even if CRC fails")
    args = p.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "labels.csv"
    write_header = not csv_path.exists()
    csv_fh = csv_path.open("a", newline="", encoding="utf-8")
    csv_w = csv.writer(csv_fh)
    if write_header:
        csv_w.writerow([
            "seq", "timestamp_ms", "rate_hz", "n_samples",
            "servo_a", "servo_b", "servo_c", "servo_AB", "servo_BC",
            "wav", "crc_ok",
        ])

    stream, source_desc = open_stream(args)
    print(f"opening {source_desc}", flush=True)
    print(f"writing to {out_dir.resolve()}", flush=True)
    print("waiting for frames... (Ctrl+C to stop)", flush=True)

    count_ok = 0
    count_bad = 0
    t0 = time.monotonic()
    try:
        while True:
            try:
                frame = read_frame(stream)
            except EOFError:
                print("source EOF; stopping", flush=True)
                break
            except (ValueError, StreamStalled) as e:
                print(f"  frame error: {e}", flush=True)
                continue

            if not frame["crc_ok"]:
                count_bad += 1
                print(f"[{frame['seq']:6d}] CRC BAD (skipped)", flush=True)
                if not args.keep_bad_crc:
                    continue

            wav_name = f"frame_{frame['seq']:06d}.wav"
            wav_path = out_dir / wav_name
            save_wav(wav_path, frame["samples"], frame["rate_hz"])
            csv_w.writerow([
                frame["seq"], frame["timestamp_ms"], frame["rate_hz"], frame["n_samples"],
                *frame["servo_deg"], wav_name, int(frame["crc_ok"]),
            ])
            csv_fh.flush()

            elapsed = time.monotonic() - t0
            count_ok += 1
            rate = count_ok / max(elapsed, 1e-6)
            print(
                f"[{frame['seq']:6d}] t={frame['timestamp_ms']:>8d}ms "
                f"sr={frame['rate_hz']} N={frame['n_samples']} "
                f"servos=[" + ",".join(f"{a:5.1f}" for a in frame["servo_deg"]) + "] "
                f"CRC={'OK' if frame['crc_ok'] else 'BAD'} "
                f"({rate:.2f} fps)",
                flush=True,
            )
            if args.max_frames and count_ok >= args.max_frames:
                break
    except KeyboardInterrupt:
        print("\ninterrupted by user", flush=True)
    finally:
        csv_fh.close()
        stream.close()
        print(f"saved {count_ok} frames (bad CRC: {count_bad}) to {out_dir}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
