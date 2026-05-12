"""Round-trip tests for the IchiPing wire format.

The frame layout is defined twice — once in firmware/shared/include/ichiping_frame.h
(C struct, packed by ichiping_frame.c) and once in pc/ichp_frame.py (Python
struct format string). These tests catch drift between the two before it
shows up as silent CRC failures on the live serial path.

Run:
    cd pc
    python -m unittest test_frame_format
"""
from __future__ import annotations

import struct
import unittest

from ichp_frame import (
    CRC_SIZE,
    HEADER_FMT,
    HEADER_SIZE,
    MAGIC,
    TYPE_AUDIO,
    crc16_ccitt,
    pack_frame,
    pack_header,
    unpack_header,
)


class HeaderLayoutTests(unittest.TestCase):
    def test_header_size_is_36_bytes(self):
        self.assertEqual(HEADER_SIZE, 36)

    def test_header_starts_with_magic(self):
        h = pack_header(seq=0, timestamp_ms=0, rate_hz=16000,
                        n_samples=0, servo_deg=[0.0] * 5)
        self.assertEqual(h[:4], MAGIC)

    def test_header_byte_offsets(self):
        # Spot-check exact byte positions. Guards against silently
        # adding/reordering fields without also touching the C struct.
        h = pack_header(seq=0x1234, timestamp_ms=0xDEADBEEF,
                        rate_hz=16000, n_samples=0, servo_deg=[0.0] * 5)
        self.assertEqual(h[0:4], b"ICHP")            # magic
        self.assertEqual(h[4], TYPE_AUDIO)           # type
        self.assertEqual(h[5], 0)                    # reserved
        self.assertEqual(h[6:8], b"\x34\x12")        # seq, LE
        self.assertEqual(h[8:12], b"\xEF\xBE\xAD\xDE")  # ts_ms, LE


class CrcTests(unittest.TestCase):
    def test_crc16_ccitt_known_vector(self):
        # Canonical CRC-16/CCITT-FALSE: "123456789" -> 0x29B1
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_crc16_empty(self):
        # CCITT-FALSE init value is preserved for empty input
        self.assertEqual(crc16_ccitt(b""), 0xFFFF)


class FrameRoundTripTests(unittest.TestCase):
    def test_pack_then_unpack(self):
        samples = [0, 1, -1, 32767, -32768, 100, -100, 0]
        servo = [12.5, 0.0, 90.0, 45.25, 60.0]
        frame = pack_frame(seq=42, timestamp_ms=12345, rate_hz=16000,
                           servo_deg=servo, samples=samples)

        expected_len = HEADER_SIZE + len(samples) * 2 + CRC_SIZE
        self.assertEqual(len(frame), expected_len)

        header = frame[:HEADER_SIZE]
        h = unpack_header(header)
        self.assertEqual(h["type"], TYPE_AUDIO)
        self.assertEqual(h["seq"], 42)
        self.assertEqual(h["timestamp_ms"], 12345)
        self.assertEqual(h["n_samples"], len(samples))
        self.assertEqual(h["rate_hz"], 16000)
        self.assertEqual(h["servo_deg"], tuple(servo))

        payload = frame[HEADER_SIZE:HEADER_SIZE + h["n_samples"] * 2]
        decoded = struct.unpack(f"<{h['n_samples']}h", payload)
        self.assertEqual(decoded, tuple(samples))

        crc_recv = struct.unpack("<H", frame[-CRC_SIZE:])[0]
        self.assertEqual(crc_recv, crc16_ccitt(header + payload))

    def test_pack_frame_rejects_wrong_servo_count(self):
        with self.assertRaises(ValueError):
            pack_frame(0, 0, 16000, [0.0, 0.0, 0.0], [0])

    def test_unpack_header_rejects_bad_magic(self):
        bad = b"XXXX" + b"\x00" * (HEADER_SIZE - 4)
        with self.assertRaises(ValueError):
            unpack_header(bad)

    def test_unpack_header_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            unpack_header(b"ICHP")


if __name__ == "__main__":
    unittest.main()
