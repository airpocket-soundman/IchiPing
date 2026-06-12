"""全 32 状態の FFT diff 帯カラーチャートを h 表記昇順に縦並べしたヒートマップ。

fftdiff_band 系の図 (gen_fftdiff_band.py / _pair.py) と同一基準:
Welch PSD (nperseg=1024) の dB 差分、x 軸 0–8 kHz 線形、
RdBu_r (0=白)、色域 ±DIVERGE_CLIM dB。

行順は --order で選択:
  h        : h 表記 (間取り順、state_labels.py 参照) の昇順 h00000→h11111 (既定)
  class    : 等価クラス順 (A1→A2→B1..B4→C1..C8、analyze_full32.sort_by_class)。
             クラス境界に太い区切り線、y ラベルにクラスタグを付ける
  subclass : class 順をさらに準等価サブクラスで分割。閉扉の裏のビットは理論上
             観測不能だが実測では微小シグネチャが漏れる (準等価) ため、
             A1/A2 内は BC の開閉、B1..B4 内は窓 c の開閉でグループ化する。
             クラス境界=太線、サブクラス境界=中線
先頭行 h00000 (h 順時) は baseline 自身との diff なので全白 (基準の参照行)。
wav は s 表記ディレクトリから h_to_s() で引く。

使い方:
  uv run --extra training python gen_fftdiff_band_all32.py \
      --root captures/full_32_eval_v1 \
      --out ../docs/img/fftdiff_band_all32_h.png
  uv run --extra training python gen_fftdiff_band_all32.py --order class \
      --out ../docs/img/fftdiff_band_all32_class.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full32 import class_of, sort_by_class  # noqa: E402
from gen_fftdiff_band import (  # noqa: E402
    DIVERGE_CLIM,
    DIVERGE_CMAP,
    FMAX_HZ,
    load_wav,
    welch_psd_db,
)
from state_labels import h_to_s, s_to_h  # noqa: E402


def subclass_of(s: str) -> str:
    """閉扉の裏で「漏れて」見える準等価サブクラスのタグ (s 表記前提)。

    AB=0 → BC の開閉 (BC0/BC1)、AB=1 かつ BC=0 → 窓 c の開閉 (c0/c1)。
    全開口が観測可能な C クラスはサブクラスなし。
    """
    a, b, c, AB, BC = (int(x) for x in s[1:])
    if AB == 0:
        return f"BC{BC}"
    if BC == 0:
        return f"c{c}"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("captures/full_32_eval_v1"))
    ap.add_argument("--out", type=Path,
                    default=Path("../docs/img/fftdiff_band_all32_h.png"))
    ap.add_argument("--clim", type=float, default=DIVERGE_CLIM,
                    help="帯カラーチャートの色域 (±dB)")
    ap.add_argument("--order", choices=("h", "class", "subclass"), default="h",
                    help="行順: h=h 表記昇順 / class=等価クラス順 / "
                         "subclass=等価クラス + 準等価サブクラス順")
    args = ap.parse_args(argv)

    row_subtags = None
    if args.order in ("class", "subclass"):
        # 等価クラス判定は s 表記前提なので s でソートし、表示だけ h にする
        s_all = ["s" + format(v, "05b") for v in range(32)]
        s_sorted = sort_by_class(s_all)
        if args.order == "subclass":
            # class_of の文字列順 (A1<A2<B1..<C8) は sort_by_class と同順
            s_sorted = sorted(s_all, key=lambda s: (class_of(s), subclass_of(s), s))
            row_subtags = [subclass_of(s) for s in s_sorted]
        h_labels = [s_to_h(s) for s in s_sorted]
        row_tags = [class_of(s) for s in s_sorted]
    else:
        h_labels = ["h" + format(v, "05b") for v in range(32)]
        row_tags = None

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
        # クラス境界=太線、(subclass 順時) サブクラス境界=中線、その他=細線
        cls_boundary = row_tags is not None and row_tags[i] != row_tags[i - 1]
        sub_boundary = (not cls_boundary and row_subtags is not None
                        and row_subtags[i] != row_subtags[i - 1])
        if cls_boundary:
            ax.axhline(i, color="#1a1d23", lw=1.4)
        elif sub_boundary:
            ax.axhline(i, color="#1a1d23", lw=0.8, linestyle=(0, (4, 2)))
        else:
            ax.axhline(i, color="#444444", lw=0.4)
    ax.set_yticks(np.arange(n) + 0.5)
    if row_subtags is not None:
        ytick_labels = [f"{t:<3}{st:<4} {h}"
                        for t, st, h in zip(row_tags, row_subtags, h_labels)]
        order_note = "class + quasi-subclass order"
    elif row_tags is not None:
        ytick_labels = [f"{t:<3} {h}" for t, h in zip(row_tags, h_labels)]
        order_note = "equivalence-class order"
    else:
        ytick_labels = h_labels
        order_note = "h-order"
    ax.set_yticklabels(ytick_labels, fontsize=7, fontfamily="monospace")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("State (h: c BC b AB a)")
    ax.set_title(f"FFT diff vs h00000 — all 32 states, {order_note}  (±{args.clim:g} dB)")
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
