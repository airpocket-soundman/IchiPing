"""IchiPingV1_32cls 用の評価 + プロット生成スクリプト。

train_32cls.py で保存した best.pt をロードして、指定 captures に対する
推論を回し、以下を吐く:

  out/
    report.json            -- overall acc / per-class F1 / 14-class collapse acc
    confusion_32cls.csv    -- 32x32 行列 (state index 順)
    confusion_14cls.csv    -- 14x14 行列 (A1..C8 順)
    confusion_32cls.png    -- 32x32 ヒートマップ
    confusion_14cls.png    -- 14x14 ヒートマップ (数値オーバーレイ)
    training_curves.png    -- train_log.csv があれば loss/acc 推移

学習側 train_32cls.py が test 分割の 32x32 / 14x14 を保存するので、
本スクリプトは「全データに対する」プロット視覚化を主用途とする。
test split に絞った再評価は --split test (デフォルト all) で切り替え。

Usage:
    cd pc
    uv run --extra training python -m training.infer_32cls \\
        --captures captures/full_32_train_v1 \\
        --ckpt runs/v1_train_60ep/best.pt \\
        --out runs/v1_train_60ep/eval \\
        --feature-mode noise
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

try:
    from dataset import IchiPingDataset, split_indices, class_of
    from model_32cls import IchiPingV1_32cls, IchiPingV1_32clsConfig, idx_to_bits, N_CLASSES
    from model_32cls_neutron import (
        IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig,
    )
except ImportError:
    from .dataset import IchiPingDataset, split_indices, class_of   # type: ignore
    from .model_32cls import (                                      # type: ignore
        IchiPingV1_32cls, IchiPingV1_32clsConfig, idx_to_bits, N_CLASSES,
    )
    from .model_32cls_neutron import (                              # type: ignore
        IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig,
    )


CLASS_ORDER_14 = ["A1", "A2", "B1", "B2", "B3", "B4",
                  "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def state_label_32(idx: int) -> str:
    """idx 0..31 → 'sABCDE' ラベル。"""
    bits = idx_to_bits(idx)
    return "s" + "".join(str(b) for b in bits)


def predict_all(model, loader, device):
    """全バッチを推論して (true_idx, pred_idx) numpy 配列を返す。"""
    model.eval()
    trues, preds = [], []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            y = batch["state_idx"].numpy()
            p = model(x).argmax(dim=-1).cpu().numpy()
            trues.append(y)
            preds.append(p)
    return np.concatenate(trues), np.concatenate(preds)


def collapse_to_14(true32: np.ndarray, pred32: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """32-class 予測を等価クラス 14 に畳み込む。"""
    cls_idx = {c: i for i, c in enumerate(CLASS_ORDER_14)}

    def to14(idx_arr: np.ndarray) -> np.ndarray:
        out = np.zeros(idx_arr.size, dtype=np.int64)
        for k, idx in enumerate(idx_arr):
            bits = np.asarray(idx_to_bits(int(idx)), dtype=np.int64)
            out[k] = cls_idx[class_of(bits)]
        return out

    return to14(true32), to14(pred32)


def confusion(n: int, true: np.ndarray, pred: np.ndarray) -> np.ndarray:
    m = np.zeros((n, n), dtype=np.int64)
    for t, p in zip(true, pred):
        m[t, p] += 1
    return m


def per_class_f1(conf: np.ndarray, labels: list[str]) -> dict:
    """混同行列から precision / recall / F1 を出す。"""
    out = {}
    for i, c in enumerate(labels):
        tp = int(conf[i, i])
        fp = int(conf[:, i].sum() - tp)
        fn = int(conf[i, :].sum() - tp)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        out[c] = {"precision": prec, "recall": rec, "f1": f1,
                  "support": int(conf[i, :].sum())}
    return out


def save_confusion_csv(conf: np.ndarray, labels: list[str], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["true\\pred"] + labels)
        for i, c in enumerate(labels):
            w.writerow([c] + [int(x) for x in conf[i]])


def save_confusion_png(conf: np.ndarray, labels: list[str], title: str,
                       out: Path, *, annotate: bool = True) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(labels)
    fig, ax = plt.subplots(figsize=(max(7, n * 0.35 + 2), max(6, n * 0.35 + 1.5)))
    # ゼロのときに log(0) 警告を避けるため +1 で擬似 log スケール
    im = ax.imshow(conf, cmap="Blues", aspect="auto")
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=90 if n > 16 else 45, fontsize=7 if n > 16 else 9)
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=7 if n > 16 else 9)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title)
    if annotate and n <= 16:
        vmax = conf.max() if conf.max() > 0 else 1
        for i in range(n):
            for j in range(n):
                v = conf[i, j]
                if v > 0:
                    ax.text(j, i, str(int(v)), ha="center", va="center",
                            color="white" if v > vmax / 2 else "black",
                            fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02, label="count")
    plt.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def save_training_curves(train_log_csv: Path, out: Path) -> bool:
    """train_log.csv から train/val の loss + acc 推移を 2 段プロット。"""
    if not train_log_csv.exists():
        return False
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    with train_log_csv.open(encoding="utf-8") as fp:
        r = csv.DictReader(fp)
        for row in r:
            rows.append(row)
    if not rows:
        return False
    ep = [int(r["epoch"]) for r in rows]
    tl = [float(r["train_loss"]) for r in rows]
    vl = [float(r["val_loss"]) for r in rows]
    ta = [float(r["train_acc"]) for r in rows]
    va = [float(r["val_acc"]) for r in rows]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    ax1.plot(ep, tl, label="train", color="#1f77b4", linewidth=1.4)
    ax1.plot(ep, vl, label="val",   color="#ff7f0e", linewidth=1.4)
    ax1.set_ylabel("Cross-entropy loss")
    ax1.set_title("Training curves — 32-class softmax (noise feature)")
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    ax2.plot(ep, ta, label="train", color="#1f77b4", linewidth=1.4)
    ax2.plot(ep, va, label="val",   color="#ff7f0e", linewidth=1.4)
    ax2.set_ylim(0, 1.02)
    ax2.set_ylabel("Accuracy (32-class)")
    ax2.set_xlabel("Epoch")
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    plt.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate IchiPingV1_32cls on a captures dir.")
    ap.add_argument("--captures", type=Path, nargs="+", required=True)
    ap.add_argument("--ckpt",     type=Path, required=True)
    ap.add_argument("--out",      type=Path, required=True)
    ap.add_argument("--batch",    type=int, default=32)
    ap.add_argument("--device",   default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--feature-mode", choices=("chirp", "noise", "noise_diff", "noise_diff_norm"),
                    default="chirp",
                    help="特徴量モード (学習時と一致させること)")
    ap.add_argument("--baseline-from", type=Path, default=None,
                    help="noise_diff baseline を別の captures dir から取得 "
                         "(例: --baseline-from captures/eval_quiet)")
    ap.add_argument("--split", choices=("all", "test"), default="all",
                    help="all: 全フレームで評価 / test: train_32cls.py と同じ seed=0 で 15% 切出し")
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
    size = "S"; arch = "conv1d"
    if isinstance(ckpt, dict) and "config" in ckpt and isinstance(ckpt["config"], dict):
        size = ckpt["config"].get("size", "S")
        arch = ckpt["config"].get("arch", "conv1d")
    print(f"model arch: {arch}, size: {size}")
    if arch == "neutron":
        model = IchiPingV1_32clsNeutron(
            IchiPingV1_32clsNeutronConfig(size=size)).to(args.device)
    else:
        model = IchiPingV1_32cls(IchiPingV1_32clsConfig(size=size)).to(args.device)
    state = ckpt["state_dict"] if isinstance(ckpt, dict) and "state_dict" in ckpt else ckpt
    model.load_state_dict(state)

    true32, pred32 = predict_all(model, loader, args.device)
    conf32 = confusion(N_CLASSES, true32, pred32)
    acc32  = float((true32 == pred32).mean())

    true14, pred14 = collapse_to_14(true32, pred32)
    conf14 = confusion(len(CLASS_ORDER_14), true14, pred14)
    acc14  = float((true14 == pred14).mean())

    per_class = per_class_f1(conf14, CLASS_ORDER_14)
    supports = [v["support"] for v in per_class.values() if v["support"] > 0]
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values() if v["support"] > 0]))

    # ----- 出力 -----
    labels_32 = [state_label_32(i) for i in range(N_CLASSES)]
    save_confusion_csv(conf32, labels_32, args.out / "confusion_32cls.csv")
    save_confusion_csv(conf14, CLASS_ORDER_14, args.out / "confusion_14cls.csv")
    save_confusion_png(conf32, labels_32,
                       f"Confusion 32-class — acc {acc32:.3f}",
                       args.out / "confusion_32cls.png", annotate=False)
    save_confusion_png(conf14, CLASS_ORDER_14,
                       f"Confusion 14-class (collapsed) — acc {acc14:.3f}, "
                       f"macro-F1 {macro_f1:.3f}",
                       args.out / "confusion_14cls.png", annotate=True)

    # train_log.csv は best.pt の隣にある想定 (train_32cls.py の出力レイアウト)
    log_csv = args.ckpt.parent / "train_log.csv"
    curves_path = args.out / "training_curves.png"
    have_curves = save_training_curves(log_csv, curves_path)
    if have_curves:
        print(f"  + training curves → {curves_path}")
    else:
        print(f"  ! train_log.csv not found next to ckpt; skipped training curves")

    report = {
        "n_samples": int(len(ds_use)),
        "split": args.split,
        "feature_mode": args.feature_mode,
        "acc_32cls": acc32,
        "acc_14cls": acc14,
        "macro_f1_14cls": macro_f1,
        "per_class_14cls": per_class,
        "supports_14cls": supports,
    }
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n=== {args.split} ({len(ds_use)} samples) ===")
    print(f"  acc 32-class: {acc32:.3f}")
    print(f"  acc 14-class: {acc14:.3f}  macro-F1 {macro_f1:.3f}")
    print("  per-class F1 (14-class collapse):")
    for c, m in per_class.items():
        if m["support"] > 0:
            print(f"    {c:<3} F1={m['f1']:.3f}  P={m['precision']:.3f}  R={m['recall']:.3f}  n={m['support']}")
    print(f"\nartifacts under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
