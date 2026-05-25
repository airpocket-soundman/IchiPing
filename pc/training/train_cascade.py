"""階層 cascade 構成 (model_cascade.IchiPingV1_cascade) の学習スクリプト。

loss = CE(L1) + α × Σ_g CE(L2_g, mask=samples_in_g)
α は --l2-weight で調整 (既定 1.0)。

Usage:
    cd pc
    uv run --extra training python -m training.train_cascade \\
        --captures captures/full_32_train_v1 \\
        --out runs/v1_train_cascade --epochs 60 \\
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
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14
    from model_cascade import (
        IchiPingV1_cascade,
        bits_to_group,
        bits_to_within_group,
        group_within_to_14cls_idx,
        N_GROUPS,
    )
    from augment import default_train_transform, default_feature_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14            # type: ignore
    from .model_cascade import (                                                    # type: ignore
        IchiPingV1_cascade,
        bits_to_group,
        bits_to_within_group,
        group_within_to_14cls_idx,
        N_GROUPS,
    )
    from .augment import default_train_transform, default_feature_transform        # type: ignore


def _bits_to_targets(state5: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """state5 (B, 5) → (group_idx (B,), within_idx (B,))。
    within_idx は group ごとの内部 index (A:0..1, B:0..3, C:0..7)。
    """
    bits = state5.cpu().numpy()
    g, w = [], []
    for b in bits:
        a, bw, cw, AB, BC = int(b[0]), int(b[1]), int(b[2]), int(b[3]), int(b[4])
        g.append(bits_to_group(a, bw, cw, AB, BC))
        w.append(bits_to_within_group(a, bw, cw, AB, BC))
    return torch.tensor(g, dtype=torch.long), torch.tensor(w, dtype=torch.long)


def _compute_loss(out, group_idx, within_idx, l2_weight: float):
    """L1 全サンプル + 各 group の L2 (該当サンプルだけ)。"""
    l1 = F.cross_entropy(out["L1"], group_idx)
    l2_loss = torch.tensor(0.0, device=out["L1"].device)
    head_names = {0: "L2_A", 1: "L2_B", 2: "L2_C"}
    for g_id, name in head_names.items():
        mask = (group_idx == g_id)
        if mask.sum() == 0:
            continue
        logits = out[name][mask]
        target = within_idx[mask]
        l2_loss = l2_loss + F.cross_entropy(logits, target)
    return l1 + l2_weight * l2_loss, float(l1.item()), float(l2_loss.item())


def _predict_14cls(model, x):
    return model.predict_14cls(x)


def _one_epoch(model, loader, device, optimiser=None, l2_weight=1.0):
    is_train = optimiser is not None
    model.train(is_train)
    total = 0; correct = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        state5 = batch["state5"]
        group_idx, within_idx = _bits_to_targets(state5)
        group_idx = group_idx.to(device)
        within_idx = within_idx.to(device)
        out = model(x)
        loss, _, _ = _compute_loss(out, group_idx, within_idx, l2_weight)
        if is_train:
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        loss_sum += float(loss.item()) * state5.size(0)
        pred14 = model.predict_14cls(x).cpu().numpy()
        # true 14cls: group_within_to_14cls_idx を再計算
        g_np = group_idx.cpu().numpy()
        w_np = within_idx.cpu().numpy()
        true14 = np.array([group_within_to_14cls_idx(g, w) for g, w in zip(g_np, w_np)])
        correct += int((pred14 == true14).sum())
        total += state5.size(0)
    return loss_sum / max(total, 1), correct / max(total, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train IchiPingV1_cascade (hierarchical).")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--epochs",   type=int,  default=60)
    ap.add_argument("--batch",    type=int,  default=32)
    ap.add_argument("--lr",       type=float, default=1e-3)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--l2-weight", type=float, default=1.0,
                    help="L2 head loss の重み (L1 = 1.0 固定)")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise_diff")
    ap.add_argument("--feature-aug", action="store_true")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}, "
          f"feature_aug: {args.feature_aug}, l2_weight: {args.l2_weight}")

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

    model = IchiPingV1_cascade().to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"IchiPingV1_cascade: {n_params} params, L1=3 + L2(A=2, B=4, C=8)")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val_acc = -1.0
    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "wall_s"])
        t0 = time.time()
        for ep in range(1, args.epochs + 1):
            tr_loss, tr_acc = _one_epoch(model, train_loader, args.device,
                                         optimiser, args.l2_weight)
            va_loss, va_acc = _one_epoch(model, val_loader,   args.device,
                                         optimiser=None, l2_weight=args.l2_weight)
            w.writerow([ep, f"{tr_loss:.4f}", f"{tr_acc:.4f}",
                         f"{va_loss:.4f}", f"{va_acc:.4f}", f"{time.time()-t0:.1f}"])
            fp.flush()
            print(f"  epoch {ep:3d}  tr_loss={tr_loss:.3f}  tr_acc(14)={tr_acc:.3f}  "
                  f"va_loss={va_loss:.3f}  va_acc(14)={va_acc:.3f}")
            if va_acc > best_val_acc:
                best_val_acc = va_acc
                torch.save({"state_dict": model.state_dict()},
                            args.out / "best.pt")

    state = torch.load(args.out / "best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])
    te_loss, te_acc = _one_epoch(model, test_loader, args.device,
                                 optimiser=None, l2_weight=args.l2_weight)

    print(f"\nbest val acc(14): {best_val_acc:.3f}")
    print(f"test acc(14)    : {te_acc:.3f}")

    (args.out / "config.json").write_text(json.dumps({
        "model": "IchiPingV1_cascade",
        "n_groups": N_GROUPS,
        "n_params": n_params,
        "epochs": args.epochs,
        "batch":  args.batch,
        "lr":     args.lr,
        "l2_weight": args.l2_weight,
        "feature_mode": args.feature_mode,
        "feature_aug":  args.feature_aug,
        "best_val_acc_14cls": best_val_acc,
        "test_acc_14cls":     te_acc,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
