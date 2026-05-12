"""Train IchiPing v1 (DigiKey).

Multi-task 1D-CNN that predicts 6 outputs from one log-magnitude spectrum
(see model.py and spec.html §4.3 for the architecture rationale).

Usage:
    cd pc
    python -m training.train \
        --captures ../captures \
        --epochs 50 \
        --batch 32 \
        --out ../runs/v1_pilot

Output:
    <out>/best.pt           PyTorch checkpoint (state_dict + cfg)
    <out>/best.onnx         ONNX export for eIQ Toolkit (INT8 quantisation)
    <out>/train_log.csv     per-epoch losses + val metrics
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Add this directory to the import path so `python -m training.train` and
# `python train.py` from inside pc/training both work without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

from dataset import IchiPingDataset, collate_targets
from model import IchiPingV1, ModelConfig


# Loss weights — see spec.html §4.3. any_open is the highest-signal task,
# so the regression heads are downweighted to keep optimisation balanced.
LOSS_WEIGHTS = {
    "any_open": 1.0,
    "door_AB":  0.5,
    "door_BC":  0.7,
    "window_a": 0.5,
    "window_b": 0.7,
    "window_c": 0.7,
}


def make_losses() -> dict:
    return {
        "any_open": nn.BCEWithLogitsLoss(),
        "door_AB":  nn.MSELoss(),
        "door_BC":  nn.CrossEntropyLoss(),
        "window_a": nn.MSELoss(),
        "window_b": nn.CrossEntropyLoss(),
        "window_c": nn.CrossEntropyLoss(),
    }


def compute_loss(out, y, losses) -> torch.Tensor:
    total = out["any_open"].new_zeros(())
    for key, w in LOSS_WEIGHTS.items():
        total = total + w * losses[key](out[key], y[key])
    return total


@torch.no_grad()
def eval_epoch(model, loader, losses, device) -> dict:
    model.eval()
    tot, n = 0.0, 0
    correct_any = 0
    for x, y in loader:
        x = x.to(device)
        y = {k: v.to(device) for k, v in y.items()}
        out = model(x)
        loss = compute_loss(out, y, losses)
        bs = x.size(0)
        tot += loss.item() * bs
        n += bs
        pred_any = (torch.sigmoid(out["any_open"]) > 0.5).float()
        correct_any += (pred_any == y["any_open"]).sum().item()
    return {"val_loss": tot / max(n, 1), "val_acc_any_open": correct_any / max(n, 1)}


def export_onnx(model: IchiPingV1, path: Path) -> None:
    model.eval()
    dummy = torch.randn(1, model.cfg.input_bins)
    torch.onnx.export(
        model, dummy, str(path),
        input_names=["spectrum"],
        output_names=list(IchiPingV1.OUTPUT_KEYS),
        dynamic_axes={"spectrum": {0: "batch"}},
        opset_version=17,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Train IchiPing v1")
    p.add_argument("--captures", type=Path, required=True,
                   help="path to a captures/ directory written by receiver.py")
    p.add_argument("--out", type=Path, required=True,
                   help="output directory for checkpoint + onnx + log")
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)

    dataset = IchiPingDataset(args.captures)
    n_val = max(1, int(len(dataset) * args.val_frac))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(dataset, [n_train, n_val],
                                    generator=torch.Generator().manual_seed(args.seed))

    train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True,
                              collate_fn=collate_targets, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=args.batch, shuffle=False,
                            collate_fn=collate_targets, num_workers=0)

    model = IchiPingV1().to(args.device)
    n_params = model.num_parameters()
    print(f"model parameters: {n_params:,}  (~{n_params/1024:.1f} KB INT8 estimate)")
    print(f"dataset: train {n_train}  val {n_val}")

    losses = make_losses()
    optim = torch.optim.Adam(model.parameters(), lr=args.lr)

    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="", encoding="utf-8") as f:
        log = csv.writer(f)
        log.writerow(["epoch", "train_loss", "val_loss", "val_acc_any_open"])

        best_val = float("inf")
        for epoch in range(1, args.epochs + 1):
            model.train()
            tot, n = 0.0, 0
            for x, y in train_loader:
                x = x.to(args.device)
                y = {k: v.to(args.device) for k, v in y.items()}
                out = model(x)
                loss = compute_loss(out, y, losses)
                optim.zero_grad()
                loss.backward()
                optim.step()
                bs = x.size(0)
                tot += loss.item() * bs
                n += bs

            train_loss = tot / max(n, 1)
            metrics = eval_epoch(model, val_loader, losses, args.device)
            log.writerow([epoch, f"{train_loss:.4f}", f"{metrics['val_loss']:.4f}",
                          f"{metrics['val_acc_any_open']:.4f}"])
            f.flush()
            print(f"epoch {epoch:3d}  train {train_loss:.4f}  "
                  f"val {metrics['val_loss']:.4f}  "
                  f"acc(any_open) {metrics['val_acc_any_open']:.3f}")

            if metrics["val_loss"] < best_val:
                best_val = metrics["val_loss"]
                torch.save(
                    {"state_dict": model.state_dict(),
                     "cfg": asdict(model.cfg),
                     "epoch": epoch, "val_loss": best_val},
                    args.out / "best.pt",
                )

    print(f"\nbest val_loss = {best_val:.4f}")
    print(f"exporting ONNX → {args.out / 'best.onnx'}")
    export_onnx(model, args.out / "best.onnx")

    with (args.out / "config.json").open("w", encoding="utf-8") as f:
        json.dump({"args": {k: str(v) if isinstance(v, Path) else v
                            for k, v in vars(args).items()},
                   "model_cfg": asdict(ModelConfig()),
                   "loss_weights": LOSS_WEIGHTS,
                   "n_params": n_params}, f, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
