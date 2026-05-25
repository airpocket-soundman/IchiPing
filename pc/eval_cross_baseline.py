"""cross-baseline 評価: 指定 captures の s00000 を baseline として全 sweep モデルを評価。

実機キャリブレーション運用 (静寂で 1 回校正 → 任意環境で運用) を模擬。

Usage:
    cd pc
    # quiet baseline で 3 環境を評価 (14cls sweep)
    uv run --extra training python eval_cross_baseline.py \\
        --sweep-dir runs/sweep_v1v2v3_14cls --head-type 14cls \\
        --baseline-from captures/eval_quiet \\
        --out-tag baseline_quiet
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
             head_type: str, baseline_from: Path, out_tag: str) -> dict:
    infer_mod = f"training.infer_{head_type}"
    run_dir = sweep_dir / f"size_{size}_seed_{seed}"
    out_dir = run_dir / f"eval_{cond_name}_{out_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "--extra", "training", "python", "-m", infer_mod,
        "--captures", str(cond_path),
        "--ckpt", str(run_dir / "best.pt"),
        "--out", str(out_dir),
        "--feature-mode", "noise_diff",
        "--baseline-from", str(baseline_from),
        "--split", "all",
    ]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-300:]); print(r.stderr[-300:])
        raise RuntimeError(f"eval failed: {size}/{seed}/{cond_name}")
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir", type=Path, required=True)
    ap.add_argument("--head-type", choices=("14cls", "32cls"), required=True)
    ap.add_argument("--baseline-from", type=Path, required=True,
                    help="この dir の s00000 を baseline として使用")
    ap.add_argument("--out-tag", required=True,
                    help="出力サブディレクトリ識別子 (例: baseline_quiet)")
    args = ap.parse_args()

    print(f"sweep: {args.sweep_dir}, head: {args.head_type}")
    print(f"baseline from: {args.baseline_from}")
    print(f"eval sets: {[c[0] for c in EVAL_SETS]}")

    results = []
    for size in SIZES:
        for seed in SEEDS:
            # best.pt が無ければスキップ (1-seed sweep を 5-seed script で処理する場合)
            ckpt = args.sweep_dir / f"size_{size}_seed_{seed}" / "best.pt"
            if not ckpt.exists():
                continue
            for cond_name, cond_path in EVAL_SETS:
                rep = eval_one(args.sweep_dir, size, seed, cond_name, cond_path,
                               args.head_type, args.baseline_from, args.out_tag)
                row = {
                    "size": size, "seed": seed, "cond": cond_name,
                    "baseline_from": args.baseline_from.name,
                    "acc_14cls": rep["acc_14cls"],
                    "macro_f1_14cls": rep["macro_f1_14cls"],
                }
                if "acc_32cls" in rep:
                    row["acc_32cls"] = rep["acc_32cls"]
                results.append(row)
                print(f"  {size}/{seed}/{cond_name}: acc14={rep['acc_14cls']:.3f}"
                      + (f" acc32={rep['acc_32cls']:.3f}" if 'acc_32cls' in rep else ""))

    out_dir = args.sweep_dir / f"eval_{args.out_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    keys = ["size", "seed", "cond", "baseline_from", "acc_14cls", "macro_f1_14cls"]
    if any("acc_32cls" in r for r in results):
        keys.append("acc_32cls")
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(keys)
        for r in results:
            w.writerow([r.get(k, "") for k in keys])

    print(f"\n=== {args.out_tag}: size × cond mean / std (14cls acc) ===")
    by = {}
    for r in results:
        by.setdefault((r["size"], r["cond"]), []).append(r["acc_14cls"])
    print(f"  {'size':<4}  {'quiet':<16}  {'noise_low':<16}  {'noise_high':<16}")
    for size in SIZES:
        row = [size]
        for cond in ("quiet", "noise_low", "noise_high"):
            accs = by.get((size, cond), [])
            if accs:
                row.append(f"{statistics.mean(accs):.3f} (±{statistics.stdev(accs):.3f})")
            else:
                row.append("—")
        print(f"  {row[0]:<4}  {row[1]:<16}  {row[2]:<16}  {row[3]:<16}")
    print(f"\nresults: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
