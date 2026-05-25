"""sweep の 20 モデルを full_32_train_v2 (cross-day n=1600) で評価。

各 size × seed の best.pt をロードして、新規収集 v2 データセットに対する
14cls accuracy を測定する。結果を CSV にまとめて size 間の真の差を判定する。

Usage:
    cd pc
    uv run --extra training python eval_sweep_v2.py
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SWEEP_ROOT = ROOT / "runs" / "sweep_size_seed"
EVAL_DATA = ROOT / "captures" / "full_32_train_v2"
SIZES = ("S", "M", "L", "XL")
SEEDS = (0, 1, 2, 3, 4)


def eval_one(size: str, seed: int) -> dict:
    run_dir = SWEEP_ROOT / f"size_{size}_seed_{seed}"
    out_dir = run_dir / "eval_xrun_train_v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "--extra", "training", "python", "-m", "training.infer_14cls",
        "--captures", str(EVAL_DATA),
        "--ckpt", str(run_dir / "best.pt"),
        "--out", str(out_dir),
        "--feature-mode", "noise_diff",
        "--split", "all",
    ]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-400:])
        print(r.stderr[-400:])
        raise RuntimeError(f"eval failed: size={size} seed={seed}")
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    print(f"  size={size} seed={seed}: acc14={report['acc_14cls']:.4f}, "
          f"macro_f1={report['macro_f1_14cls']:.4f}")
    return {
        "size": size,
        "seed": seed,
        "acc_14cls": report["acc_14cls"],
        "macro_f1_14cls": report["macro_f1_14cls"],
    }


def main() -> int:
    print(f"sweep eval on {EVAL_DATA}")
    print(f"sizes={SIZES}, seeds={SEEDS}, total {len(SIZES) * len(SEEDS)} runs")
    results = []
    for size in SIZES:
        for seed in SEEDS:
            try:
                r = eval_one(size, seed)
                results.append(r)
            except Exception as exc:
                print(f"  ! failed: {exc}")

    out_dir = SWEEP_ROOT / "eval_v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["size", "seed", "acc_14cls", "macro_f1_14cls"])
        for r in results:
            w.writerow([r["size"], r["seed"],
                         f"{r['acc_14cls']:.4f}", f"{r['macro_f1_14cls']:.4f}"])
    print(f"\nresults: {csv_path}")

    print(f"\n=== full_32_train_v2 (n=1600) 集計 ===")
    print(f"  {'size':<4} {'n_seed':>6} {'mean':>8} {'std':>8} {'min':>8} {'max':>8}  per-seed")
    by_size = {}
    for r in results:
        by_size.setdefault(r["size"], []).append(r["acc_14cls"])
    summary = {}
    for size in SIZES:
        if size not in by_size:
            continue
        accs = by_size[size]
        mean = statistics.mean(accs)
        std = statistics.stdev(accs) if len(accs) > 1 else 0.0
        print(f"  {size:<4} {len(accs):>6} {mean:>8.4f} {std:>8.4f} "
              f"{min(accs):>8.4f} {max(accs):>8.4f}  "
              f"{[f'{a:.3f}' for a in accs]}")
        summary[size] = {
            "n_seeds": len(accs), "mean": mean, "std": std,
            "min": min(accs), "max": max(accs), "per_seed": accs,
        }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
