"""新 sweep の集約混同行列を matched baseline (cross-baseline 対角) から作る。"""
import csv
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "training"))
from training.dataset import CLASS_ORDER_14
from infer_32cls import save_confusion_png, save_confusion_csv, state_label_32

SWEEPS_14cls = [
    "sweep_v1v2v3_14cls_ambient",
    "sweep_v1v2v3_14cls_spikefix",
    "sweep_v1234_14cls",
]
SWEEPS_32cls = [
    "sweep_v1v2v3_32cls_ambient",
    "sweep_v1v2v3_32cls_spikefix",
    "sweep_v1234_32cls",
]
# (file_label, eval_subdir_pattern). cond は matched baseline = 同名で組まれる
EVAL_SETS = [
    ("noise_v1",   "eval_xrun_noise_v1"),
    # eval セットには cross-baseline matched (eval_<cond>_baseline_<cond>) を採用
    ("quiet",      "eval_quiet_baseline_quiet"),
    ("noise_low",  "eval_noise_low_baseline_noise_low"),
    ("noise_high", "eval_noise_high_baseline_noise_high"),
]


def aggregate_size(sweep_dir, size, eval_sub, n_classes):
    accum = np.zeros((n_classes, n_classes), dtype=np.int64)
    n_seeds = 0
    for seed in range(5):
        csv_path = sweep_dir / f"size_{size}_seed_{seed}" / eval_sub / f"confusion_{n_classes}cls.csv"
        if not csv_path.exists():
            continue
        with csv_path.open() as fp:
            rows = list(csv.reader(fp))
        for i in range(n_classes):
            for j in range(n_classes):
                accum[i, j] += int(rows[i + 1][j + 1])
        n_seeds += 1
    return accum, n_seeds


def process_sweep(sweep_name, has_32cls):
    sweep_dir = Path("runs") / sweep_name
    out_root_14 = sweep_dir / "aggregate_confusion"
    out_root_32 = sweep_dir / "aggregate_confusion_32cls"
    out_root_14.mkdir(parents=True, exist_ok=True)
    if has_32cls:
        out_root_32.mkdir(parents=True, exist_ok=True)

    labels32 = [state_label_32(i) for i in range(32)]

    for cond_name, eval_sub in EVAL_SETS:
        for size in ["S", "M", "L", "XL"]:
            accum14, n_seeds = aggregate_size(sweep_dir, size, eval_sub, 14)
            if n_seeds == 0:
                print(f"  miss 14: {sweep_name} / {cond_name} / {size} ({eval_sub})")
                continue
            total = int(accum14.sum())
            acc = int(np.trace(accum14)) / total if total else 0
            save_confusion_csv(accum14, list(CLASS_ORDER_14), out_root_14 / f"{cond_name}_{size}.csv")
            save_confusion_png(accum14, list(CLASS_ORDER_14),
                               f"{sweep_name} {cond_name} {size} 14cls acc {acc:.3f}",
                               out_root_14 / f"{cond_name}_{size}.png", annotate=True)
            if has_32cls:
                accum32, _ = aggregate_size(sweep_dir, size, eval_sub, 32)
                total = int(accum32.sum())
                acc32 = int(np.trace(accum32)) / total if total else 0
                save_confusion_csv(accum32, labels32, out_root_32 / f"{cond_name}_{size}.csv")
                save_confusion_png(accum32, labels32,
                                   f"{sweep_name} {cond_name} {size} 32cls acc {acc32:.3f}",
                                   out_root_32 / f"{cond_name}_{size}.png", annotate=False)


for sweep in SWEEPS_14cls:
    process_sweep(sweep, has_32cls=False)
    print(f"  done {sweep}")
for sweep in SWEEPS_32cls:
    process_sweep(sweep, has_32cls=True)
    print(f"  done {sweep}")
