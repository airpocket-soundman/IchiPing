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
    from features import (
        samples_to_features,
        samples_to_noise_features,
        samples_to_noise_diff_features,
        samples_to_noise_diff_norm_features,
        samples_to_logmag_psd,
    )
except ImportError:
    from .features import (                                  # type: ignore
        samples_to_features,
        samples_to_noise_features,
        samples_to_noise_diff_features,
        samples_to_noise_diff_norm_features,
        samples_to_logmag_psd,
    )


# 特徴量抽出関数を mode 名で引けるディスパッチ表。
# - "chirp": matched filter で RIR を取り出す (chirp 励振データ用)
# - "noise": Welch スペクトルを平均し frame 内で mean-subtract (PRBS 白色雑音励振)
# - "noise_diff": Welch + s00000 ベースラインを per-bin 引く (cross-run ドリフト耐性版)
# - "noise_diff_norm": noise_diff + per-frame zero-mean unit-variance 正規化
#                      推論時の SPK 音量変動に対する不変性を強化 (vol up で SNR 稼ぐ運用)
FEATURE_FUNCS = {
    "chirp": samples_to_features,
    "noise": samples_to_noise_features,
    "noise_diff": None,       # baseline closure を __init__ で作る
    "noise_diff_norm": None,  # 同様、norm 適用は __getitem__ で
}

# noise_diff モードでベースラインを取り出す state ラベル (全閉)
BASELINE_LABEL = "s00000"


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


# 14 等価クラスの正規順 (A→B→C). NN 設計 doc の指標と一致する。
# 学習側で integer label として使うので順序固定。
CLASS_ORDER_14 = ("A1", "A2", "B1", "B2", "B3", "B4",
                  "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
CLASS_IDX_14 = {c: i for i, c in enumerate(CLASS_ORDER_14)}


def class_idx_14(bits: np.ndarray) -> int:
    """5-bit 状態を 14 等価クラスの整数インデックス (0..13) に変換。"""
    return CLASS_IDX_14[class_of(bits)]


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
        feature_mode: str = "chirp",
        feature_transform=None,
        baseline_override_dir: Optional[Path] = None,
    ) -> None:
        if feature_mode not in FEATURE_FUNCS:
            raise ValueError(f"unknown feature_mode={feature_mode!r}, "
                             f"expected one of {list(FEATURE_FUNCS)}")
        self.feature_kwargs = feature_kwargs or {}
        self.transform = transform
        # 特徴量計算後 (1024-bin log-mag 形状) に適用する augmentation。
        # FreqMask / SpectralJitter 等。学習時のみ渡す想定 (eval は None)。
        self.feature_transform = feature_transform
        self.feature_mode = feature_mode
        self.examples: List[Example] = []
        # noise_diff モード用: wav_path → 引くべきベースライン log-mag のマップ。
        # 各 captures_dir ごとに s00000 全フレーム平均を 1 本作り、その dir 配下の
        # 全 example で共有する (cross-run 推論時もその run 自身の s00000 を使う運用)。
        # baseline_override_dir が指定された場合、全 captures_dir の baseline を
        # その指定 dir の s00000 で統一する (デプロイ運用: 1 回校正の baseline を
        # 任意環境に適用するシミュレーション)。
        self._baselines: Dict[Path, np.ndarray] = {}
        self._baseline_override_dir = baseline_override_dir

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

            # noise_diff / noise_diff_norm モードでは captures_dir ごとに s00000 ベースラインを構築。
            # ただし baseline_override_dir が指定された場合、その dir の s00000 を
            # 全 captures_dir 共通の baseline として使う (デプロイ運用シミュレーション)。
            if feature_mode in ("noise_diff", "noise_diff_norm"):
                base_source = self._baseline_override_dir if self._baseline_override_dir else root
                base_dir = base_source / BASELINE_LABEL
                base_wavs = sorted(base_dir.glob("frame_*.wav")) if base_dir.exists() else []
                if not base_wavs:
                    raise RuntimeError(
                        f"{feature_mode}: {base_source} に {BASELINE_LABEL}/ が無い、または frame_*.wav 不在")
                # 各 frame の log-mag PSD を平均してこの run の baseline にする
                accum = None
                for p in base_wavs:
                    samples, _ = _load_wav_mono16(p)
                    db = samples_to_logmag_psd(samples)
                    accum = db if accum is None else accum + db
                baseline = (accum / float(len(base_wavs))).astype(np.float32)
                # この root 配下の全 example が同じ baseline を参照する
                self._baselines[root] = baseline

        # feature_mode dispatch — noise_diff / noise_diff_norm は closure で baseline を埋め込む
        if feature_mode in ("noise_diff", "noise_diff_norm"):
            self._feature_fn = None  # __getitem__ で baseline を引数に取って呼ぶ
        else:
            self._feature_fn = FEATURE_FUNCS[feature_mode]

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        ex = self.examples[idx]
        samples, rate = _load_wav_mono16(ex.wav_path)
        if self.transform is not None:
            samples, rate = self.transform(samples, rate)

        # feature_mode に応じて chirp / noise / noise_diff を呼び分け。
        # どれも FeatureConfig 既定 (16 kHz) を前提にしており、WAV から読んだ
        # rate はサニティチェック用 (現状未使用)。
        if self.feature_mode == "noise_diff":
            # ex.wav_path がどの captures_dir 配下にあるかを辿って baseline を引く
            root = self._resolve_root(ex.wav_path)
            baseline = self._baselines[root]
            feats = samples_to_noise_diff_features(samples, baseline)
        elif self.feature_mode == "noise_diff_norm":
            root = self._resolve_root(ex.wav_path)
            baseline = self._baselines[root]
            feats = samples_to_noise_diff_norm_features(samples, baseline)
        else:
            feats = self._feature_fn(samples)
        # 特徴量空間の augmentation (SpecAugment 系)。学習時のみ渡される。
        if self.feature_transform is not None:
            feats = self.feature_transform(feats)
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
            # 14 等価クラスの整数 index (0..13). train_14cls.py のターゲット。
            "cls_idx_14": torch.tensor(class_idx_14(bits), dtype=torch.long),
        }
        return item

    def _resolve_root(self, wav_path: Path) -> Path:
        """wav_path (sXXXXX/frame_*.wav) からその所属 captures_dir を逆引き。"""
        # sXXXXX フォルダの親が captures_dir。symlink でも parents をたどれば確実。
        return wav_path.parent.parent

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
