"""full_32_train_v1 (32 状態 × 30 フレーム) の健全性チェック。

これまで full_32_v1 / full_32_noise_v1 は 1 状態 1 フレームだったため、
クラス内分散を直接観測できなかった。本収集 (REPEATS=30) ではそれが
直接計算できるので、以下を出力して NN 学習前のデータ品質を確認する:

  per-state:
    fft_mean.csv  -- freq_hz, mean_db, std_db (フレーム数 30)
    fft_mean.png  -- 平均 ± σ シェード描画

  overview/:
    intra_state_sigma.csv -- 状態別、300-6000 Hz 帯の per-bin σ の RMS
    intra_class_sigma.csv -- 14 クラス別、同帯のクラス内 σ (frame と state の両方を 1 標本扱い)
    inter_class_dist.csv  -- 14 クラス平均同士の RMS 距離 (dB)
    class_diff_heatmap.png -- 14 クラスを行に取った diff vs s00000 ヒートマップ

実行:
    cd pc
    uv run python analyze_full32_train.py
    # オプション:
    uv run python analyze_full32_train.py --root captures/full_32_train_v1
"""
from __future__ import annotations

import argparse
import csv
import wave
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# analyze_full32.py 側に既に等価クラス分類ロジックがある。重複定義は避けて再利用。
from analyze_full32 import (
    class_of,
    state_bits,
    sort_by_class,
    _CLASS_ORDER,
)


# ---------------------------------------------------------------------------
# WAV ロード + FFT
# ---------------------------------------------------------------------------

def load_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"unexpected format: {path}")
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    return rate, np.frombuffer(raw, dtype=np.int16).astype(np.float32)


