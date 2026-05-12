"""Dataset for IchiPing v1 training.

Reads the directory layout that pc/receiver.py writes:

    captures/
        labels.csv             # columns: seq, ts_ms, rate_hz, n_samples,
                               #          servo_a, servo_b, servo_c,
                               #          servo_AB, servo_BC, wav, crc_ok
        frame_000000.wav
        frame_000001.wav
        ...

Each row of labels.csv becomes one example. Servo angles are continuous
(0-90°) — for the v1 NN we expose them as:

    any_open : 1 if any servo > OPEN_THRESHOLD, else 0
    door_AB  : servo_AB / 90.0                          (continuous)
    door_BC  : bucketise(servo_BC) into {closed, half, full}
    window_a : servo_a / 90.0
    window_b : bucketise(servo_b)
    window_c : bucketise(servo_c)

This mirrors the 6 output heads in model.IchiPingV1. The bucketisation
threshold is a knob — tune it per the data distribution.
"""
from __future__ import annotations

import csv
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

from features import samples_to_features


OPEN_THRESHOLD = 10.0   # degrees; below this we treat the servo as "closed"
BUCKET_HALF = 30.0      # 0..30 = closed, 30..60 = half, 60..90 = full


def bucketise(angle: float) -> int:
    if angle < OPEN_THRESHOLD:
        return 0
    if angle < 60.0:
        return 1
    return 2


def _load_wav_mono16(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, f"{path} is not mono"
        assert wf.getsampwidth() == 2, f"{path} is not 16-bit PCM"
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


@dataclass
class FrameRow:
    seq: int
    wav_path: Path
    servo_a: float
    servo_b: float
    servo_c: float
    servo_AB: float
    servo_BC: float


def load_index(capture_dir: Path, drop_bad_crc: bool = True) -> List[FrameRow]:
    rows: List[FrameRow] = []
    csv_path = capture_dir / "labels.csv"
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if drop_bad_crc and int(row.get("crc_ok", "1")) == 0:
                continue
            rows.append(FrameRow(
                seq=int(row["seq"]),
                wav_path=capture_dir / row["wav"],
                servo_a=float(row["servo_a"]),
                servo_b=float(row["servo_b"]),
                servo_c=float(row["servo_c"]),
                servo_AB=float(row["servo_AB"]),
                servo_BC=float(row["servo_BC"]),
            ))
    return rows


class IchiPingDataset(Dataset):
    """Lazy-load WAV → features. For large datasets, swap to a cached
    feature ndarray on disk; for the v1 pilot (~1000 samples) this is fine."""

    def __init__(self, capture_dir: Path, drop_bad_crc: bool = True):
        self.rows = load_index(Path(capture_dir), drop_bad_crc=drop_bad_crc)
        if not self.rows:
            raise ValueError(f"no rows found in {capture_dir}/labels.csv")

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        row = self.rows[idx]
        samples, _ = _load_wav_mono16(row.wav_path)
        feat = samples_to_features(samples)
        x = torch.from_numpy(feat).float()

        any_open = float(
            row.servo_a > OPEN_THRESHOLD or row.servo_b > OPEN_THRESHOLD
            or row.servo_c > OPEN_THRESHOLD or row.servo_AB > OPEN_THRESHOLD
            or row.servo_BC > OPEN_THRESHOLD
        )
        y = {
            "any_open": torch.tensor(any_open, dtype=torch.float32),
            "door_AB":  torch.tensor(row.servo_AB / 90.0, dtype=torch.float32),
            "door_BC":  torch.tensor(bucketise(row.servo_BC), dtype=torch.long),
            "window_a": torch.tensor(row.servo_a / 90.0, dtype=torch.float32),
            "window_b": torch.tensor(bucketise(row.servo_b), dtype=torch.long),
            "window_c": torch.tensor(bucketise(row.servo_c), dtype=torch.long),
        }
        return x, y


def collate_targets(batch):
    """Stack samples + dict-of-tensors into batched dict."""
    xs = torch.stack([b[0] for b in batch])
    keys = batch[0][1].keys()
    ys = {k: torch.stack([b[1][k] for b in batch]) for k in keys}
    return xs, ys
