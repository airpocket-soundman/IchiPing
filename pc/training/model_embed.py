"""IchiPing v1 — embedding + プロトタイプマッチング構成。

共有 backbone (Conv1D ×3 + GAP) で 64-d embedding を作り、L2 正規化して
ユニット球面上に置く。学習は supervised contrastive loss (同 state 同士は
近く、別 state 同士は遠く)。推論は学習データから各 state のプロトタイプ
(=同 state の embedding 平均) を取り、新サンプル → 最近傍プロトタイプ。

利点:
- 32 state 全部をプロトタイプ化することで「サブ状態識別 (SNR 16+)」を直接活用
- k-NN 系なので small data でも安定動作する傾向
- プロトタイプは flash に 32 × 64 = 2048 float = 8 KB (INT8 で 2 KB)

注意:
- 推論側で 32 プロトタイプとの内積計算 (deploy 時の計算量微増)
- contrastive loss の margin / temperature tuning 必要
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class IchiPingV1_embedConfig:
    in_channels: int = 1
    embed_dim:   int = 64


class IchiPingV1_embed(nn.Module):
    """共有 Conv1D backbone → 64-d embedding (L2 正規化済み)。"""

    def __init__(self, cfg: IchiPingV1_embedConfig | None = None) -> None:
        super().__init__()
        if cfg is None:
            cfg = IchiPingV1_embedConfig()
        self.cfg = cfg

        self.conv1 = nn.Conv1d(cfg.in_channels, 16, kernel_size=16, stride=4)
        self.bn1   = nn.BatchNorm1d(16)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=8, stride=4)
        self.bn2   = nn.BatchNorm1d(32)
        self.conv3 = nn.Conv1d(32, 64, kernel_size=4, stride=2)
        self.bn3   = nn.BatchNorm1d(64)
        self.dropout = nn.Dropout(0.3)
        # embedding head: 64 → embed_dim (linear, L2 正規化は forward で)
        self.head = nn.Linear(cfg.embed_dim, cfg.embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = F.relu(self.bn1(self.conv1(x)))
        h = F.relu(self.bn2(self.conv2(h)))
        h = F.relu(self.bn3(self.conv3(h)))
        h = h.mean(dim=-1)
        h = self.dropout(h)
        z = self.head(h)
        # L2 normalize → 単位球面上に射影
        return F.normalize(z, p=2, dim=-1)


def supervised_contrastive_loss(z: torch.Tensor, labels: torch.Tensor,
                                temperature: float = 0.1) -> torch.Tensor:
    """SupCon (Khosla et al. 2020) 簡易版。

    z: (B, D) L2-normalized embeddings
    labels: (B,) state index (32 値想定)
    """
    B = z.size(0)
    # 類似度行列 (cosine、z は正規化済みなので内積でよい)
    sim = torch.mm(z, z.t()) / temperature              # (B, B)
    # 数値安定化: 各行の max を引く
    sim = sim - sim.max(dim=-1, keepdim=True).values.detach()

    # 同 label mask (自分自身は除外)
    labels = labels.view(-1, 1)
    pos_mask = (labels == labels.t()).float()
    self_mask = torch.eye(B, device=z.device)
    pos_mask = pos_mask - self_mask                      # 自分を除く
    pos_mask = torch.clamp(pos_mask, min=0.0)

    exp_sim = torch.exp(sim) * (1 - self_mask)           # 自分除外
    log_prob = sim - torch.log(exp_sim.sum(dim=-1, keepdim=True) + 1e-12)

    # 各サンプルの正例平均 (正例が無い行はスキップ)
    n_pos = pos_mask.sum(dim=-1)
    mean_log_prob_pos = (pos_mask * log_prob).sum(dim=-1) / (n_pos + 1e-12)
    # 正例が無い行は損失寄与ゼロにする
    valid = (n_pos > 0).float()
    loss = -(mean_log_prob_pos * valid).sum() / (valid.sum() + 1e-12)
    return loss


@torch.no_grad()
def compute_prototypes(model: IchiPingV1_embed,
                       loader,
                       device: str,
                       label_key: str = "state_idx",
                       n_classes: int = 32) -> torch.Tensor:
    """全サンプルの embedding を平均して class ごとプロトタイプを作る。

    返り値: (n_classes, D) tensor。各行は L2 正規化済み。
    """
    model.eval()
    accum = torch.zeros(n_classes, model.cfg.embed_dim, device=device)
    counts = torch.zeros(n_classes, device=device)
    for batch in loader:
        x = batch["x"].to(device)
        y = batch[label_key].to(device)
        z = model(x)
        for i in range(y.size(0)):
            accum[y[i]] += z[i]
            counts[y[i]] += 1
    valid = counts > 0
    accum[valid] = accum[valid] / counts[valid].unsqueeze(-1)
    accum[valid] = F.normalize(accum[valid], p=2, dim=-1)
    return accum


@torch.no_grad()
def predict_with_prototypes(model: IchiPingV1_embed,
                            x: torch.Tensor,
                            prototypes: torch.Tensor) -> torch.Tensor:
    """新サンプル x の embedding → 最近傍プロトタイプ → class index。
    cosine 類似度なので argmax (内積) でよい。
    """
    z = model(x)                          # (B, D) normalized
    sim = torch.mm(z, prototypes.t())    # (B, n_classes)
    return sim.argmax(dim=-1)
