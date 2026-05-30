"""v1〜v6 captures の per-state FFT-diff-from-baseline 比較。

各 version について:
  1. 各 state の Welch log-mag PSD を frame 平均
  2. その version 自身の s00000 を baseline として per-bin で引く
  3. 14 等価クラス順 (A1..C8) でソートしたヒートマップ PNG

加えて:
  - v6 vs (v1..v5 mean) の diff ヒートマップ → ハード/環境変化で何が変わったか可視化
  - 各 group (A=AB closed / B=AB open BC closed / C=AB open BC open) の代表 state の
    1024-bin スペクトル差分を line plot で重ね描き

出力: runs/v1_6_fftdiff/
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
VERSIONS = [
    ("v1",      ROOT / "captures" / "full_32_train_v1"),
    ("v2",      ROOT / "captures" / "full_32_train_v2"),
    ("v3",      ROOT / "captures" / "full_32_train_v3"),
    ("v4",      ROOT / "captures" / "full_32_train_v4"),
    # v5 main は s00000 が 10 frame しかないので v5_part2 を採用
    ("v5",      ROOT / "captures" / "full_32_train_v5_part2"),
    ("v6",      ROOT / "captures" / "full_32_train_v6"),
]
OUT = ROOT / "runs" / "v1_6_fftdiff"
OUT.mkdir(parents=True, exist_ok=True)

SR = 16000
N_BINS = 1024


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def state_mean_psd(state_dir: Path) -> np.ndarray | None:
    wavs = sorted(state_dir.glob("frame_*.wav"))
    if not wavs:
        return None
    acc = None
    for p in wavs:
        s = load_wav(p)
        db = samples_to_logmag_psd(s)
        acc = db if acc is None else acc + db
    return (acc / len(wavs)).astype(np.float32)


def collect_version(name: str, root: Path) -> dict[str, np.ndarray]:
    """state -> (1024,) per-state-mean log-mag PSD (still in dB scale)"""
    out: dict[str, np.ndarray] = {}
    print(f"  {name}: scanning {root.name}", flush=True)
    for state_dir in sorted(root.iterdir()):
        if not state_dir.is_dir():
            continue
        bits = parse_state_label(state_dir.name)
        if bits is None:
            continue
        psd = state_mean_psd(state_dir)
        if psd is not None:
            out[state_dir.name] = psd
    return out


def diff_matrix(spec_by_state: dict[str, np.ndarray],
                baseline: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Returns (matrix [32, 1024], state labels) 14cls-class-grouped order."""
    sorted_states = sorted(spec_by_state.keys(),
                           key=lambda s: (CLASS_ORDER_14.index(
                               class_of(np.array([int(c) for c in s[1:]],
                                                 dtype=np.int64))), s))
    M = np.stack([spec_by_state[s] - baseline for s in sorted_states])
    return M, sorted_states


def plot_heatmap(M: np.ndarray, states: list[str], title: str, out: Path,
                 vmin: float = -8, vmax: float = 8):
    fig, ax = plt.subplots(figsize=(11, 7))
    im = ax.imshow(M, aspect="auto", origin="lower", cmap="RdBu_r",
                   vmin=vmin, vmax=vmax,
                   extent=[0, SR / 2, 0, len(states)])
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("state (14cls grouped)")
    ax.set_yticks(np.arange(len(states)) + 0.5)
    ax.set_yticklabels([f"{s}[{class_of(np.array([int(c) for c in s[1:]], dtype=np.int64))}]"
                         for s in states], fontsize=7)
    ax.set_title(title)
    cb = plt.colorbar(im, ax=ax, label="PSD - baseline (dB)")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"    saved {out}", flush=True)


