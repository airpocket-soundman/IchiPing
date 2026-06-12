"""図1: ping 白色雑音 vs 室内録音 (s00000) の STFT / FFT 並置比較。

フラットな白色雑音 (ping) が部屋に入る (マイクで受ける) ことで室共鳴により
周波数ごとに強度が変化し「色付く」様子を示す資料図 (docs/figures_spec.md 図1)。

  1a: STFT 横並び 2 枚 (左 = ping PRBS 合成、右 = 録音 wav)
  1b: FFT 重ね描き (各系列を中央値 0 dB に正規化してスペクトル形状だけ比較)

ping 側は firmware の xorshift32 PRBS を厳密複製した合成波形
(gen_ping_figures.xorshift32_prbs)。実機 seed は再現不可だが統計的に
フラットなスペクトルはシード非依存 (figures_spec.md「ping 図の注意」参照)。

状態ラベルは h 表記 (間取り順、state_labels.py 参照) で表示する。
wav パスのディレクトリ名は s 表記のまま (データ正本は s)。

使い方:
  uv run --extra training python gen_ping_vs_room.py \
      --wav captures/full_32_eval_v1/s00000/frame_000000.wav \
      --label h00000 \
      --out-stft ../docs/img/ping_vs_h00000_stft.png \
      --out-fft ../docs/img/ping_vs_h00000_fft.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibrator import _compute_avg_fft  # noqa: E402
from gen_fftdiff_band import load_wav  # noqa: E402
from gen_ping_figures import DURATION_MS, SAMPLE_RATE_HZ, xorshift32_prbs  # noqa: E402

FMIN_HZ = 50.0   # calibrator._save_spectrogram_plot と同じ下限
FMAX_HZ = 8000.0


def stft_db(samples_f: np.ndarray, rate: int):
    from scipy import signal
    f, t, sxx = signal.spectrogram(samples_f, fs=rate, nperseg=1024, noverlap=512)
    return f, t, 10.0 * np.log10(np.maximum(sxx, 1e-10))


def plot_stft_pair(ping_f, rec_f, rec_rate: int, rec_label: str, out_path: Path):
    """1a: ping / 録音 の STFT を横並びで描く。

    振幅基準が両者で全く違う (合成 ±1 vs int16 正規化) ため、カラースケールは
    各パネル独立。比較すべきは絶対レベルではなく「縦縞の無い均一さ vs
    室共鳴による横縞構造」。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    panels = [
        (axes[0], ping_f, SAMPLE_RATE_HZ, "ping (PRBS white noise, synthetic)"),
        (axes[1], rec_f, rec_rate, f"{rec_label} recording (room-colored)"),
    ]
    for ax, samples, rate, title in panels:
        f, t, sdb = stft_db(samples, rate)
        im = ax.pcolormesh(t, f, sdb, shading="gouraud", cmap="magma")
        ax.set_yscale("log")
        ax.set_ylim(FMIN_HZ, FMAX_HZ)
        ax.set_xlabel("Time (s)")
        ax.set_title(title)
        fig.colorbar(im, ax=ax, label="dB")
    axes[0].set_ylabel("Frequency (Hz)")
    fig.suptitle("ping (flat) vs room recording (colored by room resonance) — STFT")
    plt.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"saved {out_path}")


def plot_fft_overlay(ping_f, rec_f, rec_rate: int, rec_label: str, out_path: Path):
    """1b: ping / 録音 の平均 FFT を重ね描き。

    絶対レベル差を消すため各系列の中央値を 0 dB に正規化し、
    「ほぼフラット」vs「共鳴ピーク/ディップ」の形状差だけを見せる。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freqs_p, mag_p = _compute_avg_fft(ping_f, SAMPLE_RATE_HZ)
    freqs_r, mag_r = _compute_avg_fft(rec_f, rec_rate)
    db_p = 20.0 * np.log10(np.maximum(mag_p, 1e-10))
    db_r = 20.0 * np.log10(np.maximum(mag_r, 1e-10))
    db_p -= np.median(db_p)
    db_r -= np.median(db_r)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogx(freqs_p, db_p, lw=0.8, color="#1f77b4",
                label="ping (PRBS white noise, synthetic)")
    ax.semilogx(freqs_r, db_r, lw=0.8, color="#ff7f0e", alpha=0.85,
                label=f"{rec_label} recording (room-colored)")
    ax.set_xlim(20, FMAX_HZ)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB, median-normalized)")
    ax.set_title("ping (flat) vs room recording — average FFT")
    ax.legend(loc="lower left")
    ax.grid(True, which="both", alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"saved {out_path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path, required=True, help="録音側 wav (s00000)")
    ap.add_argument("--label", default="h00000")
    ap.add_argument("--out-stft", type=Path, required=True)
    ap.add_argument("--out-fft", type=Path, required=True)
    args = ap.parse_args(argv)

    n = SAMPLE_RATE_HZ * DURATION_MS // 1000
    ping_f = xorshift32_prbs(n)
    rec_rate, rec_f = load_wav(args.wav)

    for p in (args.out_stft, args.out_fft):
        p.parent.mkdir(parents=True, exist_ok=True)
    plot_stft_pair(ping_f, rec_f, rec_rate, args.label, args.out_stft)
    plot_fft_overlay(ping_f, rec_f, rec_rate, args.label, args.out_fft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
