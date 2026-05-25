"""IchiPing v1 本命モデル — 14-class softmax 単ヘッド。

NN 設計 (docs/nn_design.html) の v1 正式アーキテクチャ。32-class 版
(model_32cls.py) と同一 Conv1D backbone を共有し、出力ヘッドだけ
14 等価クラス softmax に差し替えてある。

学習信号がクラス間境界に直接かかるので、観測等価クラス内のサブ状態
(原理的に判別不能) で迷う必要が無く、cross-run のクラス境界安定性が
32-class 版より高くなる想定。

State encoding (dataset.CLASS_ORDER_14 と一致):
    0: A1   1: A2
    2: B1   3: B2   4: B3   5: B4
    6: C1   7: C2   8: C3   9: C4   10: C5   11: C6   12: C7   13: C8
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


N_CLASSES = 14


# サイズ別プリセット。
# - channels: Conv1/2/3 のチャンネル数。L 以上は 2 倍幅 (3 倍ではない、計算量爆発回避)
# - extra_conv: True なら conv3 の後にもう 1 層 (Conv 64→64 等) を追加
# - head: "gap" (Global Average Pool → Linear) / "flatten" (Flatten → Linear)
# - dropout: 大きいモデルほど高めに
SIZE_PRESETS = {
    "S":  {"channels": (16, 32, 64),  "extra_conv": False, "head": "gap",     "dropout": 0.3},
    "M":  {"channels": (16, 32, 64),  "extra_conv": True,  "head": "gap",     "dropout": 0.3},
    "L":  {"channels": (32, 64, 128), "extra_conv": False, "head": "gap",     "dropout": 0.3},
    "XL": {"channels": (32, 64, 128), "extra_conv": False, "head": "flatten", "dropout": 0.4},
}


@dataclass
class IchiPingV1_14clsConfig:
    in_channels: int = 1
    n_classes:   int = N_CLASSES
    size:        str = "S"          # "S" / "M" / "L" / "XL"


class IchiPingV1_14cls(nn.Module):
    """Conv1D backbone + 14-class softmax 単ヘッド。

    size パラメータで 4 段階の capacity 切替:
      S  ~14k params  : 元設計、最小
      M  ~30k params  : Conv 層追加 (深さ +1)
      L  ~52k params  : channels 2 倍 (幅 +1)
      XL ~104k params : 幅 +1 + GAP 廃止 (Flatten head)
    """

    def __init__(self, cfg: IchiPingV1_14clsConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_14clsConfig()
        self.cfg = cfg
        preset = SIZE_PRESETS[cfg.size]
        c1, c2, c3 = preset["channels"]

        # Backbone — 共通の 3 層
        self.conv1 = nn.Conv1d(cfg.in_channels, c1, kernel_size=16, stride=4)
        self.bn1   = nn.BatchNorm1d(c1)
        self.conv2 = nn.Conv1d(c1, c2, kernel_size=8, stride=4)
        self.bn2   = nn.BatchNorm1d(c2)
        self.conv3 = nn.Conv1d(c2, c3, kernel_size=4, stride=2)
        self.bn3   = nn.BatchNorm1d(c3)

        # 追加層 (M のみ)。conv3 出力 (B, c3, 30) を更にダウンサンプル。
        if preset["extra_conv"]:
            self.conv4 = nn.Conv1d(c3, c3, kernel_size=4, stride=2)
            self.bn4   = nn.BatchNorm1d(c3)
        else:
            self.conv4 = None
            self.bn4 = None

        self.dropout = nn.Dropout(preset["dropout"])
        self.head_type = preset["head"]

        # head: GAP の場合は c3 → n_classes 線形。Flatten の場合は形状から推定。
        if self.head_type == "gap":
            self.head = nn.Linear(c3, cfg.n_classes)
        elif self.head_type == "flatten":
            # 1024 入力 → s=4 → s=4 → s=2 の縮約 = 30 (S/L 共通、M は更に /2 = 14)
            # extra_conv = False 前提なので 30 固定
            self.flatten_dim = c3 * 30
            self.head = nn.Linear(self.flatten_dim, cfg.n_classes)
        else:
            raise ValueError(f"unknown head_type={self.head_type!r}")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        if self.conv4 is not None:
            h = F.relu(self.bn4(self.conv4(h)))
        if self.head_type == "gap":
            h = h.mean(dim=-1)
        else:  # flatten
            h = h.flatten(start_dim=1)
        h = self.dropout(h)
        return self.head(h)
