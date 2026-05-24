"""IchiPing 32cls — Neutron NPU 向けに op 構造を書き換えた変種。

設計方針 (MCXN947 Neutron NPU で NPU 比率を最大化することが目的):

  - Conv1D → Conv2D (kernel=(1, K)) に統一
    Neutron は Conv2D を主役オペレータとして扱う。Conv1D は TFLite で
    必ず Reshape を挟むため避ける。
  - 入力 (B, 1, 1024) → (B, 1, 1, 1024) の unsqueeze は forward 冒頭で
    1 回だけ実施 (グラフ内 Reshape を最小化)
  - BatchNorm は学習中だけ使い、export 前に Conv に fold して消す
    (`fold_bn_inplace()` → torch.nn.utils.fusion.fuse_conv_bn_eval)
  - 旧モデルの GAP (Mean op = NPU 非対応) / Flatten + Linear (これも NPU 不利)
    を AveragePool2D + 1×1 Conv2D に置換。最終 squeeze だけが Reshape として残る。
  - AveragePool2D の kernel は (1, 7) 以下に収まるよう、最終 conv の stride を
    調整して空間次元を 7 まで落としてから pool する。

アーキテクチャ (XL: channels = (32, 64, 128)):
    input  : (B, 1, 1024)
    unsq   : (B, 1, 1, 1024)
    conv1  : Conv2D(1, 32, (1,16), s=(1,4))   → (B, 32, 1, 253)
    conv2  : Conv2D(32, 64, (1,8),  s=(1,4))  → (B, 64, 1, 62)
    conv3  : Conv2D(64, 128, (1,4), s=(1,4))  → (B, 128, 1, 15)
    conv4  : Conv2D(128, 128, (1,3), s=(1,2)) → (B, 128, 1, 7)
    avgpool: AvgPool2D((1, 7))                → (B, 128, 1, 1)
    head   : Conv2D(128, 32, (1, 1))          → (B, 32, 1, 1)
    out    : squeeze(-1).squeeze(-1)          → (B, 32)
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


N_CLASSES = 32


# サイズ別プリセット。chunk reduction stride を 4,4,4,2 で固定し空間次元 7 に揃える。
SIZE_PRESETS = {
    "S":  {"channels": (16, 32, 64),  "dropout": 0.3},
    "M":  {"channels": (16, 32, 64),  "dropout": 0.3},   # S と同形状 (拡張余地)
    "L":  {"channels": (32, 64, 128), "dropout": 0.3},
    "XL": {"channels": (32, 64, 128), "dropout": 0.4},
}


@dataclass
class IchiPingV1_32clsNeutronConfig:
    in_channels: int = 1
    n_classes:   int = N_CLASSES
    size:        str = "XL"


class IchiPingV1_32clsNeutron(nn.Module):
    """Conv2D ベース + AvgPool2D + 1×1 Conv2D head の Neutron 互換 32cls モデル。

    旧 IchiPingV1_32cls との API 互換:
      - input: (B, 1, 1024) float32 log-mag spectrum
      - output: (B, 32) logits
    """

    def __init__(self, cfg: IchiPingV1_32clsNeutronConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_32clsNeutronConfig()
        self.cfg = cfg
        preset = SIZE_PRESETS[cfg.size]
        c1, c2, c3 = preset["channels"]

        # backbone: Conv2D with kernel (1, K), all NPU-friendly
        self.conv1 = nn.Conv2d(cfg.in_channels, c1, kernel_size=(1, 16), stride=(1, 4))
        self.bn1   = nn.BatchNorm2d(c1)
        self.conv2 = nn.Conv2d(c1, c2, kernel_size=(1, 8), stride=(1, 4))
        self.bn2   = nn.BatchNorm2d(c2)
        self.conv3 = nn.Conv2d(c2, c3, kernel_size=(1, 4), stride=(1, 4))
        self.bn3   = nn.BatchNorm2d(c3)
        self.conv4 = nn.Conv2d(c3, c3, kernel_size=(1, 3), stride=(1, 2))
        self.bn4   = nn.BatchNorm2d(c3)

        # spatial reduction → 1 via AvgPool2D (NPU op)
        # 1024 → 253 → 62 → 15 → 7 → 1
        self.avgpool = nn.AvgPool2d(kernel_size=(1, 7))

        self.dropout = nn.Dropout(preset["dropout"])

        # classifier: 1×1 Conv2D で Flatten+Linear を置換 (NPU 対応 op)
        self.classifier = nn.Conv2d(c3, cfg.n_classes, kernel_size=(1, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # (B, 1, 1024) → (B, 1, 1, 1024)
        if x.dim() == 3:
            x = x.unsqueeze(2)
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = F.relu(self.bn4(self.conv4(h)))
        h = self.avgpool(h)             # (B, c3, 1, 1)
        h = self.dropout(h)
        h = self.classifier(h)          # (B, 32, 1, 1)
        return h.squeeze(-1).squeeze(-1)  # (B, 32)

    def fold_bn_inplace(self) -> None:
        """学習後に BN を直前の Conv に fuse して消す (export 専用)。

        torch.nn.utils.fusion.fuse_conv_bn_eval は新しい Conv を返すので、
        子モジュールを差し替えて BN を Identity 化する。eval mode 必須。
        """
        self.eval()
        from torch.nn.utils.fusion import fuse_conv_bn_eval
        pairs = [("conv1", "bn1"), ("conv2", "bn2"),
                 ("conv3", "bn3"), ("conv4", "bn4")]
        for cname, bname in pairs:
            conv = getattr(self, cname)
            bn = getattr(self, bname)
            fused = fuse_conv_bn_eval(conv, bn)
            setattr(self, cname, fused)
            setattr(self, bname, nn.Identity())

    def predict_bits(self, logits: torch.Tensor) -> torch.Tensor:
        idx = logits.argmax(dim=-1)
        bits = torch.zeros(idx.shape[0], 5, dtype=torch.long, device=idx.device)
        for k in range(5):
            bits[:, k] = (idx >> k) & 1
        return bits


def bits_to_idx(bits) -> int:
    return int(bits[0] + bits[1] * 2 + bits[2] * 4 + bits[3] * 8 + bits[4] * 16)


def idx_to_bits(idx: int):
    return [(idx >> k) & 1 for k in range(5)]
