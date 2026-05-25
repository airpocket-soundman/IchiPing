"""IchiPing v1 — 5-bit binary head 構成 (旧 v0 + noise_diff 路線)。

物理観測量 (5 つの開閉 bit: a, b, c, AB, BC) と head を 1:1 対応させた構成。
共有 backbone (model_14cls / model_32cls と同一) の上に 5 つの独立 binary
classifier を載せ、それぞれ「その bit が 1 か 0 か」を sigmoid で予測する。

利点:
- 各 head は ~50/50 で balanced (14cls / 32cls の class imbalance を回避)
- 5 binary task は 32-way categorical より勾配信号がシャープ
- 物理量との対応が明示的で interpretability 高い

inference:
    out = model(x)        # dict[bit_name -> (B, 1) logits]
    pred_bits = torch.cat([(out[k].squeeze(-1) > 0).long() for k in BIT_ORDER], dim=-1)
    # pred_bits は (B, 5) の 0/1 tensor、a b c AB BC 順 (state5 と同じ)
    # ここから 14cls / 32cls collapse 可能 (infer_bits.py 参照)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import torch
import torch.nn as nn
import torch.nn.functional as F


# state5 と同じ bit 順 (dataset.parse_state_label, gen32.py と一致)
BIT_ORDER = ("a", "b", "c", "AB", "BC")
N_BITS = len(BIT_ORDER)


@dataclass
class IchiPingV1_bitsConfig:
    in_channels: int = 1
    embed_dim:   int = 64


class IchiPingV1_bits(nn.Module):
    """共有 Conv1D backbone + 5 binary head。

    backbone は IchiPingV1_14cls / 32cls と完全同一。head だけ
    Linear(64, 1) × 5 に差し替えてある。
    出力は dict[bit_name -> (B, 1) logit]、loss 側で BCEWithLogitsLoss を使う。
    """

    def __init__(self, cfg: IchiPingV1_bitsConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_bitsConfig()
        self.cfg = cfg

        # Backbone (14cls / 32cls と同一)
        self.conv1 = nn.Conv1d(cfg.in_channels, 16, kernel_size=16, stride=4)
        self.bn1   = nn.BatchNorm1d(16)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=8, stride=4)
        self.bn2   = nn.BatchNorm1d(32)
        self.conv3 = nn.Conv1d(32, 64, kernel_size=4, stride=2)
        self.bn3   = nn.BatchNorm1d(64)

        self.dropout = nn.Dropout(0.3)
        # 5 つの独立な binary head。出力 1 次元 = bit logit。
        self.heads = nn.ModuleDict({
            name: nn.Linear(cfg.embed_dim, 1) for name in BIT_ORDER
        })

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        """returns {bit_name: (B, 1) logits}.

        loss 計算は呼び出し側で BCEWithLogitsLoss(out[name].squeeze(-1), target_bit)。
        """
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = h.mean(dim=-1)
        h = self.dropout(h)
        return {name: head(h) for name, head in self.heads.items()}

    def predict_bits(self, x: torch.Tensor) -> torch.Tensor:
        """logits → (B, 5) long tensor (a b c AB BC 順、0/1)。"""
        out = self.forward(x)
        cols = [(out[name].squeeze(-1) > 0).long() for name in BIT_ORDER]
        return torch.stack(cols, dim=-1)


def bits_to_idx32(bits) -> int:
    """5-bit tensor / list → 32-class index (0..31). gen32.py エンコーディングと一致。"""
    return int(bits[0] + bits[1] * 2 + bits[2] * 4 + bits[3] * 8 + bits[4] * 16)
