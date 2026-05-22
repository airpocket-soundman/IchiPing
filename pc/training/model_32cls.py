"""IchiPing — 32-class softmax variant (experimental).

Same Conv1D backbone as ``model.IchiPingV1`` but the output is a single
32-class softmax over the full 5-bit state space. The 14-class
observability bound (see docs/full32_initial_test.html §2) says ~18 of
the 32 true states are *theoretically* indistinguishable when the
intervening door is closed, so we can't beat 14 effective classes — but
small mechanical bias (servo position jitter, panel resonance differences)
may give the model SOMETHING to latch onto within an equivalence class.

This variant exists to **test that hypothesis empirically**. If the
32-class accuracy beats "predict the equivalence class then collapse to
its centroid" by more than the equivalence-class limit allows, we have
evidence that the model exploits sub-class structure.

Architecture
------------
    input  : (B, 1, 1024) log-magnitude spectrum (same as IchiPingV1)
    backbone : Conv1D 16ch → Conv1D 32ch → Conv1D 64ch → GAP → 64-d embedding
    head   : Linear(64 → 32) → cross-entropy loss over 32-class softmax

State encoding (matches dataset.parse_state_label / class_of):
    state_idx = bit_a + bit_b*2 + bit_c*4 + bit_AB*8 + bit_BC*16
where bits are 0=closed, 1=open. So:
    s00000 → idx 0
    s10000 → idx 1
    s00001 → idx 16
    s11111 → idx 31
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


N_CLASSES = 32


@dataclass
class IchiPingV1_32clsConfig:
    in_channels: int = 1
    embed_dim:   int = 64
    n_classes:   int = N_CLASSES


class IchiPingV1_32cls(nn.Module):
    """Conv1D backbone + single 32-class softmax head."""

    def __init__(self, cfg: IchiPingV1_32clsConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_32clsConfig()
        self.cfg = cfg

        # Backbone (same shape budget as IchiPingV1; ~13K params here too)
        self.conv1 = nn.Conv1d(cfg.in_channels, 16, kernel_size=16, stride=4)
        self.bn1   = nn.BatchNorm1d(16)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=8, stride=4)
        self.bn2   = nn.BatchNorm1d(32)
        self.conv3 = nn.Conv1d(32, 64, kernel_size=4, stride=2)
        self.bn3   = nn.BatchNorm1d(64)

        # Classification head
        self.dropout = nn.Dropout(0.3)
        self.head = nn.Linear(cfg.embed_dim, cfg.n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns logits of shape (batch, 32). Use F.cross_entropy for loss."""
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = h.mean(dim=-1)                  # global average pool over time
        h = self.dropout(h)
        logits = self.head(h)
        return logits

    def predict_bits(self, logits: torch.Tensor) -> torch.Tensor:
        """Convert 32-class logits back to 5-bit state arrays.
        Returns (batch, 5) long tensor matching the dataset convention."""
        idx = logits.argmax(dim=-1)                              # (B,)
        bits = torch.zeros(idx.shape[0], 5, dtype=torch.long,
                            device=idx.device)
        for k in range(5):
            bits[:, k] = (idx >> k) & 1
        return bits


def bits_to_idx(bits) -> int:
    """5-bit array → 0..31 index. Matches dataset / firmware encoding."""
    return int(bits[0] + bits[1] * 2 + bits[2] * 4 + bits[3] * 8 + bits[4] * 16)


def idx_to_bits(idx: int):
    """Inverse of bits_to_idx — returns a list of 5 ints."""
    return [(idx >> k) & 1 for k in range(5)]
