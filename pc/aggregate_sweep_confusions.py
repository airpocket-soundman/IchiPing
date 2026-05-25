"""sweep 内の各 (size, seed) の eval 結果 confusion CSV を size 別に合算 → PNG。

各 size の 5 seed 分の confusion_14cls.csv を足し合わせ、cross-day と
eval_quiet / noise_low / noise_high それぞれで集約 PNG を生成する。

Usage:
    cd pc
    uv run --extra training python aggregate_sweep_confusions.py \\
        --sweep-dir runs/sweep_v1v2v3_14cls
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from training.dataset import CLASS_ORDER_14
from infer_32cls import save_confusion_png, save_confusion_csv


SIZES = ("S", "M", "L", "XL")
SEEDS = (0, 1, 2, 3, 4)
EVAL_SUBDIRS = (
    ("noise_v1",    "eval_xrun_noise_v1"),
    ("quiet",       "eval_quiet"),
    ("noise_low",   "eval_noise_low"),
    ("noise_high",  "eval_noise_high"),
)


def aggregate(sweep_dir: Path, size: str, eval_subdir: str) -> tuple[np.ndarray, int]:
    accum = np.zeros((14, 14), dtype=np.int64)
    n_seeds = 0
    for seed in SEEDS:
        csv_path = sweep_dir / f"size_{size}_seed_{seed}" / eval_subdir / "confusion_14cls.csv"
        if not csv_path.exists():
            continue
        with csv_path.open() as fp:
            rows = list(csv.reader(fp))
        for i in range(14):
            for j in range(14):
                accum[i, j] += int(rows[i + 1][j + 1])
        n_seeds += 1
    return accum, n_seeds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir", type=Path, required=True)
    args = ap.parse_args()

    out_root = args.sweep_dir / "aggregate_confusion"
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"sweep: {args.sweep_dir} → {out_root}")

    for cond_name, eval_sub in EVAL_SUBDIRS:
        for size in SIZES:
            accum, n_seeds = aggregate(args.sweep_dir, size, eval_sub)
            if n_seeds == 0:
                continue
            total = int(accum.sum())
            correct = int(np.trace(accum))
            acc = correct / total if total else 0
            csv_path = out_root / f"{cond_name}_{size}.csv"
            png_path = out_root / f"{cond_name}_{size}.png"
            save_confusion_csv(accum, list(CLASS_ORDER_14), csv_path)
            save_confusion_png(accum, list(CLASS_ORDER_14),
                               f"{cond_name} - size {size}, {n_seeds} seeds, "
                               f"{total} preds, acc {acc:.3f}",
                               png_path, annotate=True)
            print(f"  {cond_name}/{size}: {n_seeds} seeds, {total} preds, acc {acc:.4f}")


if __name__ == "__main__":
    main()
