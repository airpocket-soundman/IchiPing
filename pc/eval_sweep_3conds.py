"""sweep モデル群を 3 つの eval セット (quiet / noise_low / noise_high) で一括評価。

使い方:
    cd pc
    uv run --extra training python eval_sweep_3conds.py \\
        --sweep-dir runs/sweep_v1v2v3_14cls --head-type 14cls
    uv run --extra training python eval_sweep_3conds.py \\
        --sweep-dir runs/sweep_v1v2v3_32cls --head-type 32cls

出力:
    <sweep-dir>/eval_3conds/results.csv         全 60 評価の生データ
    <sweep-dir>/eval_3conds/summary.json        size × eval-set 集計
    <sweep-dir>/size_X_seed_Y/eval_<cond>/      個別評価 (既存形式)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EVAL_SETS = (
    ("quiet",       ROOT / "captures" / "eval_quiet"),
    ("noise_low",   ROOT / "captures" / "eval_noise_low"),
    ("noise_high",  ROOT / "captures" / "eval_noise_high"),
)
SIZES = ("S", "M", "L", "XL")
SEEDS = (0, 1, 2, 3, 4)


def eval_one(sweep_dir: Path, size: str, seed: int, cond_name: str, cond_path: Path,
             head_type: str) -> dict:
    infer_mod = f"training.infer_{head_type}"
    run_dir = sweep_dir / f"size_{size}_seed_{seed}"
    out_dir = run_dir / f"eval_{cond_name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "--extra", "training", "python", "-m", infer_mod,
        "--captures", str(cond_path),
        "--ckpt", str(run_dir / "best.pt"),
        "--out", str(out_dir),
        "--feature-mode", "noise_diff",
        "--split", "all",
    ]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-300:]); print(r.stderr[-300:])
        raise RuntimeError(f"eval failed: {size}/{seed}/{cond_name}")
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return report


def main():
    ap = argparse.ArgumentParser(description="Evaluate sweep models on 3 eval conditions.")
    ap.add_argument("--sweep-dir", type=Path, required=True,
                    help="sweep root (例: runs/sweep_v1v2v3_14cls)")
    ap.add_argument("--head-type", choices=("14cls", "32cls"), required=True)
    args = ap.parse_args()

    print(f"sweep: {args.sweep_dir}, head: {args.head_type}")
    print(f"eval sets: {[c[0] for c in EVAL_SETS]}")
    print(f"total evals: {len(SIZES) * len(SEEDS) * len(EVAL_SETS)}")

    results = []
    for size in SIZES:
        for seed in SEEDS:
            for cond_name, cond_path in EVAL_SETS:
                if not cond_path.exists():
                    print(f"  ! missing: {cond_path}")
                    continue
                rep = eval_one(args.sweep_dir, size, seed, cond_name, cond_path, args.head_type)
                row = {
                    "size": size, "seed": seed, "cond": cond_name,
                    "acc_14cls": rep["acc_14cls"],
                    "macro_f1_14cls": rep["macro_f1_14cls"],
                }
                if "acc_32cls" in rep:
                    row["acc_32cls"] = rep["acc_32cls"]
                results.append(row)
                print(f"  {size}/{seed}/{cond_name}: 14cls acc={rep['acc_14cls']:.3f}")

    out_dir = args.sweep_dir / "eval_3conds"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    keys = ["size", "seed", "cond", "acc_14cls", "macro_f1_14cls"]
    if any("acc_32cls" in r for r in results):
        keys.append("acc_32cls")
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(keys)
        for r in results:
            w.writerow([r.get(k, "") for k in keys])

    # size × cond 集計
    by = {}
    for r in results:
        by.setdefault((r["size"], r["cond"]), []).append(r["acc_14cls"])
    summary = {}
    for (size, cond), accs in by.items():
        summary.setdefault(size, {})[cond] = {
            "n_seeds": len(accs),
            "mean": statistics.mean(accs),
            "std":  statistics.stdev(accs) if len(accs) > 1 else 0.0,
            "min":  min(accs), "max": max(accs),
            "per_seed": accs,
        }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(f"\n=== size × eval condition: mean (std) ===")
    print(f"  {'size':<4}  {'quiet':<14}  {'noise_low':<14}  {'noise_high':<14}")
    for size in SIZES:
        if size not in summary:
            continue
        row = [size]
        for cond in ("quiet", "noise_low", "noise_high"):
            if cond in summary[size]:
                m = summary[size][cond]
                row.append(f"{m['mean']:.3f} (±{m['std']:.3f})")
            else:
                row.append("—")
        print(f"  {row[0]:<4}  {row[1]:<14}  {row[2]:<14}  {row[3]:<14}")
    print(f"\n出力: {csv_path}, {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
