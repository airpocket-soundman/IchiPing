"""Dataset for IchiPing v1 training — new captures layout.

Expected directory structure (created by ``collector_client.py --plan
plans/full_32_v2.yaml --run-id full_32_v2`` and similar):

    captures/<run_id>/
        s00000/                       # state label (sABCDE, A=window-a … E=door-BC)
            frame_000000.wav          # int16 mono 16 kHz
            frame_000001.wav
            ...
            labels.csv                # one row per frame, includes actual servo angles
            meta.json                 # pattern + calibration metadata
        s10000/
        ...
        s11111/

Label encoding
--------------
The 5-bit label "sABCDE" maps to:
    A (window a)  — index 0
    B (window b)  — index 1
    C (window c)  — index 2
    D (door  AB)  — index 3
    E (door  BC)  — index 4
'1' = OPEN, '0' = CLOSED.

For multi-task supervision we expose:
    any_open   : 1 if any of the 5 bits == 1, else 0          (binary)
    door_AB    : float in {0.0, 1.0}                          (continuous-ish)
    door_BC    : int   in {0, 1}    (CLOSED / OPEN)           (class)
    window_a   : float in {0.0, 1.0}
    window_b   : int   in {0, 1}
    window_c   : int   in {0, 1}

The "continuous" heads (door_AB / window_a) accept fractional servo
angles when the collector emits intermediate positions, so the same model
can be fine-tuned on a future dataset where angles are not pure 0/1.
"""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

try:
    from features import samples_to_features
except ImportError:
    from .features import samples_to_features  # type: ignore


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------

LABEL_NAMES = ("window_a", "window_b", "window_c", "door_AB", "door_BC")


def parse_state_label(name: str) -> Optional[np.ndarray]:
    """Parse a directory name like 's10100' into a 5-int array.

    Returns None if the name does not match the sABCDE pattern.
    """
    if not name.startswith("s") or len(name) != 6:
        return None
    try:
        bits = [int(c) for c in name[1:]]
    except ValueError:
        return None
    if any(b not in (0, 1) for b in bits):
        return None
    return np.array(bits, dtype=np.int64)


def class_of(bits: np.ndarray) -> str:
    """Equivalence-class tag for a 5-bit state (see full32_initial_test §2)."""
    a, b, c, AB, BC = (int(x) for x in bits)
    if AB == 0:
        return "A1" if a == 0 else "A2"
    if BC == 0:
        return {(0, 0): "B1", (1, 0): "B2",
                (0, 1): "B3", (1, 1): "B4"}[(a, b)]
    return "C" + str(1 + a + 2 * b + 4 * c)


# ---------------------------------------------------------------------------
# WAV loading
# ---------------------------------------------------------------------------

def _load_wav_mono16(path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"{path} is not mono int16")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

@dataclass
class Example:
    wav_path: Path
    state: np.ndarray            # 5-int array
    cls: str                     # equivalence class tag
    sample_rate: int


class IchiPingDataset(Dataset):
    """Reads every <run_id>/sXXXXX/frame_*.wav.

    Parameters
    ----------
    captures_dirs : list[Path]
        One or more captures/<run_id> roots. All sXXXXX subfolders are scanned.
    feature_kwargs : dict
        Passed to ``samples_to_features``.
    transform : callable | None
        Optional per-example augmentation. Receives (samples, sample_rate)
        as numpy, must return (samples, sample_rate). See augment.py.
    """

    def __init__(
        self,
        captures_dirs: List[Path],
        feature_kwargs: Optional[Dict] = None,
        transform=None,
        require_crc: bool = True,
    ) -> None:
        self.feature_kwargs = feature_kwargs or {}
        self.transform = transform
        self.examples: List[Example] = []

        for root in captures_dirs:
            for state_dir in sorted(root.iterdir()):
                if not state_dir.is_dir():
                    continue
                bits = parse_state_label(state_dir.name)
                if bits is None:
                    continue
                cls = class_of(bits)
                for wav_path in sorted(state_dir.glob("frame_*.wav")):
                    self.examples.append(
                        Example(wav_path=wav_path, state=bits, cls=cls, sample_rate=0)
                    )

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        ex = self.examples[idx]
        samples, rate = _load_wav_mono16(ex.wav_path)
        if self.transform is not None:
            samples, rate = self.transform(samples, rate)

        # samples_to_features expects a FeatureConfig (rate is baked in to the
        # FeatureConfig defaults: 16 kHz, matching firmware). The `rate` we read
        # from the WAV is used for sanity-check only.
        feats = samples_to_features(samples)
        feats_t = torch.from_numpy(feats).float().unsqueeze(0)   # add channel dim → (1, 1024)

        bits = ex.state
        # 32-class index for the single-head IchiPingV1_32cls variant.
        # Encoding must stay in sync with model_32cls.bits_to_idx.
        state_idx = int(bits[0] + bits[1] * 2 + bits[2] * 4
                        + bits[3] * 8 + bits[4] * 16)
        item: Dict[str, torch.Tensor] = {
            "x":         feats_t,
            "any_open":  torch.tensor(float(bits.sum() > 0), dtype=torch.float32),
            "window_a":  torch.tensor(float(bits[0]), dtype=torch.float32),
            "window_b":  torch.tensor(int(bits[1]),   dtype=torch.long),
            "window_c":  torch.tensor(int(bits[2]),   dtype=torch.long),
            "door_AB":   torch.tensor(float(bits[3]), dtype=torch.float32),
            "door_BC":   torch.tensor(int(bits[4]),   dtype=torch.long),
            "state5":    torch.from_numpy(bits.copy()),
            "state_idx": torch.tensor(state_idx,      dtype=torch.long),
        }
        return item

    # ---- utilities ----

    def class_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for ex in self.examples:
            counts[ex.cls] = counts.get(ex.cls, 0) + 1
        return counts

    def state_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for ex in self.examples:
            key = "s" + "".join(str(b) for b in ex.state)
            counts[key] = counts.get(key, 0) + 1
        return counts


def split_indices(n: int, train: float = 0.7, val: float = 0.15,
                  seed: int = 0) -> Tuple[List[int], List[int], List[int]]:
    """Random 70/15/15 split of indices 0..n-1."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_train = int(n * train)
    n_val = int(n * val)
    return (idx[:n_train].tolist(),
            idx[n_train:n_train + n_val].tolist(),
            idx[n_train + n_val:].tolist())
