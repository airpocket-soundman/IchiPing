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
        # rglob でサブディレクトリも辿る (例: captures/full_32_passive_v1/sXXXXX/frame_*.wav)
        for d in noise_dirs:
            if not d.exists():
                continue
            for wav_path in sorted(d.rglob("frame_*.wav")):
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
# Feature-space transforms (1024-bin 特徴量に対する augmentation)
# ---------------------------------------------------------------------------
#
# 上で定義した波形空間の transform は chirp 励振用 (TimeShift で chirp 開始
# タイミング揺らぎ等を補える)。PRBS ノイズ励振では shift-invariant のため
# TimeShift がほぼ無効、LevelJitter も BatchNorm で吸収される。そこで
# Welch スペクトル化後の特徴量に対して直接効かせる SpecAugment 系を別途用意。


FeatureTransform = Callable[[np.ndarray], np.ndarray]


class FeatureCompose:
    """feature 空間の transform を順次適用するコンテナ。"""

    def __init__(self, transforms: Sequence[FeatureTransform]) -> None:
        self.transforms = list(transforms)

    def __call__(self, feats: np.ndarray) -> np.ndarray:
        for t in self.transforms:
            feats = t(feats)
        return feats


class FreqMask:
    """SpecAugment 風の周波数マスク。連続する bin 区間を 0 に置き換える。

    1024 bin の log-mag 特徴量を想定。max_width 個までの連続 bin を 0 化することを
    n_masks 回繰り返す。0 化は「この帯域はベース (mean-subtract 後の中央値 / diff の
    場合は baseline 一致) に張りつく」と等価で、単一 bin に過剰依存する学習を防ぐ。
    """

    def __init__(self, max_width: int = 60, n_masks: int = 2,
                 p: float = 0.7) -> None:
        self.max_width = int(max_width)
        self.n_masks = int(n_masks)
        self.p = float(p)

    def __call__(self, feats: np.ndarray) -> np.ndarray:
        if random.random() > self.p:
            return feats
        out = feats.copy()
        n = out.shape[-1]
        for _ in range(self.n_masks):
            w = random.randint(1, max(1, self.max_width))
            start = random.randint(0, max(0, n - w))
            out[..., start:start + w] = 0.0
        return out


class SpectralJitter:
    """各 bin に独立な Gaussian dB ジッタを加える。

    SPK 周波数応答の日々のドリフトを擬似化。sigma_db = 0.3 dB は実測の
    cross-day intra-class σ_state (約 0.4–0.6 dB) と同オーダー。
    """

    def __init__(self, sigma_db: float = 0.3, p: float = 1.0) -> None:
        self.sigma_db = float(sigma_db)
        self.p = float(p)

    def __call__(self, feats: np.ndarray) -> np.ndarray:
        if random.random() > self.p:
            return feats
        noise = np.random.normal(0.0, self.sigma_db,
                                  size=feats.shape).astype(feats.dtype)
        return feats + noise


def default_feature_transform(
    feature_mode: str = "noise_diff",
    spike_fix: bool = False,
) -> FeatureCompose:
    """noise 系特徴量向けの既定 augmentation 連鎖。

    chirp モードでは feature 後段ではなく波形段で十分なので、
    既定では noise / noise_diff にのみ適用する想定 (呼び出し側で制御)。

    spike_fix=True で FreqMask max_width を 60→40 に縮める (val_loss spike 対策の一部)。
    """
    freqmask_width = 40 if spike_fix else 60
    return FeatureCompose([
        FreqMask(max_width=freqmask_width, n_masks=2, p=0.7),
        SpectralJitter(sigma_db=0.3, p=0.8),
    ])


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

    ambient_dirs に WAV プールが指定されていれば、実 ambient を強めの SNR で overlay。
    None なら合成 GaussianHiss でフォールバック。
    realistic ambient (TV / 会話など) を学習に取り込みたい場合は
    captures/full_32_passive_v1 など SPK off で撮った dir を渡す。
    """
    steps: List[Transform] = [
        TimeShift(max_ms=10.0, p=0.7),
        LevelJitter(db_range=2.0, p=0.7),
    ]
    if ambient_dirs:
        # ambient を確実に効かせるため SNR 5-25 dB (=ambient が信号と同等〜やや小)
        # で確率 0.7 で overlay。
        steps.append(NoiseOverlay(ambient_dirs, snr_db_range=(5.0, 25.0), p=0.7))
    else:
        steps.append(GaussianHiss(snr_db_range=(15.0, 35.0), p=0.5))
    return Compose(steps)


def strong_train_transform(
    ambient_dirs: Optional[Sequence[Path]] = None,
) -> Compose:
    """汎化性能強化版: 全パラメタを強めに振って高ノイズ耐性を取りに行く。

    用途: v6-v10 のように低ノイズ条件でしか採取されてないデータで、実推論の
    多様な室内環境 (TV / 会話 / 空調) に対する汎化が不十分なときに使う。

    変更点 (default_train_transform からの差):
      - TimeShift: ±10ms → ±20ms (chirp 開始ずれ余裕拡大)
      - LevelJitter: ±2dB → ±4dB (SPK 音量 / mic gain ドリフト想定拡大)
      - NoiseOverlay: SNR 5-25dB → SNR 0-35dB、p 0.7 → 0.9
        (信号と等強度のノイズまで含めて学習させる)
      - GaussianHiss 追加 (overlay 有無に関わらず、SNR 10-40dB, p=0.5)
        実 ambient と合成 hiss の両方を経験させる
    """
    steps: List[Transform] = [
        TimeShift(max_ms=20.0, p=0.8),
        LevelJitter(db_range=4.0, p=0.8),
    ]
    if ambient_dirs:
        steps.append(NoiseOverlay(ambient_dirs, snr_db_range=(0.0, 35.0), p=0.9))
    steps.append(GaussianHiss(snr_db_range=(10.0, 40.0), p=0.5))
    return Compose(steps)


def strong_feature_transform(
    feature_mode: str = "noise_diff",
    spike_fix: bool = False,
) -> FeatureCompose:
    """feature 空間の強化版 augmentation。

    default_feature_transform からの差:
      - FreqMask: max_width 40→80, n_masks 2→3, p 0.7→0.85
        広帯域に依存する特徴量も "落ちる" 経験を学習
      - SpectralJitter: σ 0.3dB → 0.6dB, p 0.8 → 0.9
        SPK 周波数応答の長期ドリフト幅をより広く擬似化
    """
    return FeatureCompose([
        FreqMask(max_width=80, n_masks=3, p=0.85),
        SpectralJitter(sigma_db=0.6, p=0.9),
    ])
