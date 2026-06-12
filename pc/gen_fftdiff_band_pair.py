"""2 状態 vs 共通ベースラインの FFT 比較 + diff 重ね描き + diff 帯カラーチャート並置。

gen_fftdiff_band.py (1 ペア版) の拡張。2 つの state を共通ベースライン
(通常 s00000 全閉) と比較し、「どの帯域がどちらの state で動くか」を
帯カラーチャートの並置で見せる資料図。中段の 2 本の diff 線が
学習に用いる noise_diff 特徴量そのものに対応する。

レイアウト (縦 3 段、x 軸=周波数 0–8 kHz 線形で共有):
  段1: 両 state の平均 FFT (Welch PSD, dB) を重ね描き (2 本)
  段2: 差分線 (stateA − baseline) と (stateB − baseline) を同一パネルに重ね描き
  段3: 両 diff の帯カラーチャートを 2 行で並置 (RdBu_r 0=白, 色域共有)。
       各帯は 1 次元 (y 方向は一様)、間に区切りの横線を引く

状態ラベルは h 表記 (間取り順、state_labels.py 参照) で表示する。
wav パスのディレクトリ名は s 表記のまま (データ正本は s)。

使い方:
  uv run --extra training python gen_fftdiff_band_pair.py \
      --baseline captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav-a captures/full_32_eval_v1/s00010/frame_000000.wav --label-a h00010 \
      --wav-b captures/full_32_eval_v1/s00011/frame_000000.wav --label-b h01010 \
      --out ../docs/img/fftdiff_band_h00010_h01010.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_fftdiff_band import (  # noqa: E402
    DIVERGE_CMAP,
    FMAX_HZ,
    load_wav,
    welch_psd_db,
)

# 帯カラーチャートの色域 (±dB)。full_32_eval_v1 の全 31 状態 vs s00000 の diff の
# 最大は 25.3 dB (s10110) だが、小さい変化の視認性を優先して ±10 dB とする
# (10 dB 超の bin は端の色にクリップ)
DEFAULT_CLIM_DB = 10.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path, required=True, help="ベースライン wav (s00000)")
    ap.add_argument("--wav-a", type=Path, required=True)
    ap.add_argument("--wav-b", type=Path, required=True)
    ap.add_argument("--label-base", default="h00000")
    ap.add_argument("--label-a", default="stateA")
    ap.add_argument("--label-b", default="stateB")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--clim", type=float, default=DEFAULT_CLIM_DB,
                    help="帯カラーチャートの色域 (±dB)")
    args = ap.parse_args(argv)

    rb, sb = load_wav(args.baseline)
    ra, sa = load_wav(args.wav_a)
    rc, sc = load_wav(args.wav_b)
    if not (rb == ra == rc):
        print(f"error: sample rate mismatch ({rb}/{ra}/{rc})", file=sys.stderr)
        return 2
    freqs, psd_base = welch_psd_db(sb, rb)
    _, psd_a = welch_psd_db(sa, ra)
    _, psd_b = welch_psd_db(sc, rc)

    m = freqs <= FMAX_HZ
    freqs = freqs[m]
    psd_base, psd_a, psd_b = psd_base[m], psd_a[m], psd_b[m]
    diff_a = psd_a - psd_base
    diff_b = psd_b - psd_base

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(10, 9))
    # 右列はカラーバー専用 (最下段のみ使用)。主要 3 軸の幅を揃えて x 軸を整列させる
    gs = fig.add_gridspec(3, 2, height_ratios=[3, 2, 1.2],
                          width_ratios=[1, 0.02], hspace=0.30, wspace=0.04)

    # 段1: 両 state の FFT 重ね描き (2 本)
    ax0 = fig.add_subplot(gs[0, 0])
    ax0.plot(freqs, psd_a, lw=0.8, color="#1f77b4", label=args.label_a, alpha=0.9)
    ax0.plot(freqs, psd_b, lw=0.8, color="#ff7f0e", label=args.label_b, alpha=0.85)
    ax0.set_ylabel("PSD (dB)")
    ax0.set_title(f"FFT compare + diff vs {args.label_base} + diff bands  |  "
                  f"{args.label_a} / {args.label_b}")
    ax0.legend(loc="upper right")
    ax0.grid(True, alpha=0.3)
    ax0.set_xlim(0, FMAX_HZ)

    # 段2: 2 本の diff 線を同一パネルに重ね描き (= noise_diff 特徴量)
    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax1.axhline(0, color="k", lw=0.6)
    ax1.plot(freqs, diff_a, lw=0.8, color="#1f77b4",
             label=f"{args.label_a} diff")
    ax1.plot(freqs, diff_b, lw=0.8, color="#ff7f0e", alpha=0.85,
             label=f"{args.label_b} diff")
    ax1.set_ylabel(f"Δ PSD (dB)\nvs {args.label_base}")
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, FMAX_HZ)

    # 段3: 両 diff の帯カラーチャートを 2 行並置。
    # interpolation="nearest" で行内 (y 方向) を完全一様に保ち、境界に横線を引く
    ax2 = fig.add_subplot(gs[2, 0], sharex=ax0)
    bands = np.vstack([diff_a, diff_b])
    im = ax2.imshow(bands, aspect="auto", cmap=DIVERGE_CMAP,
                    vmin=-args.clim, vmax=args.clim,
                    extent=[0, FMAX_HZ, 0, 2], interpolation="nearest")
    ax2.axhline(1, color="k", lw=1.5)
    ax2.set_yticks([0.5, 1.5])
    ax2.set_yticklabels([args.label_b, args.label_a])  # imshow は上が先頭行
    ax2.set_xlabel("Frequency (Hz)")
    cax = fig.add_subplot(gs[2, 1])
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("Δ PSD (dB)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
