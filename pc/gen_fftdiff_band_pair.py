"""2 状態 vs 共通ベースラインの FFT 比較 + 各 diff + diff 帯カラーチャート並置。

gen_fftdiff_band.py (1 ペア版) の拡張。2 つの state を共通ベースライン
(通常 s00000 全閉) と比較し、「どの帯域がどちらの state で動くか」を
帯カラーチャートの並置で見せる資料図。

レイアウト (縦 4 段、x 軸=周波数 0–8 kHz 線形で共有):
  段1: ベースライン + 両 state の平均 FFT (Welch PSD, dB) を重ね描き
  段2: 差分線 (stateA − baseline) [dB]、0 基準、正負塗り
  段3: 差分線 (stateB − baseline) [dB]、同上
  段4: 両 diff の帯カラーチャートを 2 行で並置 (coolwarm, ±6 dB 共有)
       → 行同士で色を直接比較できる

使い方:
  uv run --extra training python gen_fftdiff_band_pair.py \
      --baseline captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav-a captures/full_32_eval_v1/s00010/frame_000000.wav --label-a s00010 \
      --wav-b captures/full_32_eval_v1/s00011/frame_000000.wav --label-b s00011 \
      --out ../docs/img/fftdiff_band_s00010_s00011.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_fftdiff_band import (  # noqa: E402
    DIVERGE_CLIM,
    DIVERGE_CMAP,
    FMAX_HZ,
    load_wav,
    welch_psd_db,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path, required=True, help="ベースライン wav (s00000)")
    ap.add_argument("--wav-a", type=Path, required=True)
    ap.add_argument("--wav-b", type=Path, required=True)
    ap.add_argument("--label-base", default="s00000")
    ap.add_argument("--label-a", default="stateA")
    ap.add_argument("--label-b", default="stateB")
    ap.add_argument("--out", type=Path, required=True)
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

    fig = plt.figure(figsize=(10, 11))
    gs = fig.add_gridspec(4, 1, height_ratios=[3, 2, 2, 1.2], hspace=0.32)

    # 段1: FFT 重ね描き (ベースラインは薄いグレーで参照用)
    ax0 = fig.add_subplot(gs[0])
    ax0.plot(freqs, psd_base, lw=0.8, color="#999999", label=args.label_base, alpha=0.8)
    ax0.plot(freqs, psd_a, lw=0.8, color="#1f77b4", label=args.label_a, alpha=0.9)
    ax0.plot(freqs, psd_b, lw=0.8, color="#ff7f0e", label=args.label_b, alpha=0.85)
    ax0.set_ylabel("PSD (dB)")
    ax0.set_title(f"FFT compare + per-state diff vs {args.label_base} + diff bands  |  "
                  f"{args.label_a} / {args.label_b}")
    ax0.legend(loc="upper right")
    ax0.grid(True, alpha=0.3)
    ax0.set_xlim(0, FMAX_HZ)

    # 段2/3: 各 state の差分線 (y 軸レンジは両者共通にして比較可能にする)
    ylim = float(np.max(np.abs(np.concatenate([diff_a, diff_b])))) * 1.1
    diff_panels = [
        (fig.add_subplot(gs[1], sharex=ax0), diff_a, args.label_a, "#1f77b4"),
        (fig.add_subplot(gs[2], sharex=ax0), diff_b, args.label_b, "#ff7f0e"),
    ]
    for ax, diff, label, color in diff_panels:
        ax.axhline(0, color="k", lw=0.6)
        ax.plot(freqs, diff, lw=0.8, color=color)
        ax.fill_between(freqs, diff, 0, where=diff >= 0, color="#d62728", alpha=0.35)
        ax.fill_between(freqs, diff, 0, where=diff < 0, color="#1f5fd6", alpha=0.35)
        ax.set_ylabel(f"Δ PSD (dB)\n{label} − {args.label_base}")
        ax.set_ylim(-ylim, ylim)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, FMAX_HZ)

    # 段4: 両 diff の帯カラーチャートを 2 行並置
    ax3 = fig.add_subplot(gs[3], sharex=ax0)
    bands = np.vstack([diff_a, diff_b])
    im = ax3.imshow(bands, aspect="auto", cmap=DIVERGE_CMAP,
                    vmin=-DIVERGE_CLIM, vmax=DIVERGE_CLIM,
                    extent=[0, FMAX_HZ, 0, 2])
    ax3.set_yticks([0.5, 1.5])
    ax3.set_yticklabels([args.label_b, args.label_a])  # imshow は上が先頭行
    ax3.set_xlabel("Frequency (Hz)")
    cbar = fig.colorbar(im, ax=[ax0, *(p[0] for p in diff_panels), ax3],
                        fraction=0.025, pad=0.02)
    cbar.set_label("Δ PSD (dB)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