def main():
    all_specs: dict[str, dict[str, np.ndarray]] = {}
    baselines: dict[str, np.ndarray] = {}
    for name, root in VERSIONS:
        if not root.exists():
            print(f"SKIP {name}: {root} not found")
            continue
        specs = collect_version(name, root)
        if "s00000" not in specs:
            print(f"SKIP {name}: s00000 baseline missing")
            continue
        all_specs[name] = specs
        baselines[name] = specs["s00000"]

    # 1. 各 version の per-state diff ヒートマップ
    print("=== per-version diff heatmaps ===")
    for name in all_specs:
        M, states = diff_matrix(all_specs[name], baselines[name])
        plot_heatmap(M, states,
                     f"{name}: state mean PSD - self-baseline (s00000)",
                     OUT / f"diff_{name}.png")

    # 2. v6 vs (v1..v5 mean) — 「v6 で何が変わったか」可視化
    if "v6" in all_specs and len(all_specs) >= 2:
        print("=== v6 vs v1..v5 mean ===")
        v6_M, states = diff_matrix(all_specs["v6"], baselines["v6"])
        # v1..v5 で全部に出ている state だけ揃える
        old_versions = [v for v in all_specs if v != "v6"]
        old_Ms = []
        for v in old_versions:
            M, _ = diff_matrix(all_specs[v], baselines[v])
            old_Ms.append(M)
        old_mean = np.mean(np.stack(old_Ms), axis=0)
        delta = v6_M - old_mean    # v6 が古い世代と何 dB ずれたか
        plot_heatmap(delta, states,
                     f"v6 minus mean(v1..v5): per-bin shift (dB)",
                     OUT / "delta_v6_vs_v1_5.png",
                     vmin=-6, vmax=6)

    # 3. 代表 state を選んで line plot — non-observable bit の信号がどこまで残ってるか
    REPS = [
        ("s00000", "A1 baseline"),
        ("s10000", "A2: a open"),
        ("s00100", "A1 hidden: c open (AB closed → 非観測のはず)"),
        ("s00010", "B1: AB open"),
        ("s00110", "B1 hidden: AB open + c open"),
        ("s00011", "C1: AB+BC open"),
        ("s11111", "C8: all open"),
    ]
    print("=== representative state line plots ===")
    freqs = np.linspace(0, SR / 2, N_BINS, endpoint=False)
    for state, label in REPS:
        fig, ax = plt.subplots(figsize=(10, 5))
        for name in all_specs:
            if state not in all_specs[name]:
                continue
            diff = all_specs[name][state] - baselines[name]
            ax.plot(freqs, diff, label=name, alpha=0.75, lw=1.0)
        ax.axhline(0, color="k", lw=0.5, alpha=0.5)
        ax.set_xlabel("frequency (Hz)")
        ax.set_ylabel("PSD - baseline (dB)")
        ax.set_title(f"{state} ({label})  diff-from-self-baseline across versions")
        ax.set_xlim(0, SR / 2)
        ax.legend(fontsize=8, ncol=3)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        out_p = OUT / f"line_{state}.png"
        fig.savefig(out_p, dpi=120)
        plt.close(fig)
        print(f"    saved {out_p}", flush=True)

    # 4. 簡易 summary CSV: 各 version の |diff| の平均/最大、c-bit-only 状態の検知量
    import csv
    rows = []
    for name in all_specs:
        M, states = diff_matrix(all_specs[name], baselines[name])
        # AB=0 (= A 群) で c が flip しているペアの diff 強度: state s00000 vs s00100
        a_pair = None
        if "s00000" in all_specs[name] and "s00100" in all_specs[name]:
            a_pair = float(np.abs(all_specs[name]["s00100"] - all_specs[name]["s00000"]).mean())
        b_pair = None
        if "s00010" in all_specs[name] and "s00110" in all_specs[name]:
            b_pair = float(np.abs(all_specs[name]["s00110"] - all_specs[name]["s00010"]).mean())
        rows.append({
            "version":    name,
            "n_states":   len(states),
            "abs_diff_mean":     float(np.abs(M).mean()),
            "abs_diff_max":      float(np.abs(M).max()),
            "c_flip_A_avg_dB":   a_pair,    # AB closed で c だけ動かしたときの差 (= 非観測領域の "漏れ")
            "c_flip_B_avg_dB":   b_pair,    # AB open / BC closed で c だけ動かしたとき
        })
    csv_path = OUT / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"saved {csv_path}")
    for r in rows:
        print(f"  {r}")


if __name__ == "__main__":
    main()