def fft_db(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """全長 Hanning 窓 FFT。analyze_full32.whole_fft_db と同じ式。"""
    n = len(samples)
    win = np.hanning(n).astype(np.float32)
    spec = np.fft.rfft(samples * win)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    mag = np.abs(spec) / (n * 0.5)
    return freqs, 20.0 * np.log10(np.maximum(mag, 1e-6))


# ---------------------------------------------------------------------------
# 状態フォルダごとの集計
# ---------------------------------------------------------------------------

def aggregate_state(state_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """state_dir 内の全 frame_*.wav を読んで (freqs, mean_db, std_db, n_frames) を返す。

    1 サンプル WAV が壊れていても他の WAV から平均/分散は取り直せるよう、
    エラーフレームはスキップしてカウントだけ減らす方針。
    """
    wav_paths = sorted(state_dir.glob("frame_*.wav"))
    if not wav_paths:
        raise RuntimeError(f"no frame_*.wav under {state_dir}")
    freqs = None
    spectra = []
    for p in wav_paths:
        try:
            rate, samples = load_wav(p)
        except Exception as exc:
            print(f"  warning: skip {p.name}: {exc}")
            continue
        fr, db = fft_db(samples, rate)
        if freqs is None:
            freqs = fr
        spectra.append(db)
    arr = np.stack(spectra, axis=0)  # shape (n_frames, n_bins)
    return freqs, arr.mean(axis=0), arr.std(axis=0, ddof=1), arr.shape[0]


def save_state_csv(freqs: np.ndarray, mean_db: np.ndarray, std_db: np.ndarray,
                   out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["freq_hz", "mean_db", "std_db"])
        for f, m, s in zip(freqs, mean_db, std_db):
            w.writerow([f"{f:.2f}", f"{m:.3f}", f"{s:.3f}"])


def save_state_png(freqs: np.ndarray, mean_db: np.ndarray, std_db: np.ndarray,
                   label: str, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.semilogx(freqs, mean_db, linewidth=0.8, color="#1f77b4")
    ax.fill_between(freqs, mean_db - std_db, mean_db + std_db,
                    color="#1f77b4", alpha=0.25, linewidth=0)
    ax.set_xlim(50, freqs[-1])
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title(f"FFT mean ± σ — {label}")
    ax.grid(True, which="both", alpha=0.3)
    plt.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# クラス集計
# ---------------------------------------------------------------------------

def group_states_by_class(states: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for s in states:
        groups.setdefault(class_of(s), []).append(s)
    return groups


def in_band_rms(diff: np.ndarray, freqs: np.ndarray,
                lo: float = 300.0, hi: float = 6000.0) -> float:
    mask = (freqs >= lo) & (freqs <= hi)
    return float(np.sqrt(np.mean(diff[mask] ** 2)))


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="full_32_train_v1 健全性チェック")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parent / "captures" / "full_32_train_v1",
                    help="データ root (sXXXXX/frame_NNNNNN.wav を含む)")
    ap.add_argument("--out", type=Path, default=None,
                    help="解析成果物の出力先 (既定: <root>/analysis)")
    ap.add_argument("--skip-per-state-png", action="store_true",
                    help="状態ごとの PNG 出力をスキップ (CSV はそのまま)")
    args = ap.parse_args(argv)

    if not args.root.exists():
        print(f"FAIL: root not found: {args.root}")
        return 2
    out_dir = args.out if args.out else args.root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    state_dirs = sorted(d for d in args.root.iterdir()
                        if d.is_dir() and d.name.startswith("s") and len(d.name) == 6)
    if len(state_dirs) != 32:
        print(f"warning: expected 32 state dirs, found {len(state_dirs)}")
    print(f"discovered {len(state_dirs)} state folders under {args.root}")

    # ----- per-state 平均 / σ -----
    freqs_ref: np.ndarray | None = None
    mean_db: dict[str, np.ndarray] = {}
    std_db: dict[str, np.ndarray] = {}
    n_frames: dict[str, int] = {}

    for sd in state_dirs:
        print(f"  - {sd.name}: ", end="", flush=True)
        fr, m, s, n = aggregate_state(sd)
        if freqs_ref is None:
            freqs_ref = fr
        elif fr.shape != freqs_ref.shape:
            raise RuntimeError(f"freq grid mismatch in {sd.name}")
        mean_db[sd.name] = m
        std_db[sd.name] = s
        n_frames[sd.name] = n
        print(f"n={n}, σ(300-6k) RMS = {in_band_rms(s, fr):.3f} dB")

        state_out = out_dir / sd.name
        state_out.mkdir(parents=True, exist_ok=True)
        save_state_csv(fr, m, s, state_out / "fft_mean.csv")
        if not args.skip_per_state_png:
            save_state_png(fr, m, s, sd.name, state_out / "fft_mean.png")

    assert freqs_ref is not None

    # ----- intra-state σ サマリ (state ごとに 300-6k 帯 σ_per_bin の RMS) -----
    over_dir = out_dir / "overview"
    over_dir.mkdir(parents=True, exist_ok=True)
    intra_state_rows = []
    for state, sig in std_db.items():
        intra_state_rows.append((state, in_band_rms(sig, freqs_ref), n_frames[state]))
    intra_state_rows.sort(key=lambda r: -r[1])

    with (over_dir / "intra_state_sigma.csv").open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["state", "n_frames", "intra_state_sigma_rms_db"])
        for state, sig, n in intra_state_rows:
            w.writerow([state, n, f"{sig:.4f}"])

    # ----- intra-class σ サマリ (クラス内 frame × state の全分散) -----
    groups = group_states_by_class([s.name for s in state_dirs])
    intra_class_rows = []
    class_mean_db: dict[str, np.ndarray] = {}
    for cls in _CLASS_ORDER:
        if cls not in groups:
            continue
        members = groups[cls]
        # クラス内全 frame の平均と σ。状態の平均だけでなく frame レベルで通す。
        # = stack mean_db を素直に重ねるとフレーム内分散が消えるので、両方の指標を出す。
        means = np.stack([mean_db[s] for s in members], axis=0)  # (n_states, n_bins)
        # σ_intra_state は per-state σ の RMS (フレーム内ばらつきの代表値)
        per_state_sigmas = np.array([in_band_rms(std_db[s], freqs_ref) for s in members])
        intra_frame_db = float(per_state_sigmas.mean())
        # σ_inter_state は same-class 状態同士の平均スペクトル差の RMS (1 標本同士のずれ)
        if len(members) > 1:
            ref = means.mean(axis=0)
            inter_state_diffs = np.array([
                in_band_rms(means[i] - ref, freqs_ref) for i in range(len(members))
            ])
            inter_state_db = float(np.sqrt((inter_state_diffs ** 2).mean()))
        else:
            inter_state_db = 0.0
        class_mean_db[cls] = means.mean(axis=0)
        intra_class_rows.append((cls, len(members), intra_frame_db, inter_state_db))

    with (over_dir / "intra_class_sigma.csv").open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["class", "n_members", "intra_frame_sigma_db", "inter_state_sigma_db"])
        for cls, n, intra, inter in intra_class_rows:
            w.writerow([cls, n, f"{intra:.4f}", f"{inter:.4f}"])

    # ----- inter-class 距離 (クラス平均同士の RMS dB 差, 300-6k Hz) -----
    classes = [c for c in _CLASS_ORDER if c in class_mean_db]
    n_cls = len(classes)
    dist = np.zeros((n_cls, n_cls), dtype=np.float64)
    for i, ci in enumerate(classes):
        for j, cj in enumerate(classes):
            if i == j:
                continue
            dist[i, j] = in_band_rms(class_mean_db[ci] - class_mean_db[cj], freqs_ref)

    with (over_dir / "inter_class_dist.csv").open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow([""] + classes)
        for i, ci in enumerate(classes):
            w.writerow([ci] + [f"{dist[i, j]:.3f}" for j in range(n_cls)])

    # ----- 14 クラスの diff-from-baseline ヒートマップ -----
    if "A1" in class_mean_db:
        base = class_mean_db["A1"]
        f_mask = (freqs_ref >= 50) & (freqs_ref <= 7000)
        sub = freqs_ref[f_mask]
        diff = np.zeros((n_cls, sub.size), dtype=np.float32)
        for i, ci in enumerate(classes):
            diff[i, :] = class_mean_db[ci][f_mask] - base[f_mask]
        fig, ax = plt.subplots(figsize=(12, 5.5))
        vmax = float(np.percentile(np.abs(diff), 99))
        im = ax.pcolormesh(sub, np.arange(n_cls), diff, shading="auto",
                           cmap="RdBu_r", vmin=-vmax, vmax=vmax)
        ax.set_xscale("log")
        ax.set_xlabel("Frequency (Hz)")
        ax.set_yticks(np.arange(n_cls))
        ax.set_yticklabels(classes, fontfamily="monospace")
        ax.set_ylabel("class")
        # matplotlib のデフォルトフォントに日本語グリフがないので英語タイトルにする
        ax.set_title("14-class mean spectrum diff vs A1 (dB)")
        fig.colorbar(im, ax=ax, label="dB vs A1")
        plt.tight_layout()
        fig.savefig(over_dir / "class_diff_heatmap.png", dpi=130)
        plt.close(fig)

    # ----- コンソール出力 -----
    print("\n=== intra-state σ (300-6 kHz RMS of per-bin σ over 30 frames) ===")
    print(f"  {'state':<8} {'n':>3} {'σ_dB':>8}")
    for state, sig, n in intra_state_rows[:5]:
        print(f"  {state:<8} {n:>3} {sig:>8.3f}   (worst)")
    print("  ...")
    for state, sig, n in intra_state_rows[-3:]:
        print(f"  {state:<8} {n:>3} {sig:>8.3f}   (best)")

    print("\n=== intra-class σ summary (14 classes) ===")
    print(f"  {'cls':<4} {'n':>3} {'σ_frame_dB':>11} {'σ_state_dB':>11}")
    for cls, n, intra, inter in intra_class_rows:
        print(f"  {cls:<4} {n:>3} {intra:>11.3f} {inter:>11.3f}")

    print("\n=== nearest-neighbour class distances (dB, 300-6 kHz) ===")
    for i, ci in enumerate(classes):
        # 自分以外で最小のものを探す
        row = dist[i].copy()
        row[i] = np.inf
        j = int(np.argmin(row))
        print(f"  {ci:<4} -> {classes[j]:<4}  {row[j]:6.3f} dB")

    print(f"\ndone. analysis under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
