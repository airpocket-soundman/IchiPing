"""IchiPing frame format — single source of truth on the PC side.

Mirrors firmware/include/ichiping_frame.h. Both the live serial receiver
(receiver.py) and host-side tests pull the wire format from here so the
C struct / Python struct-string pair only has to be defined once on the
Python side.
"""
from __future__ import annotations

import struct
from typing import Sequence

MAGIC: bytes = b"ICHP"
TYPE_AUDIO: int = 0x01

# Header layout (little-endian, packed):
#   4s  magic
#   B   type
#   B   reserved
#   H   seq
#   I   timestamp_ms
#   H   n_samples
#   H   rate_hz
#   5f  servo_deg[5]
HEADER_FMT: str = "<4sBBHIHH5f"
HEADER_SIZE: int = struct.calcsize(HEADER_FMT)
assert HEADER_SIZE == 36, f"header layout drift: {HEADER_SIZE}"

CRC_SIZE: int = 2
CRC_POLY: int = 0x1021      # CRC-16/CCITT-FALSE
CRC_INIT: int = 0xFFFF


def crc16_ccitt(data: bytes) -> int:
    crc = CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def pack_header(seq: int, timestamp_ms: int, rate_hz: int,
                n_samples: int, servo_deg: Sequence[float]) -> bytes:
    if len(servo_deg) != 5:
        raise ValueError(f"servo_deg must have 5 entries, got {len(servo_deg)}")
    return struct.pack(
        HEADER_FMT,
        MAGIC, TYPE_AUDIO, 0,
        seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF,
        n_samples & 0xFFFF, rate_hz & 0xFFFF,
        *servo_deg,
    )


def pack_frame(seq: int, timestamp_ms: int, rate_hz: int,
               servo_deg: Sequence[float], samples: Sequence[int]) -> bytes:
    """Return the on-wire bytes for one audio frame (header + payload + CRC)."""
    n_samples = len(samples)
    header = pack_header(seq, timestamp_ms, rate_hz, n_samples, servo_deg)
    payload = struct.pack(f"<{n_samples}h", *samples)
    crc = crc16_ccitt(header + payload)
    return header + payload + struct.pack("<H", crc)


def unpack_header(header_bytes: bytes) -> dict:
    if len(header_bytes) != HEADER_SIZE:
        raise ValueError(f"header is {len(header_bytes)} bytes, expected {HEADER_SIZE}")
    magic, type_, _reserved, seq, ts_ms, n_samples, rate_hz, *servos = struct.unpack(
        HEADER_FMT, header_bytes
    )
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    return {
        "type": type_,
        "seq": seq,
        "timestamp_ms": ts_ms,
        "n_samples": n_samples,
        "rate_hz": rate_hz,
        "servo_deg": tuple(servos),
    }
