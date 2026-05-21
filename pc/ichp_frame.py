"""IchiPing frame format — single source of truth on the PC side.

Mirrors firmware/shared/include/ichiping_frame.h. Both the live serial receiver
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
    magic, type_, reserved, seq, ts_ms, n_samples, rate_hz, *servos = struct.unpack(
        HEADER_FMT, header_bytes
    )
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    # Field validation — guards against false-positive ICHP matches inside
    # the audio payload. A chirp at moderate level can occasionally produce
    # the literal byte sequence 0x49 0x43 0x48 0x50 ("ICHP") and without
    # this check we'd treat the random bytes after it as a frame header,
    # then block waiting for a payload whose declared length is bogus.
    # Anything that doesn't match the firmware contract is a coincidence.
    if type_ != TYPE_AUDIO:
        raise ValueError(f"bad type: 0x{type_:02x} (expected TYPE_AUDIO=0x{TYPE_AUDIO:02x})")
    if reserved != 0:
        raise ValueError(f"bad reserved byte: 0x{reserved:02x}")
    if rate_hz not in (8000, 16000, 22050, 32000, 44100, 48000):
        raise ValueError(f"implausible rate_hz: {rate_hz}")
    # n_samples: pattern_lib caps total samples at 2000 ms × 16 kHz = 32000.
    # Allow generous headroom (50% over) for future format changes; reject
    # the obvious garbage (zero or > 64k - 1 packed into uint16 truncation).
    if n_samples == 0 or n_samples > 48000:
        raise ValueError(f"implausible n_samples: {n_samples}")
    # Servo degrees: the wire format is float32 in [0, 270] for our SG90
    # range plus a small tolerance for the wider PCA9685 tick map.
    for s in servos:
        if not (-10.0 <= s <= 290.0):
            raise ValueError(f"servo deg out of range: {s}")
    return {
        "type": type_,
        "seq": seq,
        "timestamp_ms": ts_ms,
        "n_samples": n_samples,
        "rate_hz": rate_hz,
        "servo_deg": tuple(servos),
    }
