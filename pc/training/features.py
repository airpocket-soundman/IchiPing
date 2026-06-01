"""IchiPing v1 の特徴量抽出。

励振方式が 2 系統あるので入口を 2 つ持つ:

  chirp 系 (samples_to_features):
    2 s WAV → chirp テンプレートと matched filter で RIR を取り出し →
    128 ms 窓を 2048-pt rFFT → 1024 bin log-magnitude → mean-subtract
    spec.html §3 の元設計。1 状態 1 ショットでも RIR が出る。

  noise 系 (samples_to_noise_features):
    2 s WAV を Welch (2048-pt Hann, 50% overlap, ~30 セグメント) で
    平均パワースペクトル化 → 1024 bin log-magnitude → mean-subtract。
    full_32_train_v1 のような PRBS 白色雑音励振用。matched filter は
    使えない (テンプレートが事前に未知の擬似ランダム系列なので相関で
    強調できない) ため、時間方向にスタッキングして分散を抑える方が
    まっとう。

どちらも出力は 1024 次元 float32、平均 0 に正規化済み。同じ 1D-CNN
backbone を共有できる。device 側 (MCXN947) は PowerQuad-FFT で
rFFT を高速化する想定。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

# Constants tied to the firmware defaults — keep these in sync if you
# change firmware/projects/01_dummy_emitter/main.c or dummy_audio.c.
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
    """chirp 励振用: WAV → matched filter → 1024-bin log-magnitude feature。"""
    rir = extract_rir(samples, cfg)
    return rir_to_logmag_spectrum(rir, cfg)


# ---------------------------------------------------------------------------
# noise 系 (Welch スペクトル)
# ---------------------------------------------------------------------------

# Welch 設定。2 s @ 16 kHz = 32000 サンプルに対して 2048-pt 窓・50% overlap で
# 約 30 セグメント取れる。chirp 経路と同じく 1024 ビン出力に揃える。
NOISE_NPERSEG = 2048
NOISE_NOVERLAP = 1024


def samples_to_logmag_psd(samples: np.ndarray,
                          cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """noise 励振用: WAV → Welch 平均パワー → 1024-bin **絶対** log-magnitude。

    mean-subtract 等の正規化を一切しない生の dB PSD。
    samples_to_noise_features (frame 内 mean-subtract 版) と
    noise_diff 経路 (baseline 引き) の両方の共通前段として使う。
    """
    x = np.asarray(samples, dtype=np.float32)
    f, pxx = sps.welch(
        x,
        fs=cfg.rate_hz,
        window="hann",
        nperseg=NOISE_NPERSEG,
        noverlap=NOISE_NOVERLAP,
        scaling="spectrum",
        return_onesided=True,
    )
    # rFFT(2048) は 1025 ビンになるので、chirp 系と同じく DC を捨てて 1024 ビンに
    mag2 = pxx[1:]
    # 10·log10(power) は 20·log10(mag) と等価。eps でゼロ割回避。
    eps = 1e-12
    db = 10.0 * np.log10(mag2 + eps)
    db = np.maximum(db, DB_FLOOR)
    return db.astype(np.float32)


def samples_to_noise_features(samples: np.ndarray,
                              cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """noise 励振用: WAV → Welch 平均パワー → 1024-bin log-magnitude feature。

    入力 samples は WAV から読んだ float32 (整数も可、内部で同様に扱う)。
    出力は chirp 経路と同じ shape (1024,)、平均 0 に正規化、フロア -80 dB。
    Hann 窓 + 50% overlap で 30 セグメント平均するので、1 フレーム内での
    フレーム内 σ は √30 倍程度抑制される。
    """
    db = samples_to_logmag_psd(samples, cfg)
    db = db - db.mean()
    return db.astype(np.float32)


def samples_to_noise_diff_features(samples: np.ndarray,
                                   baseline_db: np.ndarray,
                                   cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """noise_diff 経路: 生 log-mag から baseline を per-bin で引く。

    baseline_db は同じ shape (1024,) の log-magnitude (mean-subtract しない生 dB)。
    通常は同 captures_dir の s00000 全フレーム平均を使う。
    結果は「ベース (全閉) からのスペクトル偏差 (dB)」になり、ゼロ近傍中心。
    全体音量シフトが偏差から消えるので cross-run の SPK ドリフトに強い想定。
    """
    db = samples_to_logmag_psd(samples, cfg)
    if baseline_db.shape != db.shape:
        raise ValueError(f"baseline shape {baseline_db.shape} != feature shape {db.shape}")
    return (db - baseline_db).astype(np.float32)


def samples_to_noise_diff_norm_features(samples: np.ndarray,
                                        baseline_db: np.ndarray,
                                        cfg: FeatureConfig = FeatureConfig()) -> np.ndarray:
    """noise_diff_norm 経路: noise_diff の per-frame zero-mean unit-variance 正規化版。

    意図: 推論時に SPK 音量を変えて SNR を稼ぐ運用に対応する。
      - 学習データは固定音量 (vol=3) で採取
      - 推論時に環境ノイズが大きい → vol を上げたい
      - per-frame で標準化することで weight は「形状」のみを学習、
        レベル軸の自由度を持つようになる

    具体的に:
      feat = db - baseline_db                      # 通常 noise_diff
      feat = feat - feat.mean()                    # global level 除去
      feat = feat / (feat.std() + 1e-6)            # 標準偏差で割って正規化

    firmware 側にも同等の処理 (ichp_features_normalize_frame) を入れる必要あり。
    """
    feat = samples_to_noise_diff_features(samples, baseline_db, cfg)
    feat = feat - feat.mean()
    std = float(feat.std() + 1e-6)
    return (feat / std).astype(np.float32)
