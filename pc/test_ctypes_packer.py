"""C ↔ Python frame packer cross-check via ctypes.

Compiles firmware/shared/source/ichiping_frame.c as a host shared library
(see firmware/host_build/Makefile) and verifies that ichp_pack_frame
emits byte-for-byte the same bytes as pc/ichp_frame.pack_frame for the
same inputs.

If the shared library has not been built (e.g. gcc unavailable), every
test is skipped. The unit-only checks in test_frame_format.py still
guard the Python-side layout.

Run:
    cd firmware/host_build && make
    cd ../../pc && python -m unittest test_ctypes_packer -v
"""
from __future__ import annotations

import ctypes
import unittest
from pathlib import Path
from typing import Optional

from ichp_frame import HEADER_SIZE, pack_frame


def _find_lib() -> Optional[Path]:
    base = Path(__file__).resolve().parent.parent / "firmware" / "host_build"
    for name in ("ichp.dll", "libichp.so", "libichp.dylib"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


_LIB_PATH = _find_lib()


@unittest.skipUnless(
    _LIB_PATH is not None,
    "firmware/host_build shared library missing — run `make` there first "
    "(needs gcc; see firmware/host_build/README.md)",
)
class CtypesPackerTests(unittest.TestCase):
    lib: ctypes.CDLL

    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(_LIB_PATH))
        # size_t ichp_pack_frame(uint8_t *out, size_t out_size,
        #                        uint16_t seq, uint32_t timestamp_ms,
        #                        uint16_t rate_hz, uint16_t n_samples,
        #                        const float servo_deg[5],
        #                        const int16_t *samples);
        cls.lib.ichp_pack_frame.restype = ctypes.c_size_t
        cls.lib.ichp_pack_frame.argtypes = [
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.c_uint16, ctypes.c_uint32,
            ctypes.c_uint16, ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_int16),
        ]

    def _c_pack(self, seq, ts, rate, samples, servos) -> bytes:
        n = len(samples)
        out_size = HEADER_SIZE + n * 2 + 2
        out = (ctypes.c_uint8 * out_size)()
        s_arr = (ctypes.c_float * 5)(*servos)
        smp_arr = (ctypes.c_int16 * max(n, 1))(*samples) if n else None
        smp_ptr = smp_arr if smp_arr is not None else None
        ret = self.lib.ichp_pack_frame(
            out, out_size, seq, ts, rate, n, s_arr, smp_ptr,
        )
        self.assertEqual(ret, out_size, "C packer returned unexpected length")
        return bytes(out)

    def test_minimal_frame_matches(self):
        samples = [0, 1, -1, 32767, -32768]
        servos = [0.0, 22.5, 45.0, 67.5, 90.0]
        py = pack_frame(seq=42, timestamp_ms=12345, rate_hz=16000,
                        servo_deg=servos, samples=samples)
        c = self._c_pack(42, 12345, 16000, samples, servos)
        self.assertEqual(py, c, "C and Python packers diverge")

    def test_realistic_v01_frame_matches(self):
        # 32000 samples @ 16 kHz = the v0.1 main-loop payload size
        samples = [((i * 17) & 0xFFFF) - 32768 for i in range(32000)]
        servos = [12.5, 33.0, 88.0, 4.0, 60.0]
        py = pack_frame(seq=999, timestamp_ms=0xDEADBEEF, rate_hz=16000,
                        servo_deg=servos, samples=samples)
        c = self._c_pack(999, 0xDEADBEEF, 16000, samples, servos)
        self.assertEqual(py, c)


if __name__ == "__main__":
    unittest.main()
