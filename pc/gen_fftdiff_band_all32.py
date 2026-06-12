"""全 32 状態の FFT diff 帯カラーチャートを h 表記昇順に縦並べしたヒートマップ。

fftdiff_band 系の図 (gen_fftdiff_band.py / _pair.py) と同一基準:
Welch PSD (nperseg=1024) の dB 差分、x 軸 0–8 kHz 線形、
RdBu_r (0=白)、色域 ±DIVERGE_CLIM dB。

行順は h 表記 (間取り順、state_labels.py 参照) の昇順 h00000→h11111。
先頭行 h00000 は baseline 自身との diff なので全白 (基準の参照行)。
wav は s 表記ディレクトリから h_to_s() で引く。

使い方:
  uv run --extra training python gen_fftdiff_band_all32.py \
      --root captures/full_32_eval_v1 \
      --out ../docs/img/fftdiff_band_all32_h.png
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
from state_labels import h_to_s  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("captures/full_32_eval_v1"))
    ap.add_argument("--out", type=Path,
                    default=Path("../docs/img/fftdiff_band_all32_h.png"))
    ap.add_argument("--clim", type=float, default=DIVERGE_CLIM,
                    help="帯カラーチャートの色域 (±dB)")
    args = ap.parse_args(argv)

    h_labels = ["h" + format(v, "05b") for v in range(32)]

    freqs = None
    psds = {}
    for h in h_labels:
        wav = args.root / h_to_s(h) / "frame_000000.wav"
        if not wav.exists():
            print(f"error: {wav} not found", file=sys.stderr)
            return 2
        rate, samples = load_wav(wav)
        f, psd = welch_psd_db(samples, rate)
        if freqs is None:
            m = f <= FMAX_HZ
            freqs = f[m]
        psds[h] = psd[: freqs.size]

    base = psds["h00000"]
    diff = np.vstack([psds[h] - base for h in h_labels])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(h_labels)
    fig, ax = plt.subplots(figsize=(11, 9))
    # extent の y は上が先頭行になるよう (n → 0) で指定
    im = ax.imshow(diff, aspect="auto", cmap=DIVERGE_CMAP,
                   vmin=-args.clim, vmax=args.clim,
                   extent=[0, FMAX_HZ, n, 0], interpolation="nearest")
    for i in range(1, n):
        ax.axhline(i, color="#444444", lw=0.4)
    ax.set_yticks(np.arange(n) + 0.5)
    ax.set_yticklabels(h_labels, fontsize=7, fontfamily="monospace")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("State (h: c BC b AB a)")
    ax.set_title(f"FFT diff vs h00000 — all 32 states, h-order  (±{args.clim:g} dB)")
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Δ PSD (dB)")

    plt.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    plt.close(fig)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
