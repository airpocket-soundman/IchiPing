"""IchiPing loopback emulator.

Acts as a virtual MCU: generates the same synthetic chirp + decaying-noise
tail that firmware/shared/source/dummy_audio.c produces, packs it into ICHP frames
via the shared pc/ichp_frame.py packer, and streams the bytes to one of:

  * stdout  (default; pipe into another tool)
  * TCP server on --tcp HOST:PORT  (the receiver connects and reads)
  * a file on --out PATH           (write a fixed number of frames and exit)

This lets the v0.1 end-to-end pipeline (frame format + CRC + WAV/CSV save)
be exercised without any hardware. Pair with the same parameters
receiver.py expects (16 kHz, 32000 samples, ~3 s cadence).

Usage examples:

  # 1) TCP loopback: emulator publishes, a TCP-aware reader consumes
  python emulator.py --tcp 127.0.0.1:5000 --frames 5

  # 2) Write 5 frames to a file, then read them back
  python emulator.py --out fake_stream.bin --frames 5

  # 3) Pipe into a hex dump for debugging
  python emulator.py --frames 1 | xxd | head
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Iterator, List

from ichp_frame import pack_frame


# ---- dummy_audio.c port (kept numerically equivalent on purpose) ----------

class _Xorshift32:
    """Same xorshift32 generator as firmware/shared/source/dummy_audio.c."""

    def __init__(self, seed: int = 0xA5A5A5A5):
        self.state = seed & 0xFFFFFFFF or 0xA5A5A5A5

    def next_u32(self) -> int:
        x = self.state
        x = (x ^ ((x << 13) & 0xFFFFFFFF)) & 0xFFFFFFFF
        x = (x ^ (x >> 17)) & 0xFFFFFFFF
        x = (x ^ ((x << 5) & 0xFFFFFFFF)) & 0xFFFFFFFF
        self.state = x
        return x


def synth_audio(n_samples: int, rate_hz: int, rng: _Xorshift32) -> List[int]:
    """Port of dummy_audio_generate (chirp 200→8 kHz over 0.3 s, then noise tail)."""
    fs = float(rate_hz)
    chirp_dur = 0.30
    f0, f1 = 200.0, 8000.0
    amp = 0.45 * 32767.0
    tau = 0.60
    two_pi = 2.0 * math.pi

    n_chirp = min(int(chirp_dur * fs), n_samples)
    out: List[int] = [0] * n_samples
    for i in range(n_chirp):
        t = i / fs
        phase = two_pi * (f0 * t + 0.5 * (f1 - f0) * t * t / chirp_dur)
        out[i] = max(-32768, min(32767, int(amp * math.sin(phase))))
    for i in range(n_chirp, n_samples):
        t = (i - n_chirp) / fs
        env = math.exp(-t / tau)
        # match C: ((float)xorshift32() / 0xFFFFFFFF) * 2 - 1
        noise = (rng.next_u32() / 0xFFFFFFFF) * 2.0 - 1.0
        out[i] = max(-32768, min(32767, int(amp * env * noise)))
    return out


def random_servo_angles(seq: int) -> List[float]:
    """Mirrors random_servo_angles() in firmware/projects/01_dummy_emitter/main.c."""
    angles = []
    for i in range(5):
        x = (seq * 2654435761 + i * 0x9E3779B1) & 0xFFFFFFFF
        angles.append(float(x % 91))
    return angles


# ---- frame generator -------------------------------------------------------

def iter_frames(rate_hz: int, n_samples: int, seed: int,
                count: int = 0) -> Iterator[bytes]:
    """Yield packed ICHP frames. count=0 means infinite."""
    rng = _Xorshift32(seed)
    t_start = time.monotonic()
    seq = 0
    while True:
        servos = random_servo_angles(seq)
        samples = synth_audio(n_samples, rate_hz, rng)
        ts_ms = int((time.monotonic() - t_start) * 1000) & 0xFFFFFFFF
        yield pack_frame(seq=seq, timestamp_ms=ts_ms, rate_hz=rate_hz,
                         servo_deg=servos, samples=samples)
        seq = (seq + 1) & 0xFFFF
        if count and seq >= count:
            return


# ---- sinks -----------------------------------------------------------------

def emit_stdout(frames: Iterator[bytes], cadence_s: float) -> None:
    out = sys.stdout.buffer
    for fr in frames:
        out.write(fr)
        out.flush()
        if cadence_s > 0:
            time.sleep(cadence_s)


def emit_file(frames: Iterator[bytes], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("wb") as f:
        for fr in frames:
            f.write(fr)
            n += 1
    return n


def emit_tcp(frames: Iterator[bytes], host: str, port: int, cadence_s: float) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    print(f"emulator: listening on {host}:{port}", file=sys.stderr, flush=True)
    conn, addr = srv.accept()
    print(f"emulator: client connected from {addr}", file=sys.stderr, flush=True)
    try:
        for fr in frames:
            conn.sendall(fr)
            if cadence_s > 0:
                time.sleep(cadence_s)
    except (BrokenPipeError, ConnectionResetError):
        print("emulator: client disconnected", file=sys.stderr, flush=True)
    finally:
        conn.close()
        srv.close()


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing loopback emulator (no hardware required)")
    p.add_argument("--rate", type=int, default=16000, help="sample rate Hz (default 16000)")
    p.add_argument("--n-samples", type=int, default=32000,
                   help="samples per frame (default 32000 = 2 s @ 16 kHz)")
    p.add_argument("--frames", type=int, default=0,
                   help="number of frames to emit (0 = infinite). file mode requires N>0.")
    p.add_argument("--cadence", type=float, default=3.0,
                   help="seconds between frames (default 3.0; set 0 for fastest)")
    p.add_argument("--seed", type=lambda s: int(s, 0), default=0xC4C4C4C4,
                   help="xorshift32 seed (default 0xC4C4C4C4 matches firmware)")
    sink = p.add_mutually_exclusive_group()
    sink.add_argument("--tcp", metavar="HOST:PORT",
                      help="serve frames over TCP (single client)")
    sink.add_argument("--out", type=Path, help="write frames to a file then exit")

    args = p.parse_args()

    frames = iter_frames(args.rate, args.n_samples, args.seed, args.frames)

    if args.out:
        if args.frames <= 0:
            p.error("--out requires a positive --frames count")
        n = emit_file(frames, args.out)
        print(f"emulator: wrote {n} frames to {args.out}", file=sys.stderr, flush=True)
        return 0

    if args.tcp:
        host, _, port_s = args.tcp.partition(":")
        if not port_s:
            p.error("--tcp value must look like HOST:PORT")
        emit_tcp(frames, host or "127.0.0.1", int(port_s), args.cadence)
        return 0

    emit_stdout(frames, args.cadence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
