"""Feature extraction for IchiPing v1.

The MCU pipeline (§3 spec.html) emits a 2-second 16 kHz WAV per frame
that contains a chirp + room impulse response. We turn that into a
1024-bin log-magnitude spectrum that the 1D-CNN consumes:

    chirp segment        : first 0.3 s of the 2 s frame (4800 samples)
    rir segment          : the remainder (27200 samples) — has the room info
    deconvolved RIR      : matched filter (cross-correlation) with template chirp
    spectrum             : 2048-pt rFFT on the first 128 ms of RIR → 1024 bins
    log-magnitude        : 20·log10(|X| + eps) then global mean-subtract

The exact constants are kept in one place so the same code path is used
both at train time and (eventually) on the device. The MCU version will
use PowerQuad-FFT in place of numpy.fft.rfft.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Constants tied to the firmware defaults — keep these in sync if you
# change firmware/source/main.c or dummy_audio.c.
RATE_HZ = 16000
CHIRP_F0 = 200.0
CHIRP_F1 = 8000.0
CHIRP_DUR_S = 0.30
RIR_WINDOW_S = 0.128         # 2048 samples at 16 kHz
RIR_NFFT = 2048              # rFFT bins = 1025; we drop bin 0 to get 1024
DB_FLOOR = -80.0


@dataclass(frozen=True)
class FeatureConfig:
    rate_hz: int = RATE_HZ
    chirp_f0: float = CHIRP_F0
    chirp_f1: float = CHIRP_F1
    chirp_dur_s: float = CHIRP_DUR_S
    rir_window_s: float = RIR_WINDOW_S
    nfft: int = RIR_NFFT


def synth_template_chirp(cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """The same linear-chirp shape the MCU emits — used as the matched filter."""
    n = int(cfg.chirp_dur_s * cfg.rate_hz)
    t = np.arange(n, dtype=np.float32) / cfg.rate_hz
    phase = 2 * np.pi * (cfg.chirp_f0 * t
                         + 0.5 * (cfg.chirp_f1 - cfg.chirp_f0) * t * t / cfg.chirp_dur_s)
    return np.sin(phase).astype(np.float32)


def extract_rir(samples: np.ndarray, cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """Matched-filter deconvolution. Returns the first cfg.nfft samples of
    the recovered RIR — that is what the spectrum head consumes."""
    template = synth_template_chirp(cfg)
    # Use cross-correlation; FFT-based for speed on long signals.
    n = samples.size + template.size - 1
    nfft_corr = 1 << (n - 1).bit_length()
    S = np.fft.rfft(samples.astype(np.float32), nfft_corr)
    T = np.fft.rfft(template[::-1], nfft_corr)  # cross-correlation = convolution with reversed template
    rir = np.fft.irfft(S * T, nfft_corr)[: cfg.nfft]
    return rir.astype(np.float32)


def rir_to_logmag_spectrum(rir: np.ndarray,
                           cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """Return a length-1024 log-magnitude vector ready for the 1D-CNN."""
    if rir.size < cfg.nfft:
        rir = np.pad(rir, (0, cfg.nfft - rir.size))
    spec = np.fft.rfft(rir[: cfg.nfft])
    mag = np.abs(spec)[1:]  # drop DC → 1024 bins
    eps = 1e-9
    db = 20.0 * np.log10(mag + eps)
    db = np.maximum(db, DB_FLOOR)
    db -= db.mean()
    return db.astype(np.float32)


def samples_to_features(samples: np.ndarray,
                        cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """One-stop: WAV samples → 1024-bin log-magnitude feature vector."""
    rir = extract_rir(samples, cfg)
    return rir_to_logmag_spectrum(rir, cfg)
