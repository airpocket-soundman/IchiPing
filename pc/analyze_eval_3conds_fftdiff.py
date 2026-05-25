"""eval_quiet / eval_noise_low / eval_noise_high の各 captures に対し:
1. 各 state の Welch スペクトル平均を取る
2. その captures 自身の s00000 を baseline として per-bin 引く
3. CLASS_ORDER_14 (A1, A2, B1..B4, C1..C8) でソート
4. ヒートマップ PNG として出力

ファイル: runs/eval_3conds_analysis/{cond}_fft_diff_by_class.png
"""
from __future__ import annotations

from pathlib import Path
import wave
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from training.dataset import class_of, parse_state_label, CLASS_ORDER_14
from training.features import samples_to_logmag_psd


ROOT = Path(__file__).resolve().parent
EVAL_DIRS = (
    ("quiet",      ROOT / "captures" / "eval_quiet"),
    ("noise_low",  ROOT / "captures" / "eval_noise_low"),
    ("noise_high", ROOT / "captures" / "eval_noise_high"),
)
OUT = ROOT / "runs" / "eval_3conds_analysis"


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def compute_state_means(captures_dir: Path) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """各 state の平均 log-mag PSD を返す。"""
    state_dirs = sorted(d for d in captures_dir.iterdir()
                        if d.is_dir() and d.name.startswith("s") and len(d.name) == 6)
    means = {}
    freqs_ref = None
    for sd in state_dirs:
        wavs = sorted(sd.glob("frame_*.wav"))
        if not wavs:
            continue
        spectra = [samples_to_logmag_psd(load_wav(p)) for p in wavs]
        means[sd.name] = np.stack(spectra, axis=0).mean(axis=0)
    # freqs を別経路で取得
    from scipy import signal as sps
    f, _ = sps.welch(np.zeros(32000, dtype=np.float32), fs=16000,
                     window="hann", nperseg=2048, noverlap=1024)
    freqs_ref = f[1:]
    return means, freqs_ref


def plot_diff_heatmap(means: dict[str, np.ndarray], freqs: np.ndarray,
                       title: str, out_path: Path) -> None:
    """state を CLASS_ORDER_14 順にソートして diff from s00000 をヒートマップに。"""
    if "s00000" not in means:
        print(f"  ! s00000 not found, skipping {out_path}")
        return
    baseline = means["s00000"]

    # state を class 順 + state 名でソート
    def sort_key(state):
        bits = parse_state_label(state)
        cls = class_of(bits)
        cls_idx = CLASS_ORDER_14.index(cls)
        return (cls_idx, state)
    sorted_states = sorted(means.keys(), key=sort_key)

    # 50 Hz - 7 kHz だけ表示
    f_mask = (freqs >= 50) & (freqs <= 7000)
    sub_freqs = freqs[f_mask]
    n = len(sorted_states)
    diff = np.zeros((n, sub_freqs.size), dtype=np.float32)
    for i, state in enumerate(sorted_states):
        diff[i, :] = means[state][f_mask] - baseline[f_mask]

    fig, ax = plt.subplots(figsize=(12, 8))
    vmax = float(np.percentile(np.abs(diff), 99))
    im = ax.pcolormesh(sub_freqs, np.arange(n), diff, shading="auto",
                       cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_yticks(np.arange(n))
    # y label: クラスタグ + state 名
    labels = []
    for s in sorted_states:
        bits = parse_state_label(s)
        cls = class_of(bits)
        labels.append(f"{cls:<3} {s}")
    ax.set_yticklabels(labels, fontsize=7, fontfamily="monospace")
    # クラス境界に水平線
    prev_cls = None
    for i, s in enumerate(sorted_states):
        cls = class_of(parse_state_label(s))
        if prev_cls is not None and cls != prev_cls:
            ax.axhline(i - 0.5, color="#1a1d23", linewidth=0.8, alpha=0.7)
        prev_cls = cls
    ax.set_ylabel("State (sorted by class)")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="dB vs s00000")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for cond_name, captures_dir in EVAL_DIRS:
        if not captures_dir.exists():
            print(f"  ! missing {captures_dir}")
            continue
        print(f"processing {cond_name}: {captures_dir}")
        means, freqs = compute_state_means(captures_dir)
        out = OUT / f"{cond_name}_fft_diff_by_class.png"
        plot_diff_heatmap(
            means, freqs,
            title=f"FFT diff from s00000 baseline — eval_{cond_name} (sorted by 14-class)",
            out_path=out,
        )
        print(f"  -> {out}")


if __name__ == "__main__":
    main()
