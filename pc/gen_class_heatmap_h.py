"""docs 用クラス別 FFT diff ヒートマップを h 表記ラベルで生成する。

docs/img/fft_diff_heatmap_by_class.png (元は full_32_v1 の
analysis/overview/fft_diff_from_baseline_by_class.png のコピー) を、
y 軸ラベルだけ h 表記 (間取り順、state_labels.py 参照) に変換して再生成する。
データ読み込み・FFT・等価クラス順ソートは analyze_full32.py と同一処理。
captures 配下の analysis 出力 (s 表記) はデータ正本としてそのまま残す。

使い方:
  uv run --extra training python gen_class_heatmap_h.py \
      --root captures/full_32_v1 \
      --out ../docs/img/fft_diff_heatmap_by_class.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full32 import (  # noqa: E402
    load_wav,
    make_diff_heatmap,
    sort_by_class,
    whole_fft_db,
)
from state_labels import s_to_h  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("captures/full_32_v1"))
    ap.add_argument("--out", type=Path,
                    default=Path("../docs/img/fft_diff_heatmap_by_class.png"))
    ap.add_argument("--baseline", default="s00000")
    args = ap.parse_args(argv)

    state_dirs = sorted(d for d in args.root.iterdir()
                        if d.is_dir() and d.name.startswith("s"))
    ffts = {}
    for d in state_dirs:
        wavs = sorted(d.glob("*.wav"))
        if not wavs:
            print(f"warning: no wav in {d}, skipping")
            continue
        rate, samples = load_wav(wavs[0])
        ffts[d.name] = whole_fft_db(samples, rate)
    print(f"loaded {len(ffts)} states from {args.root}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    make_diff_heatmap(
        ffts, args.baseline, args.out,
        order=sort_by_class(list(ffts.keys())),
        title_suffix="  — sorted by equivalence class",
        draw_class_dividers=True,
        label_fn=s_to_h,
        ylabel="State (h: c BC b AB a)",
    )
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
