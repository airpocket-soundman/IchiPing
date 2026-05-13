#!/usr/bin/env python3
"""IchiPing data-collector client (PC side).

Pairs with firmware/projects/10_collector. Drives the bidirectional UART:

  - Sends ASCII commands (SET / GET / START / STOP / PING) one per line.
  - Reads back interleaved ASCII reply lines ("OK", "INFO", "ERR") and
    binary ICHP audio frames; saves each frame as WAV plus a metadata row
    in <out>/labels.csv keyed by the SET label and repeat index.

The wire de-frame logic is identical to receiver.py — we scan for the
"ICHP" magic, accumulate the header / payload / CRC, and treat any bytes
that arrive while NOT inside a frame as ASCII reply text terminated by
'\\n'. This way the PC can interleave commands and frames safely.

Two ways to use it:

  1) Interactive REPL (default):
       python collector_client.py --port COM7
     Then type commands ("SET tone chirp", "SET repeats 20", "START"...)
     and watch frames pile up under captures/.

  2) Script mode — run a predefined collection plan from a YAML/JSON file:
       python collector_client.py --port COM7 --plan plan.json
     plan.json example:
       [
         {"label": "door_closed", "tone": "chirp",  "repeats": 30},
         {"label": "door_half",   "tone": "chirp",  "repeats": 30},
         {"label": "door_open",   "tone": "chirp",  "repeats": 30},
         {"label": "amb_silence", "tone": "silence","repeats": 10}
       ]
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
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

from ichp_frame import MAGIC, HEADER_SIZE, crc16_ccitt, unpack_header


# ---- shared serial driver --------------------------------------------------

class SerialDuplex:
    def __init__(self, port: str, baud: int):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self._buf = bytearray()
        self._stop = False
        self._ascii_q: "Queue[str]" = Queue()
        self._frame_q: "Queue[dict]" = Queue()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def send_line(self, s: str) -> None:
        self.ser.write((s.rstrip("\r\n") + "\n").encode("ascii"))

    def next_ascii(self, timeout: float) -> Optional[str]:
        try:
            return self._ascii_q.get(timeout=timeout)
        except Empty:
            return None

    def next_frame(self, timeout: float) -> Optional[dict]:
        try:
            return self._frame_q.get(timeout=timeout)
        except Empty:
            return None

    def close(self) -> None:
        self._stop = True
        self._t.join(timeout=0.5)
        self.ser.close()

    # internals -------------------------------------------------------------

    def _reader(self) -> None:
        line_buf = bytearray()
        while not self._stop:
            chunk = self.ser.read(4096)
            if not chunk:
                continue
            self._buf.extend(chunk)
            self._drain(line_buf)

    def _drain(self, line_buf: bytearray) -> None:
        i = 0
        while i < len(self._buf):
            # If the next 4 bytes are ICHP magic we have a binary frame
            if (i + 4 <= len(self._buf)
                    and self._buf[i:i+4] == MAGIC):
                consumed = self._try_consume_frame(i)
                if consumed is None:
                    break       # not enough bytes yet; wait for more
                i = consumed
                continue
            # otherwise treat as ASCII line content
            b = self._buf[i]
            i += 1
            if b in (0x0d,):       # CR -> drop
                continue
            if b == 0x0a:          # LF -> emit line
                self._ascii_q.put(line_buf.decode("utf-8", errors="replace"))
                line_buf.clear()
                continue
            line_buf.append(b)
        # discard processed bytes
        del self._buf[:i]

    def _try_consume_frame(self, start: int) -> Optional[int]:
        if len(self._buf) - start < HEADER_SIZE:
            return None
        header_bytes = bytes(self._buf[start:start + HEADER_SIZE])
        h = unpack_header(header_bytes)
        n = h["n_samples"]
        total = HEADER_SIZE + n * 2 + 2
        if len(self._buf) - start < total:
            return None
        body = bytes(self._buf[start + HEADER_SIZE:start + HEADER_SIZE + n * 2])
        crc_bytes = bytes(self._buf[start + HEADER_SIZE + n * 2:start + total])
        crc_recv = crc_bytes[0] | (crc_bytes[1] << 8)
        crc_calc = crc16_ccitt(header_bytes + body)
        samples = struct.unpack(f"<{n}h", body)
        self._frame_q.put({
            **h,
            "samples": samples,
            "crc_ok":  (crc_recv == crc_calc),
        })
        return start + total


# ---- saver -----------------------------------------------------------------

def save_wav(path: Path, samples, rate_hz: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate_hz)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


# ---- run modes -------------------------------------------------------------

def configure(s: SerialDuplex, label: str, tone: str, repeats: int,
              window: int, rate: int) -> None:
    for cmd in (f"SET label {label}",
                f"SET tone {tone}",
                f"SET repeats {repeats}",
                f"SET window {window}",
                f"SET rate {rate}"):
        s.send_line(cmd)
        time.sleep(0.02)


def collect_one_plan(s: SerialDuplex, plan: list, out: Path) -> None:
    csv_path = out / "labels.csv"
    write_hdr = not csv_path.exists()
    out.mkdir(parents=True, exist_ok=True)
    fh = csv_path.open("a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if write_hdr:
        w.writerow(["label", "tone", "idx", "total",
                    "rate_hz", "n_samples", "wav", "crc_ok"])

    current_label = "(no label)"
    for step in plan:
        label   = step["label"]
        tone    = step.get("tone", "chirp")
        repeats = int(step.get("repeats", 1))
        window  = int(step.get("window", 32000))
        rate    = int(step.get("rate", 16000))

        current_label = label
        configure(s, label, tone, repeats, window, rate)
        print(f">>> {label} ({tone}) ×{repeats}", flush=True)
        s.send_line("START")

        seen = 0
        deadline = time.monotonic() + repeats * 8 + 30
        while seen < repeats and time.monotonic() < deadline:
            # drain ASCII lines so user can see INFO/OK chatter
            while True:
                line = s.next_ascii(timeout=0.0) if False else s.next_ascii(timeout=0.05)
                if line is None:
                    break
                print(f"  · {line}")
                if line.strip().startswith("INFO DONE") or line.strip().startswith("INFO ABORTED"):
                    deadline = time.monotonic()  # break outer
            f = s.next_frame(timeout=0.5)
            if f is None:
                continue
            seen += 1
            wav_name = f"{label}_{int(time.time())}_{f['seq']:06d}.wav"
            save_wav(out / wav_name, f["samples"], f["rate_hz"])
            tone_idx = int(f["servo_deg"][2])
            idx      = int(f["servo_deg"][3])
            total    = int(f["servo_deg"][4])
            w.writerow([label, tone, idx, total,
                        f["rate_hz"], f["n_samples"], wav_name,
                        int(f["crc_ok"])])
            fh.flush()
            print(f"  ← frame {idx+1}/{total}  CRC={'OK' if f['crc_ok'] else 'BAD'}",
                  flush=True)
    fh.close()


def collect_repl(s: SerialDuplex, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "labels.csv"
    write_hdr = not csv_path.exists()
    fh = csv_path.open("a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if write_hdr:
        w.writerow(["label", "tone", "idx", "total",
                    "rate_hz", "n_samples", "wav", "crc_ok"])

    print("interactive — type commands, blank line to quit.", flush=True)
    print("e.g.  SET label door_closed\n      SET repeats 20\n      START", flush=True)
    label = "(no label)"

    while True:
        # drain frames/lines first
        while True:
            line = s.next_ascii(timeout=0.0)
            if line is None: break
            print(f"  · {line}")
            if line.startswith("OK label="):
                label = line[len("OK label="):]
        while True:
            f = s.next_frame(timeout=0.0)
            if f is None: break
            wav_name = f"{label}_{int(time.time())}_{f['seq']:06d}.wav"
            save_wav(out / wav_name, f["samples"], f["rate_hz"])
            w.writerow([label, "?", int(f["servo_deg"][3]),
                        int(f["servo_deg"][4]),
                        f["rate_hz"], f["n_samples"], wav_name,
                        int(f["crc_ok"])])
            fh.flush()
            print(f"  ← frame  CRC={'OK' if f['crc_ok'] else 'BAD'}", flush=True)
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break
        s.send_line(line)
    fh.close()


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="IchiPing labelled collector client")
    p.add_argument("--port", required=True, help="serial port (e.g. COM7)")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--out", type=Path, default=Path("captures/10"))
    p.add_argument("--plan", type=Path, default=None,
                   help="JSON list of {label, tone, repeats, window?, rate?} steps")
    args = p.parse_args()

    s = SerialDuplex(args.port, args.baud)
    # quick PING handshake
    s.send_line("PING")
    pong = s.next_ascii(timeout=2.0)
    print(f"[{args.port}] ← {pong!s}", flush=True)
    try:
        if args.plan is not None:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            collect_one_plan(s, plan, args.out)
        else:
            collect_repl(s, args.out)
    finally:
        s.send_line("STOP")
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
