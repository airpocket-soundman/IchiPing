"""IchiPing v1 — 階層 cascade head 構成。

Level 1: 3-way (A / B / C) — 大グループ判定 (扉 AB, BC の状態で決まる)
Level 2 within group:
  A グループ (AB=0): 2-way for a-bit
  B グループ (AB=1, BC=0): 4-way for (a, b) → B1..B4
  C グループ (AB=1, BC=1): 8-way for (a, b, c) → C1..C8

Group encoding (forward 出力と一致):
  0: A   1: B   2: C

Within-group encoding:
  A: a-bit (0..1)
  B: a + 2*b (0..3) → B1=0, B2=1, B3=2, B4=3
  C: a + 2*b + 4*c (0..7) → C1=0, C2=1, ..., C8=7

学習時は batch 内サンプルを group ごとに mask して対応 L2 head のみ loss を取る。
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


# 内部 group 順 (CLASS_ORDER_14 の A→B→C と一致)
GROUP_ORDER = ("A", "B", "C")
N_GROUPS = 3


def bits_to_group(a, b, c, AB, BC) -> int:
    """5-bit → group index (0=A, 1=B, 2=C)。"""
    if AB == 0:
        return 0
    if BC == 0:
        return 1
    return 2


def bits_to_within_group(a, b, c, AB, BC) -> int:
    """5-bit → within-group index (A:0..1, B:0..3, C:0..7)。"""
    if AB == 0:
        return a
    if BC == 0:
        return a + 2 * b
    return a + 2 * b + 4 * c


def group_within_to_14cls_idx(group: int, within: int) -> int:
    """(group, within) → CLASS_ORDER_14 index (0..13)。

    CLASS_ORDER_14 順: A1, A2, B1..B4, C1..C8
    A: group=0, within=0..1     → CLASS_ORDER_14 [0..1]
    B: group=1, within=0..3     → CLASS_ORDER_14 [2..5]
    C: group=2, within=0..7     → CLASS_ORDER_14 [6..13]
    """
    if group == 0:
        return within          # A1, A2
    if group == 1:
        return 2 + within      # B1..B4
    return 6 + within          # C1..C8


@dataclass
class IchiPingV1_cascadeConfig:
    in_channels: int = 1
    embed_dim:   int = 64


class IchiPingV1_cascade(nn.Module):
    """共有 Conv1D backbone + L1 (3-way) + 3 個の L2 head。"""

    def __init__(self, cfg: IchiPingV1_cascadeConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_cascadeConfig()
        self.cfg = cfg

        self.conv1 = nn.Conv1d(cfg.in_channels, 16, kernel_size=16, stride=4)
        self.bn1   = nn.BatchNorm1d(16)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=8, stride=4)
        self.bn2   = nn.BatchNorm1d(32)
        self.conv3 = nn.Conv1d(32, 64, kernel_size=4, stride=2)
        self.bn3   = nn.BatchNorm1d(64)
        self.dropout = nn.Dropout(0.3)

        # Level 1: A/B/C 大分類
        self.head_L1 = nn.Linear(cfg.embed_dim, N_GROUPS)
        # Level 2: 各 group 内の細分類
        self.head_A = nn.Linear(cfg.embed_dim, 2)   # a-bit
        self.head_B = nn.Linear(cfg.embed_dim, 4)   # a + 2b
        self.head_C = nn.Linear(cfg.embed_dim, 8)   # a + 2b + 4c

    def _embed(self, x: torch.Tensor) -> torch.Tensor:
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = h.mean(dim=-1)
        return self.dropout(h)

    def forward(self, x: torch.Tensor) -> dict:
        """returns {"L1": (B, 3), "L2_A": (B, 2), "L2_B": (B, 4), "L2_C": (B, 8)}。"""
        h = self._embed(x)
        return {
            "L1": self.head_L1(h),
            "L2_A": self.head_A(h),
            "L2_B": self.head_B(h),
            "L2_C": self.head_C(h),
        }

    def predict_14cls(self, x: torch.Tensor) -> torch.Tensor:
        """L1 で group 予測 → 該当 L2 head で within-group 予測 → 14cls 合成。"""
        out = self.forward(x)
        group = out["L1"].argmax(dim=-1)         # (B,)
        # 3 つの L2 予測を取り全て計算してから group で選択
        within_A = out["L2_A"].argmax(dim=-1)    # (B,)
        within_B = out["L2_B"].argmax(dim=-1)
        within_C = out["L2_C"].argmax(dim=-1)
        # 14cls index: A:within, B:2+within, C:6+within
        pred14 = torch.where(group == 0, within_A,
                  torch.where(group == 1, 2 + within_B, 6 + within_C))
        return pred14
