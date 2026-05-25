"""モデルサイズ × seed の sweep 実験。

4 size (S/M/L/XL) × N seed の組合せで 14cls noise_diff + feature_aug を学習し、
それぞれ train_v1 (全 960)、noise_v1 (cross-day 32) で評価する。
結果を CSV と JSON にまとめて mean ± std で size 間の真の差を判定する。

Usage:
    cd pc
    uv run --extra training python sweep_size_seed.py --seeds 0 1 2 3 4

各 run の所要は ~2 min なので 4×5 = 20 runs ≒ 40 分。
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np


SIZES = ("S", "M", "L", "XL")
ROOT = Path(__file__).resolve().parent
# 既定 = v1 のみ。--train-dirs で v1+v2 結合学習に切替可能。
DEFAULT_TRAIN_DIRS = [ROOT / "captures" / "full_32_train_v1"]
CAPTURES_XRUN  = ROOT / "captures" / "full_32_noise_v1"
RUNS_ROOT_DEFAULT = ROOT / "runs" / "sweep_size_seed"


def run_one(size: str, seed: int, epochs: int,
            train_dirs: list[Path], runs_root: Path,
            head_type: str = "14cls",
            ambient_dirs: list[Path] | None = None,
            spike_fix: bool = False) -> dict:
    """1 つの (size, seed) で学習 + 各データセット評価。

    head_type: "14cls" or "32cls" — どちらの train / infer スクリプトを使うか。
    ambient_dirs: 指定されたら NoiseOverlay augmentation のソースとして使う。
    どちらも report.json に acc_14cls / macro_f1_14cls を持つので集計形式は共通。
    """
    train_mod = f"training.train_{head_type}"
    infer_mod = f"training.infer_{head_type}"
    out_dir = runs_root / f"size_{size}_seed_{seed}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 学習 (train_dirs に列挙された captures を全部使う) ---
    train_cmd = [
        "uv", "run", "--extra", "training", "python", "-m", train_mod,
        "--captures", *[str(p) for p in train_dirs],
        "--out", str(out_dir),
        "--epochs", str(epochs),
        "--feature-mode", "noise_diff",
        "--feature-aug",
        "--size", size,
        "--seed", str(seed),
    ]
    if ambient_dirs:
        train_cmd += ["--ambient-dirs", *[str(p) for p in ambient_dirs]]
    if spike_fix:
        train_cmd += ["--spike-fix"]
    print(f"\n[head={head_type} size={size} seed={seed}] training on {[p.name for p in train_dirs]}"
          + (f" + ambient={[p.name for p in ambient_dirs]}" if ambient_dirs else "")
          + (" + spike-fix" if spike_fix else "")
          + "...")
    res = subprocess.run(train_cmd, cwd=str(ROOT), capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout[-500:])
        print(res.stderr[-500:])
        raise RuntimeError(f"train failed: head={head_type} size={size} seed={seed}")

    # --- 評価ヘルパ ---
    def _eval(captures: Path, eval_out: Path) -> dict:
        eval_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            "uv", "run", "--extra", "training", "python", "-m", infer_mod,
            "--captures", str(captures),
            "--ckpt", str(out_dir / "best.pt"),
            "--out", str(eval_out),
            "--feature-mode", "noise_diff",
            "--split", "all",
        ]
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"eval failed: {captures}")
        report = json.loads((eval_out / "report.json").read_text(encoding="utf-8"))
        return report

    # 学習に使った各 captures dir を個別に評価 (in-distribution sanity)
    in_dist_reports = {}
    for tdir in train_dirs:
        rep = _eval(tdir, out_dir / f"eval_{tdir.name}")
        in_dist_reports[tdir.name] = rep["acc_14cls"]

    # cross-day reference: noise_v1 (常に評価)
    xrun_report = _eval(CAPTURES_XRUN, out_dir / "eval_xrun_noise_v1")

    cfg = json.loads((out_dir / "config.json").read_text())
    summary = {
        "size":     size,
        "seed":     seed,
        "n_params": cfg["n_params"],
        "in_dist":  in_dist_reports,
        "xrun_acc": xrun_report["acc_14cls"],
        "xrun_macro_f1": xrun_report["macro_f1_14cls"],
    }
    print(f"  size={size} seed={seed}: in_dist={in_dist_reports}, "
          f"xrun(noise_v1)={xrun_report['acc_14cls']:.3f}")
    return summary


def summarize(results: list[dict]) -> dict:
    """size ごとに mean / std / min / max を集計。"""
    by_size: dict[str, list[dict]] = {}
    for r in results:
        by_size.setdefault(r["size"], []).append(r)

    summary = {}
    for size, rows in by_size.items():
        xrun_accs  = [r["xrun_acc"] for r in rows]
        # in_dist は dict のため、各 captures dir の平均を計算
        captures_keys = list(rows[0]["in_dist"].keys())
        in_dist_summary = {}
        for k in captures_keys:
            accs = [r["in_dist"][k] for r in rows]
            in_dist_summary[k] = {
                "mean": statistics.mean(accs),
                "std":  statistics.stdev(accs) if len(accs) > 1 else 0.0,
            }
        summary[size] = {
            "n_seeds": len(rows),
            "n_params": rows[0]["n_params"],
            "in_dist": in_dist_summary,
            "xrun_acc_mean":  statistics.mean(xrun_accs),
            "xrun_acc_std":   statistics.stdev(xrun_accs) if len(xrun_accs) > 1 else 0.0,
            "xrun_acc_min":   min(xrun_accs),
            "xrun_acc_max":   max(xrun_accs),
            "seeds": [r["seed"] for r in rows],
            "xrun_per_seed":  xrun_accs,
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Size × seed sweep for 14cls.")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4],
                    help="評価する seed のリスト (既定 0..4)")
    ap.add_argument("--sizes", nargs="+", default=list(SIZES),
                    choices=SIZES, help="評価する size (既定 S M L XL 全部)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--train-dirs", type=Path, nargs="+", default=DEFAULT_TRAIN_DIRS,
                    help="学習に使う captures dir (複数可)。既定 = v1 のみ。"
                         "v1+v2 結合学習: --train-dirs captures/full_32_train_v1 captures/full_32_train_v2")
    ap.add_argument("--out-name", default=None,
                    help="出力サブディレクトリ名 (既定 = 自動生成)。例: sweep_v1v2_combined")
    ap.add_argument("--head-type", choices=("14cls", "32cls"), default="14cls",
                    help="出力 head 種別。14cls = 14-class softmax / 32cls = 32-class softmax")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs",
                    help="NoiseOverlay augmentation のソース dir 群 (rglob で frame_*.wav 収集)")
    ap.add_argument("--spike-fix", action="store_true",
                    help="val_loss spike 対策バンドルを有効化 (各 run の train スクリプトに渡す)")
    args = ap.parse_args()

    runs_root = ROOT / "runs" / (args.out_name or "sweep_size_seed")
    runs_root.mkdir(parents=True, exist_ok=True)
    print(f"sweep: sizes={args.sizes}, seeds={args.seeds}, epochs={args.epochs}")
    print(f"train_dirs: {[p.name for p in args.train_dirs]}")
    print(f"out: {runs_root}")
    print(f"total runs: {len(args.sizes) * len(args.seeds)}")

    results = []
    for size in args.sizes:
        for seed in args.seeds:
            try:
                r = run_one(size, seed, args.epochs, args.train_dirs, runs_root,
                            head_type=args.head_type,
                            ambient_dirs=args.ambient_dirs,
                            spike_fix=args.spike_fix)
                results.append(r)
            except Exception as exc:
                print(f"  ! failed: {exc}")

    # CSV 出力 (in-dist は dict なので展開)
    csv_path = runs_root / "results.csv"
    in_dist_keys = list(results[0]["in_dist"].keys()) if results else []
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["size", "seed", "n_params"]
                   + [f"in_dist_{k}" for k in in_dist_keys]
                   + ["xrun_acc", "xrun_macro_f1"])
        for r in results:
            row = [r["size"], r["seed"], r["n_params"]]
            row += [f"{r['in_dist'][k]:.4f}" for k in in_dist_keys]
            row += [f"{r['xrun_acc']:.4f}", f"{r['xrun_macro_f1']:.4f}"]
            w.writerow(row)
    print(f"\nresults CSV: {csv_path}")

    summary = summarize(results)
    summary_path = runs_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(f"\n=== 集計: size 別 cross-day (noise_v1) mean ± std ===")
    print(f"  {'size':<4} {'params':>7} {'xrun_mean':>10} {'xrun_std':>9} "
          f"{'xrun_min':>9} {'xrun_max':>9}")
    for size in args.sizes:
        if size not in summary:
            continue
        s = summary[size]
        print(f"  {size:<4} {s['n_params']:>7} "
              f"{s['xrun_acc_mean']:>10.4f} {s['xrun_acc_std']:>9.4f} "
              f"{s['xrun_acc_min']:>9.4f} {s['xrun_acc_max']:>9.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
