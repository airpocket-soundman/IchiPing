"""PC-side inference + evaluation.

Loads a trained checkpoint and evaluates it against a captures directory.
Useful for the iterative loop:

    device collects new WAVs
       ↓
    python -m training.infer --captures captures/full_32_v2 \
        --ckpt runs/v1/best.pt --out reports/v1_v2_eval/
       ↓
    inspect reports/v1_v2_eval/confusion_matrix.png + per-class F1

The script reports:
  - per-head accuracy / F1
  - 14-class effective accuracy (using the equivalence class derived from
    the model's 5-bit prediction)
  - confusion matrix (true 14 class × predicted 14 class)
  - mean / worst predicted-state per true-state
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

import numpy as np
import torch
from torch.utils.data import DataLoader

try:
    from dataset import IchiPingDataset, class_of, parse_state_label
    from model import IchiPingV1
except ImportError:
    from .dataset import IchiPingDataset, class_of, parse_state_label  # type: ignore
    from .model import IchiPingV1  # type: ignore


def predict_5bit(model: IchiPingV1, batch: Dict[str, torch.Tensor],
                 device: str) -> np.ndarray:
    """Return one 5-bit prediction array per item in the batch."""
    x = batch["x"].to(device)
    out = model(x)
    # Heads convention (model.py):
    #   window_a / door_AB: float in [0, 1]  → threshold at 0.5
    #   window_b/c, door_BC: class index (0..K-1) → take argmax
    a  = (out["window_a"].squeeze(-1).detach().cpu().numpy() > 0.5).astype(np.int64)
    b  = out["window_b"].detach().cpu().numpy().argmax(axis=-1).astype(np.int64)
    c  = out["window_c"].detach().cpu().numpy().argmax(axis=-1).astype(np.int64)
    AB = (out["door_AB"].squeeze(-1).detach().cpu().numpy() > 0.5).astype(np.int64)
    BC = out["door_BC"].detach().cpu().numpy().argmax(axis=-1).astype(np.int64)
    # Some heads use 3-class buckets — squash to binary for the 14-class eval.
    if b.max() > 1: b = (b > 0).astype(np.int64)
    if c.max() > 1: c = (c > 0).astype(np.int64)
    if BC.max() > 1: BC = (BC > 0).astype(np.int64)
    return np.stack([a, b, c, AB, BC], axis=-1)


def state_to_class(bits: np.ndarray) -> str:
    return class_of(bits)


def evaluate(model: IchiPingV1, loader: DataLoader, device: str):
    """Returns dict with: per-head accuracies, confusion matrix, per-class F1."""
    model.eval()
    true_states: List[np.ndarray] = []
    pred_states: List[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            true_states.append(batch["state5"].numpy())
            pred_states.append(predict_5bit(model, batch, device))
    true_arr = np.concatenate(true_states, axis=0)
    pred_arr = np.concatenate(pred_states, axis=0)

    n = true_arr.shape[0]

    # Per-bit accuracy
    bit_acc: Dict[str, float] = {}
    for i, name in enumerate(("window_a", "window_b", "window_c",
                              "door_AB", "door_BC")):
        bit_acc[name] = float((true_arr[:, i] == pred_arr[:, i]).mean())

    # 14-class confusion: convert each state to class tag
    true_cls = np.array([state_to_class(s) for s in true_arr])
    pred_cls = np.array([state_to_class(s) for s in pred_arr])
    classes = ["A1", "A2", "B1", "B2", "B3", "B4",
               "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    conf = np.zeros((len(classes), len(classes)), dtype=np.int64)
    cls_idx = {c: i for i, c in enumerate(classes)}
    for t, p in zip(true_cls, pred_cls):
        if t in cls_idx and p in cls_idx:
            conf[cls_idx[t], cls_idx[p]] += 1

    # Per-class precision / recall / F1
    per_class = {}
    for i, c in enumerate(classes):
        tp = conf[i, i]
        fp = conf[:, i].sum() - tp
        fn = conf[i, :].sum() - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        per_class[c] = {"precision": float(prec), "recall": float(rec),
                         "f1": float(f1), "support": int(conf[i, :].sum())}

    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))
    overall_acc = float((true_cls == pred_cls).mean())

    return {
        "n_samples":   n,
        "overall_acc": overall_acc,
        "macro_f1":    macro_f1,
        "bit_acc":     bit_acc,
        "per_class":   per_class,
        "confusion":   conf.tolist(),
        "classes":     classes,
    }


def write_reports(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # Confusion matrix CSV
    classes = report["classes"]
    conf = np.array(report["confusion"], dtype=np.int64)
    with (out_dir / "confusion.csv").open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["true\\pred"] + classes)
        for i, c in enumerate(classes):
            w.writerow([c] + conf[i, :].tolist())

    # Plot if matplotlib is available
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 7))
        im = ax.imshow(conf, cmap="Blues")
        ax.set_xticks(range(len(classes))); ax.set_xticklabels(classes, rotation=45)
        ax.set_yticks(range(len(classes))); ax.set_yticklabels(classes)
        for i in range(len(classes)):
            for j in range(len(classes)):
                if conf[i, j] > 0:
                    ax.text(j, i, str(conf[i, j]), ha="center", va="center",
                            color="white" if conf[i, j] > conf.max() / 2 else "black",
                            fontsize=8)
        ax.set_xlabel("Predicted"); ax.set_ylabel("True")
        ax.set_title(f"Confusion matrix — acc {report['overall_acc']:.3f} / "
                     f"macro-F1 {report['macro_f1']:.3f}")
        fig.colorbar(im, ax=ax, fraction=0.04)
        plt.tight_layout()
        fig.savefig(out_dir / "confusion_matrix.png", dpi=130)
        plt.close(fig)
    except ImportError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PC-side inference + eval.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True,
                    help="one or more captures/<run_id> root dirs")
    ap.add_argument("--ckpt", type=Path, required=True,
                    help="trained model checkpoint (.pt)")
    ap.add_argument("--out", type=Path, required=True,
                    help="output directory for report + confusion plot")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args(argv)

    print(f"using device: {args.device}")
    ds = IchiPingDataset(captures_dirs=args.captures)
    print(f"loaded {len(ds)} examples from {len(args.captures)} captures dirs")
    if len(ds) == 0:
        print("FAIL: no examples found")
        return 2

    loader = DataLoader(ds, batch_size=args.batch, shuffle=False, num_workers=0)

    model = IchiPingV1()
    state = torch.load(args.ckpt, map_location=args.device)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model = model.to(args.device)

    report = evaluate(model, loader, args.device)
    write_reports(report, args.out)

    print(f"\nresults:")
    print(f"  N samples       : {report['n_samples']}")
    print(f"  overall acc     : {report['overall_acc']:.3f}")
    print(f"  macro-F1 (14cls): {report['macro_f1']:.3f}")
    print(f"  per-bit accuracy:")
    for k, v in report["bit_acc"].items():
        print(f"    {k:<10} {v:.3f}")

    print(f"  per-class F1:")
    for c, m in report["per_class"].items():
        if m["support"] > 0:
            print(f"    {c:<3} F1={m['f1']:.3f}  support={m['support']}")

    print(f"\nwrote {args.out}/report.json + confusion_matrix.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
