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
    from dataset import IchiPingDataset, split_indices, class_of
    from model_32cls import (
        IchiPingV1_32cls, IchiPingV1_32clsConfig, idx_to_bits,
        N_CLASSES, SIZE_PRESETS,
    )
    from model_32cls_neutron import (
        IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig,
        SIZE_PRESETS as SIZE_PRESETS_NEUTRON,
    )
    from augment import default_train_transform, default_feature_transform
except ImportError:
    from .dataset import IchiPingDataset, split_indices, class_of   # type: ignore
    from .model_32cls import (                                      # type: ignore
        IchiPingV1_32cls, IchiPingV1_32clsConfig, idx_to_bits,
        N_CLASSES, SIZE_PRESETS,
    )
    from .model_32cls_neutron import (                              # type: ignore
        IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig,
        SIZE_PRESETS as SIZE_PRESETS_NEUTRON,
    )
    from .augment import default_train_transform, default_feature_transform  # type: ignore


# 14 等価クラスの正規順 (analyze_full32._CLASS_ORDER と一致)。
# 学習自体は 32-class softmax だが、評価時に class_of で 14-class に
# 畳み込んで NN 設計ドキュメント側の指標と直接比較できるようにする。
CLASS_ORDER_14 = ["A1", "A2", "B1", "B2", "B3", "B4",
                  "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


# Hamming-distance soft label の事前計算 (32 真状態 x 32 予測状態)
def _build_hamming_soft_labels(alpha: float) -> torch.Tensor:
    """Returns (32, 32) soft label matrix。row=true_idx, col=class。
    softmax(-alpha * hamming_dist) で正規化。alpha 大きいほど hard label に近づく。"""
    n = 32
    matrix = torch.zeros(n, n, dtype=torch.float32)
    for i in range(n):
        bi = [(i >> k) & 1 for k in range(5)]
        for j in range(n):
            bj = [(j >> k) & 1 for k in range(5)]
            ham = sum(int(a != b) for a, b in zip(bi, bj))
            matrix[i, j] = -float(alpha) * ham
    return torch.softmax(matrix, dim=-1)


def _one_epoch(model, loader, device, optimiser=None,
               loss_mode="ce", hamming_soft=None):
    is_train = optimiser is not None
    model.train(is_train)
    total = 0; correct = 0; loss_sum = 0.0
    for batch in loader:
        x = batch["x"].to(device)
        y = batch["state_idx"].to(device)
        logits = model(x)
        if loss_mode == "hamming":
            # soft label cross-entropy: -mean(sum(soft * log_softmax(logits)))
            soft = hamming_soft[y]                         # (B, 32)
            log_p = torch.log_softmax(logits, dim=-1)      # (B, 32)
            loss = -(soft * log_p).sum(dim=-1).mean()
        else:
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
    ap.add_argument("--feature-mode",
                    choices=("chirp", "noise", "noise_diff", "noise_diff_norm"),
                    default="chirp",
                    help="特徴量抽出: chirp / noise / noise_diff / noise_diff_norm "
                         "(noise_diff_norm は per-frame zero-mean unit-variance 正規化追加)")
    ap.add_argument("--baseline-override-dir", type=Path, default=None,
                    dest="baseline_override_dir",
                    help="noise_diff の baseline を全 captures で共通の dir から取る "
                         "(指定 dir の s00000 frame_*.wav を使用)。"
                         "factory_baseline.h と一致させたい運用 (= 推論時 factory モード固定運用) "
                         "では captures/eval_noise_low を指定。")
    ap.add_argument("--baseline-jitter-dirs", type=Path, nargs="*", default=None,
                    dest="baseline_jitter_dirs",
                    help="Baseline jittering augmentation: 指定した複数 dir の s00000 を baseline として "
                         "使う dataset を **重複生成** して ConcatDataset で連結。"
                         "同じ録音を N 種類の baseline で diff した N サンプルとして学習に投入 → "
                         "モデルが baseline 不変性を学習。例: "
                         "--baseline-jitter-dirs captures/full_32_train_v{1,2,3,4,5} で 5x データ拡張。"
                         "--baseline-override-dir とは排他。")
    ap.add_argument("--feature-aug", action="store_true",
                    help="FreqMask + SpectralJitter を特徴量空間で適用 (noise 系で推奨)")
    ap.add_argument("--loss-mode", choices=("ce", "hamming"), default="ce",
                    help="ce: 通常 cross-entropy / hamming: Hamming-distance soft label CE")
    ap.add_argument("--hamming-alpha", type=float, default=2.0,
                    help="hamming soft label の鋭さ (大 = hard 寄り、既定 2.0)")
    ap.add_argument("--size", choices=tuple(SIZE_PRESETS.keys()), default="S",
                    help="モデルサイズ S/M/L/XL (model_14cls と同じ preset)")
    ap.add_argument("--seed", type=int, default=0,
                    help="乱数 seed (torch / numpy / random / split_indices)")
    ap.add_argument("--spike-fix", action="store_true",
                    help="val_loss spike 対策バンドル: XL dropout 0.4→0.3 / "
                         "FreqMask max 60→40 / lr cosine + warmup")
    ap.add_argument("--arch", choices=("conv1d", "neutron"), default="conv1d",
                    help="モデルアーキ。conv1d=旧 IchiPingV1_32cls / "
                         "neutron=Conv2D ベース (MCXN947 Neutron NPU 互換)")
    ap.add_argument("--aug-strong", action="store_true",
                    help="強化 augmentation (TimeShift ±20ms, LevelJitter ±4dB, "
                         "NoiseOverlay SNR 0-35dB p=0.9, GaussianHiss 追加, "
                         "FreqMask max 80/3個, SpectralJitter σ=0.6dB)。"
                         "低ノイズ条件のみで採取したデータの汎化性能補強に使う。")
    args = ap.parse_args(argv)

    import random
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"device: {args.device}")

    if args.aug_strong:
        try:
            from augment import strong_train_transform, strong_feature_transform
        except ImportError:
            from .augment import strong_train_transform, strong_feature_transform  # type: ignore
        train_tf = strong_train_transform(args.ambient_dirs)
        feat_tf = (strong_feature_transform(args.feature_mode, spike_fix=args.spike_fix)
                   if args.feature_aug else None)
        print("AUG STRONG: TimeShift ±20ms / LevelJitter ±4dB / "
              "NoiseOverlay SNR 0-35dB p=0.9 / GaussianHiss / "
              "FreqMask max 80 ×3 / SpectralJitter σ=0.6")
    else:
        train_tf = default_train_transform(args.ambient_dirs)
        feat_tf = (default_feature_transform(args.feature_mode, spike_fix=args.spike_fix)
                   if args.feature_aug else None)
    if feat_tf is not None:
        print(f"feature-space aug: FreqMask + SpectralJitter (spike_fix={args.spike_fix})")
    # Baseline jittering: 複数の baseline で同じデータを重複生成して ConcatDataset
    if args.baseline_jitter_dirs:
        if args.baseline_override_dir is not None:
            raise SystemExit("--baseline-jitter-dirs と --baseline-override-dir は排他")
        from torch.utils.data import ConcatDataset
        ds_per_bl = [
            IchiPingDataset(captures_dirs=args.captures, transform=train_tf,
                            feature_mode=args.feature_mode,
                            feature_transform=feat_tf,
                            baseline_override_dir=bl)
            for bl in args.baseline_jitter_dirs
        ]
        ds_eval_per_bl = [
            IchiPingDataset(captures_dirs=args.captures, transform=None,
                            feature_mode=args.feature_mode,
                            feature_transform=None,
                            baseline_override_dir=bl)
            for bl in args.baseline_jitter_dirs
        ]
        ds_all  = ConcatDataset(ds_per_bl)
        ds_eval = ConcatDataset(ds_eval_per_bl)
        print(f"baseline JITTER: {len(args.baseline_jitter_dirs)} baseline 種類 × "
              f"{len(ds_per_bl[0])} sample = {len(ds_all)} 総サンプル")
        for bl in args.baseline_jitter_dirs:
            print(f"  baseline source: {bl}/s00000")
        # state_counts() は IchiPingDataset 固有メソッドなので 1 個目から取る
        first = ds_per_bl[0]
    else:
        ds_all = IchiPingDataset(captures_dirs=args.captures, transform=train_tf,
                                  feature_mode=args.feature_mode,
                                  feature_transform=feat_tf,
                                  baseline_override_dir=args.baseline_override_dir)
        ds_eval = IchiPingDataset(captures_dirs=args.captures, transform=None,
                                  feature_mode=args.feature_mode,
                                  feature_transform=None,
                                  baseline_override_dir=args.baseline_override_dir)
        if args.baseline_override_dir:
            print(f"baseline OVERRIDE: 全 captures に対して {args.baseline_override_dir}/s00000 を baseline 使用")
        first = ds_all
    print(f"loaded {len(ds_all)} examples (base ds={len(first)} unique recordings)")
    print(f"state distribution: {sorted(first.state_counts().items())[:5]} ...")

    n = len(ds_all)
    tr, va, te = split_indices(n, seed=args.seed)
    train_loader = DataLoader(Subset(ds_all,  tr), batch_size=args.batch, shuffle=True)
    val_loader   = DataLoader(Subset(ds_eval, va), batch_size=args.batch)
    test_loader  = DataLoader(Subset(ds_eval, te), batch_size=args.batch)

    # spike-fix で XL の dropout を 0.3 に下げる (preset を一時上書き)。
    if args.spike_fix and args.size == "XL":
        SIZE_PRESETS["XL"]["dropout"] = 0.3
        SIZE_PRESETS_NEUTRON["XL"]["dropout"] = 0.3
        print("spike-fix: XL dropout 0.4 → 0.3")

    if args.arch == "neutron":
        model = IchiPingV1_32clsNeutron(
            IchiPingV1_32clsNeutronConfig(size=args.size)).to(args.device)
        arch_name = "IchiPingV1_32clsNeutron"
    else:
        model = IchiPingV1_32cls(IchiPingV1_32clsConfig(size=args.size)).to(args.device)
        arch_name = "IchiPingV1_32cls"
    n_params = sum(p.numel() for p in model.parameters())
    print(f"{arch_name}[{args.size}]: {n_params} params, {N_CLASSES} classes")

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    # spike-fix: lr cosine schedule + 5 epoch warmup
    scheduler = None
    if args.spike_fix:
        warmup_epochs = 5
        def lr_lambda(epoch):
            if epoch < warmup_epochs:
                return (epoch + 1) / warmup_epochs
            progress = (epoch - warmup_epochs) / max(args.epochs - warmup_epochs, 1)
            import math
            return 0.5 * (1 + math.cos(math.pi * progress))
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimiser, lr_lambda)
        print(f"spike-fix: lr schedule (warmup {warmup_epochs}ep → cosine)")

    # Hamming soft label を事前計算 (loss_mode=hamming のときだけ使う)
    hamming_soft = None
    if args.loss_mode == "hamming":
        hamming_soft = _build_hamming_soft_labels(args.hamming_alpha).to(args.device)
        print(f"hamming loss enabled (alpha={args.hamming_alpha})")

    best_val_acc = -1.0
    log_rows = []
    log_path = args.out / "train_log.csv"
    with log_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "wall_s"])
        t0 = time.time()
        for ep in range(1, args.epochs + 1):
            tr_loss, tr_acc = _one_epoch(model, train_loader, args.device, optimiser,
                                         loss_mode=args.loss_mode, hamming_soft=hamming_soft)
            va_loss, va_acc = _one_epoch(model, val_loader,   args.device, optimiser=None,
                                         loss_mode=args.loss_mode, hamming_soft=hamming_soft)
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
                                            "size": args.size,
                                            "arch": args.arch}},
                            args.out / "best.pt")

    # ---- Final test eval ----
    state = torch.load(args.out / "best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(state["state_dict"])
    te_loss, te_acc = _one_epoch(model, test_loader, args.device, optimiser=None,
                                 loss_mode=args.loss_mode, hamming_soft=hamming_soft)

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

    # 32-class の予測を等価クラス (14-class) に畳み込んで再評価。
    # NN 設計ドキュメント側の Information-theoretic 上限 (log2 14 ≒ 3.81 bit)
    # と直接比較したいので、この畳み込み accuracy が事実上の v1 指標。
    cls_to_idx = {c: i for i, c in enumerate(CLASS_ORDER_14)}
    conf14 = np.zeros((len(CLASS_ORDER_14), len(CLASS_ORDER_14)), dtype=np.int64)
    for i in range(N_CLASSES):
        bits_i = idx_to_bits(i)
        cls_i = class_of(np.asarray(bits_i, dtype=np.int64))
        ti14 = cls_to_idx[cls_i]
        for j in range(N_CLASSES):
            bits_j = idx_to_bits(j)
            cls_j = class_of(np.asarray(bits_j, dtype=np.int64))
            pj14 = cls_to_idx[cls_j]
            conf14[ti14, pj14] += conf[i, j]
    correct14 = int(np.trace(conf14))
    total14 = int(conf14.sum())
    te_acc14 = correct14 / max(total14, 1)
    # ヘッダー行 + ラベル列をつけた 14x14 CSV を吐く
    with (args.out / "confusion_14cls.csv").open("w", encoding="utf-8") as fp:
        fp.write("," + ",".join(CLASS_ORDER_14) + "\n")
        for i, c in enumerate(CLASS_ORDER_14):
            fp.write(c + "," + ",".join(str(int(x)) for x in conf14[i]) + "\n")

    print(f"\nbest val acc: {best_val_acc:.3f}")
    print(f"test acc    : {te_acc:.3f}   (32-class, full state)")
    print(f"test acc    : {te_acc14:.3f}   (14-class, equivalence-class collapse)")
    print(f"saved {args.out/'best.pt'} + train_log.csv + confusion_{{32,14}}cls.csv")

    # Save run metadata
    (args.out / "config.json").write_text(json.dumps({
        "model": arch_name,
        "arch":  args.arch,
        "size":  args.size,
        "n_classes": N_CLASSES,
        "n_params": n_params,
        "epochs":   args.epochs,
        "batch":    args.batch,
        "lr":       args.lr,
        "ambient_dirs": [str(p) for p in args.ambient_dirs] if args.ambient_dirs else None,
        "feature_mode": args.feature_mode,
        "feature_aug": args.feature_aug,
        "loss_mode": args.loss_mode,
        "hamming_alpha": args.hamming_alpha if args.loss_mode == "hamming" else None,
        "best_val_acc": best_val_acc,
        "test_acc_32cls": te_acc,
        "test_acc_14cls": te_acc14,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
