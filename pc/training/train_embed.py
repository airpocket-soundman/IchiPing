"""Embedding + プロトタイプ構成 (model_embed.IchiPingV1_embed) の学習。

SupCon loss で 32 state 全部を表現空間で分離させ、学習終了後に
train セットからプロトタイプを計算して best.pt と一緒に保存する。

Usage:
    cd pc
    uv run --extra training python -m training.train_embed \\
        --captures captures/full_32_train_v1 \\
        --out runs/v1_train_embed --epochs 60 \\
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
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of
    from model_embed import (
        IchiPingV1_embed,
        supervised_contrastive_loss,
        compute_prototypes,
        predict_with_prototypes,
    )
    from augment import default_train_transform, default_feature_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14, class_of  # type: ignore
    from .model_embed import (                                                      # type: ignore
        IchiPingV1_embed,
        supervised_contrastive_loss,
        compute_prototypes,
        predict_with_prototypes,
    )
    from .augment import default_train_transform, default_feature_transform        # type: ignore


def _idx_to_14cls(idx32: int) -> int:
    """32-class index → 14cls index。bits を class_of に通す。"""
    bits = np.array([(idx32 >> k) & 1 for k in range(5)], dtype=np.int64)
    return CLASS_ORDER_14.index(class_of(bits))


def _eval_loop(model, loader, device, prototypes):
    """プロトタイプベース推論で 14cls accuracy を計算。"""
    model.eval()
    correct = 0; total = 0
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            y32 = batch["state_idx"].cpu().numpy()
            pred32 = predict_with_prototypes(model, x, prototypes).cpu().numpy()
            for t, p in zip(y32, pred32):
                if _idx_to_14cls(int(t)) == _idx_to_14cls(int(p)):
                    correct += 1
            total += y32.size
    return correct / max(total, 1)


def _train_epoch(model, loader, device, optimiser, temperature):
    model.train()
    total = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        y = batch["state_idx"].to(device)
        z = model(x)
        loss = supervised_contrastive_loss(z, y, temperature=temperature)
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        loss_sum += float(loss.item()) * y.size(0)
        total += y.size(0)
    return loss_sum / max(total, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train IchiPingV1_embed (contrastive + prototypes).")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--epochs",   type=int,  default=60)
    ap.add_argument("--batch",    type=int,  default=64)
    ap.add_argument("--lr",       type=float, default=1e-3)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise_diff")
    ap.add_argument("--feature-aug", action="store_true")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}, "
          f"feature_aug: {args.feature_aug}, temperature: {args.temperature}")

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
    # プロトタイプ計算用は train セットの augmentation OFF 版
    train_loader_eval = DataLoader(Subset(ds_eval, tr), batch_size=args.batch)

    model = IchiPingV1_embed().to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"IchiPingV1_embed: {n_params} params, embed_dim={model.cfg.embed_dim}")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val_acc = -1.0
    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "wall_s"])
        t0 = time.time()
        for ep in range(1, args.epochs + 1):
            tr_loss = _train_epoch(model, train_loader, args.device, optimiser, args.temperature)
            # プロトタイプは毎 epoch 再計算 (本来 epoch 末でよい)
            prototypes = compute_prototypes(model, train_loader_eval, args.device,
                                             n_classes=32)
            # train_acc は train セットの一部に対する手抜き計算
            tr_acc = _eval_loop(model, train_loader_eval, args.device, prototypes)
            va_acc = _eval_loop(model, val_loader, args.device, prototypes)
            # val loss は計算しない (contrastive は batch composition 依存なので)
            w.writerow([ep, f"{tr_loss:.4f}", f"{tr_acc:.4f}",
                         f"{0.0:.4f}", f"{va_acc:.4f}", f"{time.time()-t0:.1f}"])
            fp.flush()
            print(f"  epoch {ep:3d}  tr_loss={tr_loss:.3f}  tr_acc(14)={tr_acc:.3f}  "
                  f"va_acc(14)={va_acc:.3f}")
            if va_acc > best_val_acc:
                best_val_acc = va_acc
                torch.save({
                    "state_dict": model.state_dict(),
                    "prototypes": prototypes.cpu(),  # (32, D) tensor
                }, args.out / "best.pt")

    # 最終 test
    state = torch.load(args.out / "best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])
    prototypes = state["prototypes"].to(args.device)
    te_acc = _eval_loop(model, test_loader, args.device, prototypes)

    print(f"\nbest val acc(14): {best_val_acc:.3f}")
    print(f"test acc(14)    : {te_acc:.3f}")

    (args.out / "config.json").write_text(json.dumps({
        "model": "IchiPingV1_embed",
        "embed_dim": model.cfg.embed_dim,
        "n_params": n_params,
        "epochs": args.epochs,
        "batch":  args.batch,
        "lr":     args.lr,
        "temperature": args.temperature,
        "feature_mode": args.feature_mode,
        "feature_aug":  args.feature_aug,
        "best_val_acc_14cls": best_val_acc,
        "test_acc_14cls":     te_acc,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
