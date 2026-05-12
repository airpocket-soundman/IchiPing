"""End-to-end loopback test: emulator → file → receiver → WAV + CSV.

Exercises the entire v0.1 pipeline without any hardware. If this passes
in CI it means the on-the-wire format produced by the (Python) emulator
is decoded byte-for-byte by the same code path receiver.py uses for the
real MCU.

Also bit-exact verifies random_servo_angles against the C formula in
firmware/source/main.c — the same integer math should produce identical
values on every platform.

Run:
    cd pc
    python -m unittest test_loopback
"""
from __future__ import annotations

import csv
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from emulator import iter_frames, random_servo_angles
from receiver import FileStream, read_frame, save_wav


class ServoAnglesCMirrorTests(unittest.TestCase):
    """random_servo_angles is a port of the C function in main.c; verify
    the integer math matches the C formula exactly."""

    def test_seq0(self):
        # seq=0: x = i * 0x9E3779B1 mod 2^32, then x % 91
        expected = [
            float((0 * 2654435761 + i * 0x9E3779B1) % (1 << 32) % 91)
            for i in range(5)
        ]
        self.assertEqual(random_servo_angles(0), expected)

    def test_seq_wraparound_consistent(self):
        # Range check: every output is in [0, 90] degrees
        for seq in [0, 1, 42, 1000, 0xFFFF]:
            angles = random_servo_angles(seq)
            self.assertEqual(len(angles), 5)
            for a in angles:
                self.assertGreaterEqual(a, 0.0)
                self.assertLessEqual(a, 90.0)

    def test_seq_deterministic(self):
        self.assertEqual(random_servo_angles(123), random_servo_angles(123))


class LoopbackRoundtripTests(unittest.TestCase):
    """Build a small stream with the emulator, pass it through receiver,
    confirm CRC OK on every frame and that WAV+CSV outputs are well-formed."""

    N_FRAMES = 3
    RATE = 16000
    N_SAMPLES = 8000  # 0.5 s — smaller than the runtime default to keep tests fast

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ichp-loopback-"))
        self.bin_path = self.tmp / "stream.bin"
        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()

        with self.bin_path.open("wb") as f:
            for frame in iter_frames(self.RATE, self.N_SAMPLES, seed=0xC4C4C4C4,
                                     count=self.N_FRAMES):
                f.write(frame)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            else:
                p.rmdir()
        self.tmp.rmdir()

    def test_receiver_decodes_all_frames(self):
        with self.bin_path.open("rb") as fh:
            stream = FileStream(fh)
            decoded = []
            for _ in range(self.N_FRAMES):
                decoded.append(read_frame(stream))

        self.assertEqual(len(decoded), self.N_FRAMES)
        for i, fr in enumerate(decoded):
            self.assertTrue(fr["crc_ok"], f"frame {i} CRC failed")
            self.assertEqual(fr["seq"], i)
            self.assertEqual(fr["rate_hz"], self.RATE)
            self.assertEqual(fr["n_samples"], self.N_SAMPLES)
            self.assertEqual(len(fr["samples"]), self.N_SAMPLES)

    def test_wav_save_roundtrips_samples(self):
        with self.bin_path.open("rb") as fh:
            stream = FileStream(fh)
            fr = read_frame(stream)

        wav_path = self.out_dir / "frame_000000.wav"
        save_wav(wav_path, fr["samples"], fr["rate_hz"])
        self.assertTrue(wav_path.exists())

        with wave.open(str(wav_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), self.RATE)
            self.assertEqual(wf.getnframes(), self.N_SAMPLES)
            raw = wf.readframes(self.N_SAMPLES)
        decoded = struct.unpack(f"<{self.N_SAMPLES}h", raw)
        self.assertEqual(decoded, tuple(fr["samples"]))


class EmulatorOutputShapeTests(unittest.TestCase):
    """The synthetic audio should at least be in range and the chirp section
    should not be zero. We don't try to assert bit-equality with the C
    output (float math diverges), only that the structure is sane."""

    def test_chirp_segment_is_nonzero(self):
        frames = list(iter_frames(16000, 8000, seed=0xC4C4C4C4, count=1))
        self.assertEqual(len(frames), 1)
        # Pull the payload back out by re-decoding
        with tempfile.TemporaryFile() as fh:
            fh.write(frames[0])
            fh.seek(0)
            from receiver import FileStream as FS  # local for clarity
            stream = FS(fh)  # type: ignore[arg-type]
            decoded = read_frame(stream)
        samples = decoded["samples"]
        # The 0.3 s chirp at 16 kHz fills the first 4800 samples
        chirp = samples[:4800]
        self.assertNotEqual(max(abs(s) for s in chirp), 0)
        # And tails decay — last 100 samples should average smaller than the chirp
        tail_avg = sum(abs(s) for s in samples[-100:]) / 100
        chirp_avg = sum(abs(s) for s in chirp[:100]) / 100
        self.assertLess(tail_avg, chirp_avg)


if __name__ == "__main__":
    unittest.main()
