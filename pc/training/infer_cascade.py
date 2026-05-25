"""IchiPingV1_cascade 用の評価スクリプト。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of
    from model_cascade import IchiPingV1_cascade
    from infer_32cls import (
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of  # type: ignore
    from .model_cascade import IchiPingV1_cascade                                  # type: ignore
    from .infer_32cls import (                                                      # type: ignore
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate IchiPingV1_cascade.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--ckpt",     type=Path, required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--batch",    type=int, default=32)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise_diff")
    ap.add_argument("--split", choices=("all", "test"), default="all")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}, split: {args.split}")

    ds = IchiPingDataset(captures_dirs=args.captures, transform=None,
                          feature_mode=args.feature_mode)
    if args.split == "test":
        _, _, te = split_indices(len(ds), seed=0)
        ds_use = Subset(ds, te)
    else:
        ds_use = ds
    print(f"loaded {len(ds)} examples ({args.split}={len(ds_use)})")

    loader = DataLoader(ds_use, batch_size=args.batch, shuffle=False)
    model = IchiPingV1_cascade().to(args.device)
    state = torch.load(args.ckpt, map_location=args.device, weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    true14, pred14 = [], []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(args.device)
            bits = batch["state5"].numpy()
            t14 = np.array([CLASS_ORDER_14.index(class_of(b)) for b in bits], dtype=np.int64)
            p14 = model.predict_14cls(x).cpu().numpy()
            true14.append(t14)
            pred14.append(p14)
    true14 = np.concatenate(true14)
    pred14 = np.concatenate(pred14)
    conf14 = confusion(14, true14, pred14)
    acc14 = float((true14 == pred14).mean())
    per_class = per_class_f1(conf14, list(CLASS_ORDER_14))
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))

    save_confusion_csv(conf14, list(CLASS_ORDER_14), args.out / "confusion_14cls.csv")
    save_confusion_png(conf14, list(CLASS_ORDER_14),
                       f"Confusion 14-class (cascade) — acc {acc14:.3f}, macro-F1 {macro_f1:.3f}",
                       args.out / "confusion_14cls.png", annotate=True)

    log_csv = args.ckpt.parent / "train_log.csv"
    if save_training_curves(log_csv, args.out / "training_curves.png"):
        print(f"  + training curves -> {args.out / 'training_curves.png'}")

    report = {
        "n_samples": int(len(ds_use)),
        "split": args.split,
        "feature_mode": args.feature_mode,
        "acc_14cls": acc14,
        "macro_f1_14cls": macro_f1,
        "per_class_14cls": per_class,
    }
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n=== {args.split} ({len(ds_use)} samples) ===")
    print(f"  acc 14-class: {acc14:.3f}  macro-F1 {macro_f1:.3f}")
    for c, m in per_class.items():
        if m["support"] > 0:
            print(f"    {c:<3} F1={m['f1']:.3f}  n={m['support']}")
    print(f"\nartifacts under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
