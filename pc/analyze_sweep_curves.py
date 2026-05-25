"""sweep 20 run の学習曲線を size 別に重ねて可視化、収束/過学習の有無を確認。

各 size につき seed 0-4 を別色で重ねた以下を出力:
  - train_loss / val_loss vs epoch (上段 2 サブプロット)
  - train_acc / val_acc vs epoch (下段 2 サブプロット)

更にクロス比較として:
  - 4 size の val_loss 平均曲線 (size 間で収束特性が違うか)
  - 4 size の cross-day v2 acc per seed の散布

出力: runs/sweep_size_seed/curves/{size}_curves.png + cross_size_compare.png
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
SIZES = ("S", "M", "L", "XL")
SEEDS = (0, 1, 2, 3, 4)
COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd")

# 既定の sweep dir。CLI で上書き可能。
SWEEP = ROOT / "runs" / "sweep_size_seed"
OUT = SWEEP / "curves"


def load_log(size: str, seed: int):
    p = SWEEP / f"size_{size}_seed_{seed}" / "train_log.csv"
    if not p.exists():
        return None
    epochs, tl, ta, vl, va = [], [], [], [], []
    with p.open() as fp:
        r = csv.DictReader(fp)
        for row in r:
            epochs.append(int(row["epoch"]))
            tl.append(float(row["train_loss"]))
            ta.append(float(row["train_acc"]))
            vl.append(float(row["val_loss"]))
            va.append(float(row["val_acc"]))
    return dict(epochs=epochs, tl=tl, ta=ta, vl=vl, va=va)


def plot_size_curves(size: str, out_path: Path):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
    (ax_tl, ax_vl), (ax_ta, ax_va) = axes

    for i, seed in enumerate(SEEDS):
        d = load_log(size, seed)
        if d is None:
            continue
        ax_tl.plot(d["epochs"], d["tl"], color=COLORS[i], linewidth=1.0, alpha=0.9, label=f"seed {seed}")
        ax_vl.plot(d["epochs"], d["vl"], color=COLORS[i], linewidth=1.0, alpha=0.9, label=f"seed {seed}")
        ax_ta.plot(d["epochs"], d["ta"], color=COLORS[i], linewidth=1.0, alpha=0.9, label=f"seed {seed}")
        ax_va.plot(d["epochs"], d["va"], color=COLORS[i], linewidth=1.0, alpha=0.9, label=f"seed {seed}")

    ax_tl.set_title("train loss")
    ax_vl.set_title("val loss")
    ax_ta.set_title("train acc (14cls)")
    ax_va.set_title("val acc (14cls)")
    ax_tl.set_yscale("log")
    ax_vl.set_yscale("log")
    for ax in (ax_tl, ax_vl, ax_ta, ax_va):
        ax.grid(True, alpha=0.3)
    ax_ta.set_ylim(0, 1.02)
    ax_va.set_ylim(0, 1.02)
    ax_ta.set_xlabel("epoch")
    ax_va.set_xlabel("epoch")
    ax_va.legend(fontsize=8, loc="lower right")
    fig.suptitle(f"size {size} — 5 seeds overlay")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_cross_size_compare(out_path: Path):
    """4 size の val_loss / val_acc 平均曲線を重ねる。"""
    fig, (ax_vl, ax_va) = plt.subplots(1, 2, figsize=(13, 5))
    size_colors = {"S": "#1f77b4", "M": "#ff7f0e", "L": "#2ca02c", "XL": "#d62728"}
    for size in SIZES:
        all_vl = []
        all_va = []
        epochs = None
        for seed in SEEDS:
            d = load_log(size, seed)
            if d is None:
                continue
            epochs = d["epochs"]
            all_vl.append(d["vl"])
            all_va.append(d["va"])
        if not all_vl:
            continue
        mean_vl = np.mean(np.stack(all_vl), axis=0)
        mean_va = np.mean(np.stack(all_va), axis=0)
        std_va  = np.std(np.stack(all_va), axis=0)
        ax_vl.plot(epochs, mean_vl, color=size_colors[size], label=size, linewidth=1.5)
        ax_va.plot(epochs, mean_va, color=size_colors[size], label=size, linewidth=1.5)
        ax_va.fill_between(epochs, mean_va - std_va, mean_va + std_va,
                            color=size_colors[size], alpha=0.15)
    ax_vl.set_title("val loss (mean over 5 seeds)")
    ax_va.set_title("val acc (mean ± std over 5 seeds)")
    ax_vl.set_yscale("log")
    ax_vl.set_xlabel("epoch")
    ax_va.set_xlabel("epoch")
    ax_va.set_ylim(0, 1.02)
    for ax in (ax_vl, ax_va):
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_xrun_scatter(out_path: Path):
    """val acc (in-distribution) vs cross-day acc の散布 — overfit 度を可視化。"""
    pts = []
    for size in SIZES:
        for seed in SEEDS:
            d = load_log(size, seed)
            if d is None:
                continue
            best_val = max(d["va"])
            xrun_report = SWEEP / f"size_{size}_seed_{seed}" / _SCATTER_REPORT_REL / "report.json"
            if not xrun_report.exists():
                continue
            xrun_acc = json.loads(xrun_report.read_text())["acc_14cls"]
            pts.append((size, seed, best_val, xrun_acc))
    fig, ax = plt.subplots(figsize=(8, 7))
    size_colors = {"S": "#1f77b4", "M": "#ff7f0e", "L": "#2ca02c", "XL": "#d62728"}
    by_size = {s: ([], []) for s in SIZES}
    for size, seed, v, x in pts:
        by_size[size][0].append(v)
        by_size[size][1].append(x)
        ax.annotate(f"{size}{seed}", (v, x), fontsize=7,
                    xytext=(4, 2), textcoords="offset points")
    for size, (vs, xs) in by_size.items():
        if not vs:
            continue
        ax.scatter(vs, xs, color=size_colors[size], s=80, label=size, alpha=0.8,
                    edgecolors="black", linewidths=0.5)
    # diagonal reference
    lo, hi = 0.55, 1.02
    ax.plot([lo, hi], [lo, hi], color="gray", linestyle="--", alpha=0.5,
             label="y = x (no overfit)")
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
    ax.set_xlabel("best val acc (train_v1 内 in-distribution)")
    ax.set_ylabel("cross-day v2 acc (n=1600)")
    ax.set_title("val (in-distribution) vs cross-day (v2) — overfit 度の可視化")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


_SCATTER_REPORT_REL = "eval_xrun_train_v2"


def main():
    global SWEEP, OUT, _SCATTER_REPORT_REL
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir", type=Path, default=SWEEP,
                    help="sweep root (default: runs/sweep_size_seed)")
    ap.add_argument("--scatter-source", type=str, default="eval_xrun_train_v2",
                    help="val vs xrun scatter で使う eval サブディレクトリ名")
    args = ap.parse_args()
    SWEEP = args.sweep_dir
    OUT = SWEEP / "curves"
    _SCATTER_REPORT_REL = args.scatter_source
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"sweep: {SWEEP}")
    for size in SIZES:
        plot_size_curves(size, OUT / f"{size}_curves.png")
        print(f"  wrote {OUT / f'{size}_curves.png'}")
    plot_cross_size_compare(OUT / "cross_size_mean_curves.png")
    print(f"  wrote {OUT / 'cross_size_mean_curves.png'}")
    plot_xrun_scatter(OUT / f"val_vs_{args.scatter_source}_scatter.png")
    print(f"  wrote {OUT / f'val_vs_{args.scatter_source}_scatter.png'}")


if __name__ == "__main__":
    main()
