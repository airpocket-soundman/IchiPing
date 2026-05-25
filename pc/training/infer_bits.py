"""IchiPingV1_bits 用の評価スクリプト (5-bit binary heads)。

5 bit を独立予測 → 5-bit state を合成 → 14cls + 32cls の両方で評価。
report.json / confusion_14cls.{csv,png} / training_curves.png を吐く。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of
    from model_bits import IchiPingV1_bits, BIT_ORDER, bits_to_idx32
    from infer_32cls import (
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of  # type: ignore
    from .model_bits import IchiPingV1_bits, BIT_ORDER, bits_to_idx32              # type: ignore
    from .infer_32cls import (                                                      # type: ignore
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )


def predict_all(model, loader, device):
    model.eval()
    true_bits_list, pred_bits_list = [], []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            out = model(x)
            cols = [(out[name].squeeze(-1) > 0).long() for name in BIT_ORDER]
            pred = torch.stack(cols, dim=-1).cpu().numpy()
            true = batch["state5"].numpy()
            true_bits_list.append(true)
            pred_bits_list.append(pred)
    return np.concatenate(true_bits_list), np.concatenate(pred_bits_list)


def bits_to_14_idx(bits_arr: np.ndarray) -> np.ndarray:
    return np.array([CLASS_ORDER_14.index(class_of(b)) for b in bits_arr], dtype=np.int64)


def bits_to_32_idx(bits_arr: np.ndarray) -> np.ndarray:
    return np.array([bits_to_idx32(b) for b in bits_arr], dtype=np.int64)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate IchiPingV1_bits.")
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
    model = IchiPingV1_bits().to(args.device)
    state = torch.load(args.ckpt, map_location=args.device, weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)

    true_bits, pred_bits = predict_all(model, loader, args.device)
    # bit ごとの正解率 (interpretability 用)
    bit_acc = {}
    for i, name in enumerate(BIT_ORDER):
        bit_acc[name] = float((true_bits[:, i] == pred_bits[:, i]).mean())

    # 14cls / 32cls 両方で評価
    true14 = bits_to_14_idx(true_bits)
    pred14 = bits_to_14_idx(pred_bits)
    conf14 = confusion(14, true14, pred14)
    acc14 = float((true14 == pred14).mean())
    per_class = per_class_f1(conf14, list(CLASS_ORDER_14))
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))

    true32 = bits_to_32_idx(true_bits)
    pred32 = bits_to_32_idx(pred_bits)
    acc32 = float((true32 == pred32).mean())

    save_confusion_csv(conf14, list(CLASS_ORDER_14), args.out / "confusion_14cls.csv")
    save_confusion_png(conf14, list(CLASS_ORDER_14),
                       f"Confusion 14-class — acc {acc14:.3f}, macro-F1 {macro_f1:.3f}",
                       args.out / "confusion_14cls.png", annotate=True)

    log_csv = args.ckpt.parent / "train_log.csv"
    if save_training_curves(log_csv, args.out / "training_curves.png"):
        print(f"  + training curves -> {args.out / 'training_curves.png'}")

    report = {
        "n_samples": int(len(ds_use)),
        "split": args.split,
        "feature_mode": args.feature_mode,
        "acc_14cls": acc14,
        "acc_32cls": acc32,
        "macro_f1_14cls": macro_f1,
        "bit_acc": bit_acc,
        "per_class_14cls": per_class,
    }
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n=== {args.split} ({len(ds_use)} samples) ===")
    print(f"  acc 14-class: {acc14:.3f}  macro-F1 {macro_f1:.3f}")
    print(f"  acc 32-class: {acc32:.3f}")
    print("  per-bit acc:")
    for name in BIT_ORDER:
        print(f"    {name:<3} {bit_acc[name]:.3f}")
    print("  per-class F1 (14cls):")
    for c, m in per_class.items():
        if m["support"] > 0:
            print(f"    {c:<3} F1={m['f1']:.3f}  n={m['support']}")
    print(f"\nartifacts under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
