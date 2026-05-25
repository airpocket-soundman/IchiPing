"""IchiPingV1_embed 評価スクリプト (プロトタイプベース推論)。

best.pt にプロトタイプ (state_idx 0..31) が同梱されている前提。
推論は新サンプル → embedding → 最近傍プロトタイプ → 14cls collapse。

cross-run 推論時もこのプロトタイプを使う (deploy 想定)。
別 run でプロトタイプを取り直すなら --prototypes-from で別の captures を指定可能。
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
    from model_embed import (
        IchiPingV1_embed,
        compute_prototypes,
        predict_with_prototypes,
    )
    from infer_32cls import (
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of  # type: ignore
    from .model_embed import (                                                      # type: ignore
        IchiPingV1_embed,
        compute_prototypes,
        predict_with_prototypes,
    )
    from .infer_32cls import (                                                      # type: ignore
        save_confusion_csv,
        save_confusion_png,
        save_training_curves,
        per_class_f1,
        confusion,
    )


def _idx_to_14cls(idx32: int) -> int:
    bits = np.array([(idx32 >> k) & 1 for k in range(5)], dtype=np.int64)
    return CLASS_ORDER_14.index(class_of(bits))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluate IchiPingV1_embed.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--ckpt",     type=Path, required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--batch",    type=int, default=32)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise_diff")
    ap.add_argument("--split", choices=("all", "test"), default="all")
    ap.add_argument("--prototypes-from", type=Path, default=None,
                    help="このフォルダの s00000..s11111 でプロトタイプを再計算 "
                         "(deploy 時の per-house キャリブレーション相当)")
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
    model = IchiPingV1_embed().to(args.device)
    state = torch.load(args.ckpt, map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])

    # プロトタイプ: 既定では best.pt 同梱 (train セット由来)
    # --prototypes-from が指定されたら、その captures から再計算
    if args.prototypes_from is not None:
        proto_ds = IchiPingDataset(captures_dirs=[args.prototypes_from], transform=None,
                                    feature_mode=args.feature_mode)
        proto_loader = DataLoader(proto_ds, batch_size=args.batch, shuffle=False)
        prototypes = compute_prototypes(model, proto_loader, args.device, n_classes=32)
        print(f"prototypes computed from {args.prototypes_from}")
    else:
        prototypes = state["prototypes"].to(args.device)
        print("prototypes loaded from ckpt (train set)")

    # 推論
    model.eval()
    true14_all, pred14_all = [], []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(args.device)
            y32 = batch["state_idx"].numpy()
            pred32 = predict_with_prototypes(model, x, prototypes).cpu().numpy()
            for t, p in zip(y32, pred32):
                true14_all.append(_idx_to_14cls(int(t)))
                pred14_all.append(_idx_to_14cls(int(p)))
    true14 = np.array(true14_all, dtype=np.int64)
    pred14 = np.array(pred14_all, dtype=np.int64)
    conf14 = confusion(14, true14, pred14)
    acc14 = float((true14 == pred14).mean())
    per_class = per_class_f1(conf14, list(CLASS_ORDER_14))
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))

    save_confusion_csv(conf14, list(CLASS_ORDER_14), args.out / "confusion_14cls.csv")
    save_confusion_png(conf14, list(CLASS_ORDER_14),
                       f"Confusion 14-class (embed+proto) — acc {acc14:.3f}, "
                       f"macro-F1 {macro_f1:.3f}",
                       args.out / "confusion_14cls.png", annotate=True)

    log_csv = args.ckpt.parent / "train_log.csv"
    if save_training_curves(log_csv, args.out / "training_curves.png"):
        print(f"  + training curves -> {args.out / 'training_curves.png'}")

    report = {
        "n_samples": int(len(ds_use)),
        "split": args.split,
        "feature_mode": args.feature_mode,
        "prototypes_from": str(args.prototypes_from) if args.prototypes_from else "ckpt",
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
