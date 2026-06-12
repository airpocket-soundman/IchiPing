"""図2: s00000 vs s00001 の STFT と差分 (特徴分離の可視化)。

状態差 (扉1枚の開閉, s00000→s00001) が STFT 差分として分離抽出でき、
状態固有の特徴だけが残ることを示す資料図 (docs/figures_spec.md 図2)。
noise_diff 特徴量の妥当性の可視化。

縦 3 段 (x=時間, y=周波数 log で共有):
  2a: s00000 の STFT
  2b: s00001 の STFT
  2c: STFT 差分 (s00001 − s00000) [dB]、0 中心の発散カラーマップ (RdBu_r, 0=白)

2a/2b はカラースケールを共通化して比較可能にする。2c の色域は
|diff| の 99 パーセンタイルから対称に決める (独立録音同士の per-bin 差分は
分散が大きいため、固定 ±6 dB では飽和しやすい)。

状態ラベルは h 表記 (間取り順、state_labels.py 参照) で表示する。
wav パスのディレクトリ名は s 表記のまま (データ正本は s)。

使い方:
  uv run --extra training python gen_stft_diff.py \
      --wav0 captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav1 captures/full_32_eval_v1/s00001/frame_000000.wav \
      --label0 h00000 --label1 h01000 \
      --out ../docs/img/stft_diff_h00000_vs_h01000.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_fftdiff_band import load_wav  # noqa: E402

FMIN_HZ = 50.0
FMAX_HZ = 8000.0
DIVERGE_CMAP = "RdBu_r"  # 0 = 白背景 (coolwarm は 0 がグレーになるため不採用)


def stft_db(samples_f: np.ndarray, rate: int):
    from scipy import signal
    f, t, sxx = signal.spectrogram(samples_f, fs=rate, nperseg=1024, noverlap=512)
    return f, t, 10.0 * np.log10(np.maximum(sxx, 1e-10))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav0", type=Path, required=True, help="基準 state の wav")
    ap.add_argument("--wav1", type=Path, required=True, help="比較 state の wav")
    ap.add_argument("--label0", default="h00000")
    ap.add_argument("--label1", default="h01000")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    r0, s0 = load_wav(args.wav0)
    r1, s1 = load_wav(args.wav1)
    if r0 != r1:
        print(f"error: sample rate mismatch ({r0} vs {r1})", file=sys.stderr)
        return 2
    f, t0, sdb0 = stft_db(s0, r0)
    _, t1, sdb1 = stft_db(s1, r1)
    n_t = min(sdb0.shape[1], sdb1.shape[1])  # フレーム長差は末尾を切って揃える
    sdb0, sdb1, t = sdb0[:, :n_t], sdb1[:, :n_t], t0[:n_t]
    diff = sdb1 - sdb0

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fmask = f >= FMIN_HZ
    vmin = np.percentile(np.concatenate([sdb0[fmask], sdb1[fmask]]), 2)
    vmax = np.percentile(np.concatenate([sdb0[fmask], sdb1[fmask]]), 99.5)
    dlim = float(np.percentile(np.abs(diff[fmask]), 99))

    fig, axes = plt.subplots(3, 1, figsize=(10, 12), sharex=True, sharey=True)
    for ax, sdb, title in ((axes[0], sdb0, f"2a  STFT — {args.label0}"),
                           (axes[1], sdb1, f"2b  STFT — {args.label1}")):
        im = ax.pcolormesh(t, f, sdb, shading="gouraud", cmap="magma",
                           vmin=vmin, vmax=vmax)
        ax.set_yscale("log")
        ax.set_ylim(FMIN_HZ, FMAX_HZ)
        ax.set_ylabel("Frequency (Hz)")
        ax.set_title(title)
        fig.colorbar(im, ax=ax, label="dB")

    ax = axes[2]
    im = ax.pcolormesh(t, f, diff, shading="gouraud", cmap=DIVERGE_CMAP,
                       vmin=-dlim, vmax=dlim)
    ax.set_yscale("log")
    ax.set_ylim(FMIN_HZ, FMAX_HZ)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title(f"2c  STFT diff ({args.label1} − {args.label0})")
    fig.colorbar(im, ax=ax, label="Δ dB")

    fig.suptitle("state difference isolated by STFT diff "
                 f"({args.label0} vs {args.label1})")
    plt.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    plt.close(fig)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
