"""IchiPing v1 本命学習スクリプト — 14-class softmax 単ヘッド。

NN 設計 (docs/nn_design.html) で確定した v1 構成の学習ループ。
train_32cls.py と同じデータ・特徴量・最適化を使い、ターゲットだけ
state_idx (0..31) から cls_idx_14 (0..13) に切り替えてある。

理論的には情報理論的上限 (log2 14 ≒ 3.81 bit) が天井なので、
val/test acc が 1.0 に到達するのが期待される。32-class 学習で観察された
「等価クラス内サブ状態を当てに行く」現象は構造的に排除される。

Usage:
    cd pc
    uv run --extra training python -m training.train_14cls \\
        --captures captures/full_32_train_v1 \\
        --out runs/v1_train_14cls --epochs 60 --feature-mode noise
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, CLASS_ORDER_14
    from model_14cls import IchiPingV1_14cls, IchiPingV1_14clsConfig, N_CLASSES, SIZE_PRESETS
    from augment import default_train_transform, default_feature_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices, CLASS_ORDER_14  # type: ignore
    from .model_14cls import IchiPingV1_14cls, IchiPingV1_14clsConfig, N_CLASSES, SIZE_PRESETS  # type: ignore
    from .augment import default_train_transform, default_feature_transform  # type: ignore


def _one_epoch(model, loader, device, optimiser=None):
    """1 epoch 分の学習または検証。optimiser=None なら eval。"""
    is_train = optimiser is not None
    model.train(is_train)
    total = 0; correct = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        y = batch["cls_idx_14"].to(device)
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
    ap = argparse.ArgumentParser(description="Train v1 14-class IchiPing model.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--epochs",   type=int,  default=60)
    ap.add_argument("--batch",    type=int,  default=32)
    ap.add_argument("--lr",       type=float, default=1e-3)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--ambient-dirs", type=Path, nargs="*", default=None,
                    dest="ambient_dirs",
                    help="任意で silence_* 等の実環境ノイズを augmentation に混ぜる")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff"),
                    default="noise",
                    help="特徴量抽出。noise_diff は run ごとの s00000 ベースラインを per-bin 引く版")
    ap.add_argument("--feature-aug", action="store_true",
                    help="FreqMask / SpectralJitter を特徴量空間で適用 (noise 系で推奨)")
    ap.add_argument("--size", choices=tuple(SIZE_PRESETS.keys()), default="S",
                    help="モデルサイズ: S(~14k) / M(~30k, 深さ+1) / L(~52k, 幅2倍) / XL(~104k, 幅2倍+Flatten)")
    ap.add_argument("--seed", type=int, default=0,
                    help="乱数 seed (torch / numpy / random / split_indices すべてに適用)。"
                         "マルチシード実験で size 性能の seed 分散を測るのに使う。")
    ap.add_argument("--spike-fix", action="store_true",
                    help="val_loss spike 対策をまとめて有効化:"
                         " XL の dropout 0.4→0.3 / FreqMask max_width 60→40 / lr cosine + warmup")
    args = ap.parse_args(argv)

    # 全乱数源を同じ seed で初期化 (再現性 + 多シード比較用)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}, feature_mode: {args.feature_mode}")

    train_tf = default_train_transform(args.ambient_dirs)
    feat_tf = (default_feature_transform(args.feature_mode, spike_fix=args.spike_fix)
               if args.feature_aug else None)
    if feat_tf is not None:
        print(f"feature-space aug: FreqMask + SpectralJitter "
              f"(spike_fix={args.spike_fix})")
    ds_all = IchiPingDataset(captures_dirs=args.captures, transform=train_tf,
                              feature_mode=args.feature_mode,
                              feature_transform=feat_tf)
    ds_eval = IchiPingDataset(captures_dirs=args.captures, transform=None,
                              feature_mode=args.feature_mode,
                              feature_transform=None)
    print(f"loaded {len(ds_all)} examples from {len(args.captures)} captures dir(s)")

    n = len(ds_all)
    tr, va, te = split_indices(n, seed=args.seed)
    train_loader = DataLoader(Subset(ds_all,  tr), batch_size=args.batch, shuffle=True)
    val_loader   = DataLoader(Subset(ds_eval, va), batch_size=args.batch)
    test_loader  = DataLoader(Subset(ds_eval, te), batch_size=args.batch)

    # spike-fix で XL の dropout を 0.3 に下げる (preset を一時上書き)。
    if args.spike_fix and args.size == "XL":
        SIZE_PRESETS["XL"]["dropout"] = 0.3
        print("spike-fix: XL dropout 0.4 → 0.3")

    model = IchiPingV1_14cls(IchiPingV1_14clsConfig(size=args.size)).to(args.device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"IchiPingV1_14cls[{args.size}]: {n_params} params, {N_CLASSES} classes")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    # spike-fix: lr cosine schedule + 5 epoch warmup
    scheduler = None
    if args.spike_fix:
        # warmup 5 epoch (linear) → cosine annealing
        warmup_epochs = 5
        def lr_lambda(epoch):
            if epoch < warmup_epochs:
                return (epoch + 1) / warmup_epochs
            # cosine from 1.0 to 0.0 over remaining epochs
            progress = (epoch - warmup_epochs) / max(args.epochs - warmup_epochs, 1)
            import math
            return 0.5 * (1 + math.cos(math.pi * progress))
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimiser, lr_lambda)
        print(f"spike-fix: lr schedule (warmup {warmup_epochs}ep linear → cosine)")

    best_val_acc = -1.0
    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "wall_s"])
        t0 = time.time()
        for ep in range(1, args.epochs + 1):
            tr_loss, tr_acc = _one_epoch(model, train_loader, args.device, optimiser)
            va_loss, va_acc = _one_epoch(model, val_loader,   args.device, optimiser=None)
            if scheduler is not None:
                scheduler.step()
            w.writerow([ep, f"{tr_loss:.4f}", f"{tr_acc:.4f}",
                         f"{va_loss:.4f}", f"{va_acc:.4f}", f"{time.time()-t0:.1f}"])
            fp.flush()
            print(f"  epoch {ep:3d}  tr_loss={tr_loss:.3f}  tr_acc={tr_acc:.3f}  "
                  f"va_loss={va_loss:.3f}  va_acc={va_acc:.3f}")
            if va_acc > best_val_acc:
                best_val_acc = va_acc
                torch.save({"state_dict": model.state_dict(),
                             "config":     {"n_classes": N_CLASSES,
                                            "class_order": list(CLASS_ORDER_14),
                                            "size": args.size}},
                            args.out / "best.pt")

    # ----- 最終 test 評価 + 混同行列 -----
    state = torch.load(args.out / "best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])
    te_loss, te_acc = _one_epoch(model, test_loader, args.device, optimiser=None)

    model.eval()
    conf = np.zeros((N_CLASSES, N_CLASSES), dtype=np.int64)
    with torch.no_grad():
        for batch in test_loader:
            x = batch["x"].to(args.device)
            y = batch["cls_idx_14"].numpy()
            p = model(x).argmax(dim=-1).cpu().numpy()
            for ti, pi in zip(y, p):
                conf[ti, pi] += 1

    # ヘッダ付きで保存 (32cls 側と CSV 形式を揃える)
    with (args.out / "confusion_14cls.csv").open("w", encoding="utf-8") as fp:
        fp.write("," + ",".join(CLASS_ORDER_14) + "\n")
        for i, c in enumerate(CLASS_ORDER_14):
            fp.write(c + "," + ",".join(str(int(x)) for x in conf[i]) + "\n")

    print(f"\nbest val acc: {best_val_acc:.3f}")
    print(f"test acc    : {te_acc:.3f}   (14-class direct)")
    print(f"saved {args.out/'best.pt'} + train_log.csv + confusion_14cls.csv")

    (args.out / "config.json").write_text(json.dumps({
        "model": "IchiPingV1_14cls",
        "size": args.size,
        "preset": SIZE_PRESETS[args.size],
        "n_classes": N_CLASSES,
        "n_params": n_params,
        "epochs":   args.epochs,
        "batch":    args.batch,
        "lr":       args.lr,
        "seed":     args.seed,
        "feature_mode": args.feature_mode,
        "feature_aug": args.feature_aug,
        "class_order": list(CLASS_ORDER_14),
        "ambient_dirs": [str(p) for p in args.ambient_dirs] if args.ambient_dirs else None,
        "best_val_acc": best_val_acc,
        "test_acc_14cls": te_acc,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
