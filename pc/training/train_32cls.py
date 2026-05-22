"""Train the 32-class softmax variant (experimental).

Same dataset and feature pipeline as ``train.py``, but the model is the
single-head ``IchiPingV1_32cls`` and the loss is plain cross-entropy
over the 32-class state index.

Use this to test the hypothesis: "can the model exploit sub-equivalence-
class structure (small mechanical bias) to beat the 14-class limit?"
The 14-class observability bound says no — but it costs us nothing to
try, and a useful side-effect is the 32-class confusion matrix which
makes equivalence-class collapses visible at a glance.

Usage:
    cd pc
    uv run --extra training python -m training.train_32cls \\
        --captures captures/full_32_v2 --out runs/v1_32cls --epochs 50
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
    from dataset import IchiPingDataset, split_indices
    from model_32cls import IchiPingV1_32cls, idx_to_bits, N_CLASSES
    from augment import default_train_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices            # type: ignore
    from .model_32cls import IchiPingV1_32cls, idx_to_bits, N_CLASSES  # type: ignore
    from .augment import default_train_transform                    # type: ignore


def _one_epoch(model, loader, device, optimiser=None):
    is_train = optimiser is not None
    model.train(is_train)
    total = 0; correct = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        y = batch["state_idx"].to(device)
        logits = model(x)
        loss = F.cross_entropy(logits, y)
        if is_train:
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        loss_sum += float(loss.item()) * y.size(0)
        pred = logits.argmax(dim=-1)
        correct += int((pred == y).sum().item())
        total += y.size(0)
    return loss_sum / max(total, 1), correct / max(total, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train 32-class IchiPing variant.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--epochs",   type=int,  default=50)
    ap.add_argument("--batch",    type=int,  default=32)
    ap.add_argument("--lr",       type=float, default=1e-3)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs",
                    help="optional silence_* dirs to augment with real ambient noise")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}")

    train_tf = default_train_transform(args.ambient_dirs)
    ds_all = IchiPingDataset(captures_dirs=args.captures, transform=train_tf)
    ds_eval = IchiPingDataset(captures_dirs=args.captures, transform=None)
    print(f"loaded {len(ds_all)} examples across {len(args.captures)} captures dirs")
    print(f"state distribution: {sorted(ds_all.state_counts().items())[:5]} ...")

    n = len(ds_all)
    tr, va, te = split_indices(n, seed=0)
    train_loader = DataLoader(Subset(ds_all,  tr), batch_size=args.batch, shuffle=True)
    val_loader   = DataLoader(Subset(ds_eval, va), batch_size=args.batch)
    test_loader  = DataLoader(Subset(ds_eval, te), batch_size=args.batch)

    model = IchiPingV1_32cls().to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"IchiPingV1_32cls: {n_params} params, {N_CLASSES} classes")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val_acc = -1.0
    log_rows = []
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
            print(f"  epoch {ep:3d}  tr_loss={tr_loss:.3f}  tr_acc={tr_acc:.3f}  "
                  f"va_loss={va_loss:.3f}  va_acc={va_acc:.3f}")
            if va_acc > best_val_acc:
                best_val_acc = va_acc
                torch.save({"state_dict": model.state_dict(),
                             "config":     {"n_classes": N_CLASSES}},
                            args.out / "best.pt")

    # ---- Final test eval ----
    state = torch.load(args.out / "best.pt", map_location=args.device)
    model.load_state_dict(state["state_dict"])
    te_loss, te_acc = _one_epoch(model, test_loader, args.device, optimiser=None)

    # 32-class confusion matrix on test
    model.eval()
    conf = np.zeros((N_CLASSES, N_CLASSES), dtype=np.int64)
    with torch.no_grad():
        for batch in test_loader:
            x = batch["x"].to(args.device)
            y = batch["state_idx"].numpy()
            p = model(x).argmax(dim=-1).cpu().numpy()
            for ti, pi in zip(y, p):
                conf[ti, pi] += 1

    np.savetxt(args.out / "confusion_32cls.csv", conf, fmt="%d", delimiter=",")
    print(f"\nbest val acc: {best_val_acc:.3f}")
    print(f"test acc    : {te_acc:.3f}   (32-class)")
    print(f"saved {args.out/'best.pt'} + train_log.csv + confusion_32cls.csv")

    # Save run metadata
    (args.out / "config.json").write_text(json.dumps({
        "model": "IchiPingV1_32cls",
        "n_classes": N_CLASSES,
        "n_params": n_params,
        "epochs":   args.epochs,
        "batch":    args.batch,
        "lr":       args.lr,
        "ambient_dirs": [str(p) for p in args.ambient_dirs] if args.ambient_dirs else None,
        "best_val_acc": best_val_acc,
        "test_acc":     te_acc,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
