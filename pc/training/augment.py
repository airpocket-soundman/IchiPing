"""Data augmentation for IchiPing training.

Used during the training loop (Dataset transform) to make the model
robust against:
  - external ambient noise (TV / conversation / appliances)
  - level drift (speaker volume, mic gain)
  - small time alignment errors (chirp start jitter)

Each transform is a plain callable ``(samples: np.ndarray, rate: int) ->
(samples, rate)`` so they compose via ``Compose``.

Recommended chain (training):
    Compose([
        TimeShift(max_ms=10),
        LevelJitter(db_range=2.0),
        NoiseOverlay(noise_dir=Path('captures/silence_2cond_v1/silence_tv'),
                     snr_db_range=(10, 30)),
    ])

For PoC iterations the noise overlay can be omitted if no ambient
recordings exist yet — the augmentation falls back to a no-op.
"""
from __future__ import annotations

import random
import wave
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

Transform = Callable[[np.ndarray, int], Tuple[np.ndarray, int]]


class Compose:
    """Sequentially apply a list of transforms."""

    def __init__(self, transforms: Sequence[Transform]) -> None:
        self.transforms = list(transforms)

    def __call__(self, samples: np.ndarray, rate: int) -> Tuple[np.ndarray, int]:
        for t in self.transforms:
            samples, rate = t(samples, rate)
        return samples, rate


# ---------------------------------------------------------------------------
# Simple per-sample transforms
# ---------------------------------------------------------------------------

class LevelJitter:
    """Multiply by 10^(g/20) for random g in [-db_range, +db_range].

    Simulates SPK volume / mic gain drift between recordings.
    """

    def __init__(self, db_range: float = 2.0, p: float = 1.0) -> None:
        self.db_range = float(db_range)
        self.p = float(p)

    def __call__(self, samples: np.ndarray, rate: int) -> Tuple[np.ndarray, int]:
        if random.random() > self.p:
            return samples, rate
        db = random.uniform(-self.db_range, +self.db_range)
        gain = 10.0 ** (db / 20.0)
        return samples * gain, rate


class TimeShift:
    """Circularly shift the signal by a random number of milliseconds.

    Models small chirp-start timing jitter. We use circular (np.roll)
    rather than zero-padded shift to preserve total energy — fine for
    spectral features that we average over the full window.
    """

    def __init__(self, max_ms: float = 10.0, p: float = 1.0) -> None:
        self.max_ms = float(max_ms)
        self.p = float(p)

    def __call__(self, samples: np.ndarray, rate: int) -> Tuple[np.ndarray, int]:
        if random.random() > self.p:
            return samples, rate
        max_n = int(self.max_ms * rate / 1000.0)
        if max_n == 0:
            return samples, rate
        shift = random.randint(-max_n, +max_n)
        return np.roll(samples, shift), rate


class GaussianHiss:
    """Add Gaussian noise scaled to a target SNR in dB (wrt input RMS).

    Cheaper substitute for real-ambient overlay when no ambient WAVs
    are available yet.
    """

    def __init__(self, snr_db_range: Tuple[float, float] = (15.0, 40.0),
                 p: float = 1.0) -> None:
        self.snr_db_range = snr_db_range
        self.p = float(p)

    def __call__(self, samples: np.ndarray, rate: int) -> Tuple[np.ndarray, int]:
        if random.random() > self.p:
            return samples, rate
        snr_db = random.uniform(*self.snr_db_range)
        sig_rms = float(np.sqrt(np.mean(samples ** 2)) + 1e-12)
        noise_rms = sig_rms / (10.0 ** (snr_db / 20.0))
        noise = np.random.normal(0.0, noise_rms, size=samples.shape).astype(np.float32)
        return samples + noise, rate


# ---------------------------------------------------------------------------
# Ambient noise overlay
# ---------------------------------------------------------------------------

class NoiseOverlay:
    """Overlay a randomly-chosen ambient WAV at a target SNR.

    ``noise_dirs`` should point at directories containing recorded silent
    WAVs (e.g. ``captures/silence_2cond_v1/silence_tv/``). At construction
    time we scan and cache the audio so per-batch overhead is small.

    If the cache is empty (no WAVs found) the transform becomes a no-op,
    so it is safe to include in the pipeline before ambient data exists.
    """

    def __init__(
        self,
        noise_dirs: Sequence[Path],
        snr_db_range: Tuple[float, float] = (10.0, 30.0),
        p: float = 1.0,
    ) -> None:
        self.snr_db_range = snr_db_range
        self.p = float(p)
        self.noise_buffers: List[np.ndarray] = []
        self.noise_rates: List[int] = []
        for d in noise_dirs:
            if not d.exists():
                continue
            for wav_path in sorted(d.glob("frame_*.wav")):
                try:
                    samples, rate = _load_wav_mono16(wav_path)
                except Exception:
                    continue
                self.noise_buffers.append(samples)
                self.noise_rates.append(rate)

    def __call__(self, samples: np.ndarray, rate: int) -> Tuple[np.ndarray, int]:
        if not self.noise_buffers or random.random() > self.p:
            return samples, rate

        # Pick a random ambient buffer at the same rate (or first available).
        candidates = [i for i, r in enumerate(self.noise_rates) if r == rate]
        if not candidates:
            return samples, rate
        idx = random.choice(candidates)
        noise = self.noise_buffers[idx]

        # Match length: take a random slice of `noise` of len(samples).
        if noise.size < samples.size:
            return samples, rate
        start = random.randint(0, noise.size - samples.size)
        noise_slice = noise[start:start + samples.size]

        # Scale noise to target SNR.
        sig_rms = float(np.sqrt(np.mean(samples ** 2)) + 1e-12)
        noise_rms_current = float(np.sqrt(np.mean(noise_slice ** 2)) + 1e-12)
        target_snr = random.uniform(*self.snr_db_range)
        target_noise_rms = sig_rms / (10.0 ** (target_snr / 20.0))
        noise_scale = target_noise_rms / noise_rms_current
        return samples + noise_slice * noise_scale, rate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_wav_mono16(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"{path} is not mono int16")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, rate


# ---------------------------------------------------------------------------
# Convenience presets
# ---------------------------------------------------------------------------

def default_train_transform(
    ambient_dirs: Optional[Sequence[Path]] = None,
) -> Compose:
    """A reasonable default chain for first-pass training.

    If ``ambient_dirs`` is None, falls back to synthetic GaussianHiss.
    """
    steps: List[Transform] = [
        TimeShift(max_ms=10.0, p=0.7),
        LevelJitter(db_range=2.0, p=0.7),
    ]
    if ambient_dirs:
        steps.append(NoiseOverlay(ambient_dirs, snr_db_range=(10.0, 30.0), p=0.6))
    else:
        steps.append(GaussianHiss(snr_db_range=(15.0, 35.0), p=0.5))
    return Compose(steps)
