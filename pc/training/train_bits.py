"""5-bit binary head 構成 (model_bits.IchiPingV1_bits) の学習スクリプト。

各 bit を独立な binary classifier として学習。loss は 5 つの BCE 合算。
target は dataset の state5 (a, b, c, AB, BC 順の 5-int tensor)。

Usage:
    cd pc
    uv run --extra training python -m training.train_bits \\
        --captures captures/full_32_train_v1 \\
        --out runs/v1_train_bits --epochs 60 \\
        --feature-mode noise_diff --feature-aug
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of
    from model_bits import IchiPingV1_bits, BIT_ORDER, N_BITS
    from augment import default_train_transform, default_feature_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of  # type: ignore
    from .model_bits import IchiPingV1_bits, BIT_ORDER, N_BITS                      # type: ignore
    from .augment import default_train_transform, default_feature_transform        # type: ignore


def _compute_loss(out, y_bits):
    """5 BCE の単純合算。bit ごとの重みは均等 (将来必要なら配列化)。"""
    loss = 0.0
    for i, name in enumerate(BIT_ORDER):
        logit = out[name].squeeze(-1)
        target = y_bits[:, i].float()
        loss = loss + F.binary_cross_entropy_with_logits(logit, target)
    return loss


def _bit_predictions(out):
    """(B, 5) long, threshold 0 (logit) = 0.5 (sigmoid 後)。"""
    cols = [(out[name].squeeze(-1) > 0).long() for name in BIT_ORDER]
    return torch.stack(cols, dim=-1)


def _bits_to_14cls(bits_np: np.ndarray) -> int:
    return CLASS_ORDER_14.index(class_of(bits_np))


def _one_epoch(model, loader, device, optimiser=None):
    is_train = optimiser is not None
    model.train(is_train)
    total = 0; correct_14 = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        y_bits = batch["state5"].to(device)
        out = model(x)
        loss = _compute_loss(out, y_bits)
        if is_train:
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        loss_sum += float(loss.item()) * y_bits.size(0)

        pred_bits = _bit_predictions(out).cpu().numpy()
        true_bits = batch["state5"].cpu().numpy()
        for tb, pb in zip(true_bits, pred_bits):
            if _bits_to_14cls(pb) == _bits_to_14cls(tb):
                correct_14 += 1
        total += y_bits.size(0)
    return loss_sum / max(total, 1), correct_14 / max(total, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train IchiPingV1_bits (5-bit binary heads).")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--epochs",   type=int,  default=60)
    ap.add_argument("--batch",    type=int,  default=32)
    ap.add_argument("--lr",       type=float, default=1e-3)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise_diff")
    ap.add_argument("--feature-aug", action="store_true")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}, "
          f"feature_aug: {args.feature_aug}")

    train_tf = default_train_transform(args.ambient_dirs)
    feat_tf = default_feature_transform(args.feature_mode) if args.feature_aug else None
    ds_all = IchiPingDataset(captures_dirs=args.captures, transform=train_tf,
                              feature_mode=args.feature_mode, feature_transform=feat_tf)
    ds_eval = IchiPingDataset(captures_dirs=args.captures, transform=None,
                              feature_mode=args.feature_mode, feature_transform=None)
    print(f"loaded {len(ds_all)} examples")

    n = len(ds_all)
    tr, va, te = split_indices(n, seed=0)
    train_loader = DataLoader(Subset(ds_all,  tr), batch_size=args.batch, shuffle=True)
    val_loader   = DataLoader(Subset(ds_eval, va), batch_size=args.batch)
    test_loader  = DataLoader(Subset(ds_eval, te), batch_size=args.batch)

    model = IchiPingV1_bits().to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"IchiPingV1_bits: {n_params} params, 5 binary heads")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val_acc = -1.0
    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "wall_s"])
        t0 = time.time()
        for ep in range(1, args.epochs + 1):
            tr_loss, tr_acc = _one_epoch(model, train_loader, args.device, optimiser)
            va_loss, va_acc = _one_epoch(model, val_loader,   args.device, optimiser=None)
            w.writerow([ep, f"{tr_loss:.4f}", f"{tr_acc:.4f}",
                         f"{va_loss:.4f}", f"{va_acc:.4f}", f"{time.time()-t0:.1f}"])
            fp.flush()
            print(f"  epoch {ep:3d}  tr_loss={tr_loss:.3f}  tr_acc(14)={tr_acc:.3f}  "
                  f"va_loss={va_loss:.3f}  va_acc(14)={va_acc:.3f}")
            if va_acc > best_val_acc:
                best_val_acc = va_acc
                torch.save({"state_dict": model.state_dict(),
                             "config":     {"bit_order": list(BIT_ORDER)}},
                            args.out / "best.pt")

    # 最終 test 評価
    state = torch.load(args.out / "best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])
    te_loss, te_acc = _one_epoch(model, test_loader, args.device, optimiser=None)

    print(f"\nbest val acc(14): {best_val_acc:.3f}")
    print(f"test acc(14)    : {te_acc:.3f}")

    (args.out / "config.json").write_text(json.dumps({
        "model": "IchiPingV1_bits",
        "n_bits": N_BITS,
        "bit_order": list(BIT_ORDER),
        "n_params": n_params,
        "epochs": args.epochs,
        "batch":  args.batch,
        "lr":     args.lr,
        "feature_mode": args.feature_mode,
        "feature_aug":  args.feature_aug,
        "best_val_acc_14cls": best_val_acc,
        "test_acc_14cls":     te_acc,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
