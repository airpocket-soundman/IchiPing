"""IchiPing v1 (DigiKey) NN — multi-task 1D-CNN.

Matches the architecture in docs/spec.html §4.3:

    input : 1D log-magnitude spectrum, 1024 bins (single-sided FFT of one
            chirp response, 16 kHz × 0.128 s = 2048-pt FFT → 1024 bins).

    backbone :  Conv1D 16ch k=16 s=4 + ReLU
                Conv1D 32ch k=8  s=4 + ReLU
                Conv1D 64ch k=4  s=2 + ReLU
                Global Average Pool        -> 64-d shared embedding

    heads (multi-task, sharing the backbone):
        - any_open   : binary       (sigmoid)
        - door_AB    : continuous   (linear, 0..1 normalised aperture)
        - door_BC    : 3-class      (softmax over closed / half / full)
        - window_a   : continuous
        - window_b   : 3-class
        - window_c   : 3-class

Target parameter count is ~30 K. After INT8 quantisation the on-device
memory budget is ~30 KB.

This module is deliberately PyTorch-only (no eIQ-specific layers) so that
the exported ONNX is portable; the eIQ Toolkit conversion happens later
in docs/mcu_deployment.html.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import torch
import torch.nn as nn


@dataclass(frozen=True)
class ModelConfig:
    input_bins: int = 1024
    backbone_widths: tuple = (16, 32, 64)
    backbone_kernels: tuple = (16, 8, 4)
    backbone_strides: tuple = (4, 4, 2)
    door_bc_classes: int = 3
    window_b_classes: int = 3
    window_c_classes: int = 3


class Backbone(nn.Module):
    """Three Conv1D blocks + global average pool → embedding."""

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        layers = []
        in_ch = 1
        for w, k, s in zip(cfg.backbone_widths, cfg.backbone_kernels, cfg.backbone_strides):
            layers.append(nn.Conv1d(in_ch, w, kernel_size=k, stride=s, padding=k // 2))
            layers.append(nn.BatchNorm1d(w))
            layers.append(nn.ReLU(inplace=True))
            in_ch = w
        self.net = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.embedding_dim = in_ch

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x : (B, 1024) → (B, 1, 1024) → (B, 64, 1) → (B, 64)
        if x.ndim == 2:
            x = x.unsqueeze(1)
        h = self.net(x)
        h = self.pool(h).squeeze(-1)
        return h


class IchiPingV1(nn.Module):
    """Shared backbone + 6 task heads."""

    OUTPUT_KEYS = (
        "any_open", "door_AB", "door_BC", "window_a", "window_b", "window_c",
    )

    def __init__(self, cfg: ModelConfig = ModelConfig()):
        super().__init__()
        self.cfg = cfg
        self.backbone = Backbone(cfg)
        d = self.backbone.embedding_dim
        self.head_any_open = nn.Linear(d, 1)
        self.head_door_AB = nn.Linear(d, 1)
        self.head_door_BC = nn.Linear(d, cfg.door_bc_classes)
        self.head_window_a = nn.Linear(d, 1)
        self.head_window_b = nn.Linear(d, cfg.window_b_classes)
        self.head_window_c = nn.Linear(d, cfg.window_c_classes)

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            "any_open": self.head_any_open(h).squeeze(-1),
            "door_AB":  self.head_door_AB(h).squeeze(-1),
            "door_BC":  self.head_door_BC(h),
            "window_a": self.head_window_a(h).squeeze(-1),
            "window_b": self.head_window_b(h),
            "window_c": self.head_window_c(h),
        }

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def build_default_model() -> IchiPingV1:
    return IchiPingV1()


if __name__ == "__main__":
    m = build_default_model()
    print(f"IchiPing v1: {m.num_parameters():,} parameters")
    print(f"Estimated INT8 footprint: ~{m.num_parameters() / 1024:.1f} KB (weights only)")
    dummy = torch.randn(4, 1024)
    out = m(dummy)
    for k, v in out.items():
        print(f"  {k:>10}: {tuple(v.shape)}")
