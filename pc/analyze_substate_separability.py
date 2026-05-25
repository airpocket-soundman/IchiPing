"""等価クラス内サブ状態の分離可能性を実データで定量化する。

理論上は「閉扉の向こうの状態は観測不能」だが、実際には扉は完全遮音ではなく
減衰だけなので、サブ状態間には 0 でない差分が残る。それを実測で測る。

各等価クラス (A1, A2, B1-B4, C1-C8) について:
  1. 各サブ状態の平均スペクトルを 30 frame で取る
  2. クラス内の全ペア (i, j) について平均スペクトル差を計算
  3. その差を per-state intra-frame σ (約 0.45 dB) と比較
  4. 「差 / σ_noise」比が大きいほど統計的に分離可能

出力:
  サブ状態ペアごとの 300-6 kHz 帯 RMS 差 (dB) と SNR (= 差 / 観測ノイズ床)
"""
from __future__ import annotations

from pathlib import Path
import csv
import numpy as np
import wave

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from training.dataset import class_of, parse_state_label  # noqa: E402
from training.features import samples_to_logmag_psd      # noqa: E402


ROOT = Path(__file__).resolve().parent / "captures" / "full_32_train_v1"


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def in_band_rms(diff: np.ndarray, freqs: np.ndarray,
                lo: float = 300.0, hi: float = 6000.0) -> float:
    mask = (freqs >= lo) & (freqs <= hi)
    return float(np.sqrt(np.mean(diff[mask] ** 2)))


def main() -> None:
    state_dirs = sorted(d for d in ROOT.iterdir()
                        if d.is_dir() and d.name.startswith("s") and len(d.name) == 6)

    # 各状態の平均スペクトルと per-bin σ を計算
    print("loading 32 states × 30 frames ...")
    mean_db: dict[str, np.ndarray] = {}
    std_db: dict[str, np.ndarray] = {}
    cls_map: dict[str, str] = {}
    freqs_ref: np.ndarray | None = None

    for sd in state_dirs:
        bits = parse_state_label(sd.name)
        if bits is None:
            continue
        cls_map[sd.name] = class_of(bits)
        spectra = []
        for wav in sorted(sd.glob("frame_*.wav")):
            samples = load_wav(wav)
            spectra.append(samples_to_logmag_psd(samples))
        arr = np.stack(spectra, axis=0)
        mean_db[sd.name] = arr.mean(axis=0)
        std_db[sd.name] = arr.std(axis=0, ddof=1)

    # freqs は features.py の定義から再構築
    from scipy import signal as sps
    f, _ = sps.welch(np.zeros(32000, dtype=np.float32), fs=16000,
                     window="hann", nperseg=2048, noverlap=1024)
    freqs_ref = f[1:]  # DC 落とし

    # サブ状態の集約: cls -> [state_name, ...]
    groups: dict[str, list[str]] = {}
    for state, cls in cls_map.items():
        groups.setdefault(cls, []).append(state)

    print(f"\n{'class':<5} {'pair (i, j)':<22} "
          f"{'RMS diff (dB)':>14} {'noise floor':>12} "
          f"{'SNR':>7} {'verdict':>10}")
    print("-" * 78)

    rows = []
    for cls in sorted(groups, key=lambda c: (c[0], int(c[1:]))):
        members = sorted(groups[cls])
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                # 平均スペクトル差
                diff = mean_db[a] - mean_db[b]
                rms_diff = in_band_rms(diff, freqs_ref)
                # 観測ノイズ床 (両 state の per-bin σ を統合)
                # 30 frame 平均なので mean の σ は σ_frame / sqrt(30)
                noise_floor_per_state = (std_db[a] + std_db[b]) / 2.0 / np.sqrt(30)
                noise_rms = in_band_rms(noise_floor_per_state, freqs_ref) * np.sqrt(2)
                snr = rms_diff / max(noise_rms, 1e-6)
                if snr > 5:
                    verdict = "STRONG"
                elif snr > 2:
                    verdict = "weak"
                else:
                    verdict = "noise"
                rows.append((cls, a, b, rms_diff, noise_rms, snr, verdict))
                print(f"{cls:<5} {a} vs {b}  {rms_diff:>14.3f} "
                      f"{noise_rms:>12.3f} {snr:>7.2f} {verdict:>10}")
        print()  # 区切り

    # CSV にも吐く
    out = ROOT / "analysis" / "overview" / "substate_separability.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["class", "state_a", "state_b",
                    "rms_diff_dB", "noise_floor_dB", "snr", "verdict"])
        for row in rows:
            w.writerow([row[0], row[1], row[2],
                        f"{row[3]:.4f}", f"{row[4]:.4f}", f"{row[5]:.3f}", row[6]])

    # サマリ
    print(f"\n=== サマリ ===")
    strong = sum(1 for r in rows if r[6] == "STRONG")
    weak = sum(1 for r in rows if r[6] == "weak")
    noise = sum(1 for r in rows if r[6] == "noise")
    print(f"  STRONG (SNR>5): {strong} pairs - 明確に分離可能")
    print(f"  weak (2<SNR<5): {weak} pairs - 統計的に有意だが学習困難")
    print(f"  noise (SNR<2): {noise} pairs - 実質ノイズ床")
    print(f"\n書き出し: {out}")


if __name__ == "__main__":
    main()
