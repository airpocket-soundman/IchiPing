"""IchiPingV1_14cls 用の評価 + プロット生成スクリプト。

infer_32cls.py の 14-class 版。同じ artifacts を吐く:

  out/
    report.json
    confusion_14cls.csv / .png
    training_curves.png  (train_log.csv が ckpt の隣にあれば)

Usage:
    cd pc
    uv run --extra training python -m training.infer_14cls \\
        --captures captures/full_32_train_v1 \\
        --ckpt runs/v1_train_14cls/best.pt \\
        --out runs/v1_train_14cls/eval \\
        --feature-mode noise --split all
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14
    from model_14cls import IchiPingV1_14cls, IchiPingV1_14clsConfig, N_CLASSES
    from infer_32cls import (
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14  # type: ignore
    from .model_14cls import IchiPingV1_14cls, IchiPingV1_14clsConfig, N_CLASSES  # type: ignore
    from .infer_32cls import (                                            # type: ignore
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )


def predict_all(model, loader, device):
    """全バッチで (true_idx14, pred_idx14) を返す。"""
    model.eval()
    trues, preds = [], []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            y = batch["cls_idx_14"].numpy()
            p = model(x).argmax(dim=-1).cpu().numpy()
            trues.append(y)
            preds.append(p)
    return np.concatenate(trues), np.concatenate(preds)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate IchiPingV1_14cls.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--ckpt",     type=Path, required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--batch",    type=int, default=32)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise",
                    help="特徴量モード (学習時と一致させること)")
    ap.add_argument("--baseline-from", type=Path, default=None,
                    help="noise_diff baseline を別の captures dir から取得 "
                         "(例: --baseline-from captures/eval_quiet で 1 回校正運用を模擬)")
    ap.add_argument("--split", choices=("all", "test"), default="all",
                    help="all: 全フレーム / test: train_14cls.py と同じ seed=0 で 15% 切出し")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}, split: {args.split}")

    ds = IchiPingDataset(captures_dirs=args.captures, transform=None,
                          feature_mode=args.feature_mode,
                          baseline_override_dir=args.baseline_from)
    if args.split == "test":
        _, _, te = split_indices(len(ds), seed=0)
        ds_use = Subset(ds, te)
    else:
        ds_use = ds
    print(f"loaded {len(ds)} examples ({args.split}={len(ds_use)})")

    loader = DataLoader(ds_use, batch_size=args.batch, shuffle=False)

    ckpt = torch.load(args.ckpt, map_location=args.device, weights_only=False)
    # ckpt 内に config["size"] があれば尊重、無ければ S
    size = "S"
    if isinstance(ckpt, dict) and "config" in ckpt and isinstance(ckpt["config"], dict):
        size = ckpt["config"].get("size", "S")
    print(f"model size: {size}")
    model = IchiPingV1_14cls(IchiPingV1_14clsConfig(size=size)).to(args.device)
    state = ckpt["state_dict"] if isinstance(ckpt, dict) and "state_dict" in ckpt else ckpt
    model.load_state_dict(state)

    true14, pred14 = predict_all(model, loader, args.device)
    conf14 = confusion(N_CLASSES, true14, pred14)
    acc14 = float((true14 == pred14).mean())
    per_class = per_class_f1(conf14, list(CLASS_ORDER_14))
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))

    save_confusion_csv(conf14, list(CLASS_ORDER_14), args.out / "confusion_14cls.csv")
    save_confusion_png(conf14, list(CLASS_ORDER_14),
                       f"Confusion 14-class direct — acc {acc14:.3f}, "
                       f"macro-F1 {macro_f1:.3f}",
                       args.out / "confusion_14cls.png", annotate=True)

    log_csv = args.ckpt.parent / "train_log.csv"
    curves_path = args.out / "training_curves.png"
    have_curves = save_training_curves(log_csv, curves_path)
    if have_curves:
        print(f"  + training curves -> {curves_path}")
    else:
        print("  ! train_log.csv not found next to ckpt; skipped training curves")

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
            print(f"    {c:<3} F1={m['f1']:.3f}  P={m['precision']:.3f}  R={m['recall']:.3f}  n={m['support']}")
    print(f"\nartifacts under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
