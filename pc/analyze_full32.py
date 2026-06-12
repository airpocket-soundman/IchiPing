"""Analyse the full_32_v1 dataset (32 states × 1 sample each, Linear chirp).

Two complementary representations per Linear chirp recording:

  1D 全データ FFT  — time-averaged |Y(f)|² across the whole 2 s recording.
                     Easy overlay across states; shows aggregate spectral
                     differences but loses chirp time-frequency structure.

  2D STFT ヒートマップ — time × frequency spectrogram of the chirp response.
                        Resonance ridges and mode splits appear directly
                        because the chirp drives each frequency at a known
                        instant; the brightness pattern is the room's
                        impulse response convolved with the chirp.

Outputs (under pc/captures/full_32_v1/analysis/ by default):

  per-state subfolder sXXXXX/:
    fft.png, spectrogram.png, fft.csv (freq_hz, mag_db)

  overview/:
    fft_overlay_all.png         — all 32 states overlaid (alpha-shaded)
    fft_single_open.png         — s00000 vs s{a,b,c,AB,BC}_open only
    fft_diff_from_baseline.png  — heatmap of mag_db − s00000 mag_db
                                  (rows = states, cols = freq)
    spectrogram_grid.png        — 32-panel STFT grid (4×8)

Run:
    cd pc
    uv run python analyze_full32.py
    # or:
    uv run python analyze_full32.py --root captures/full_32_v1 --out captures/full_32_v1/analysis
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
from matplotlib.colors import LinearSegmentedColormap
from scipy import signal as sps


# ---------------------------------------------------------------------------
# WAV I/O
# ---------------------------------------------------------------------------

def load_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"unexpected format: {path}")
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    return rate, np.frombuffer(raw, dtype=np.int16).astype(np.float32)


# ---------------------------------------------------------------------------
# Spectra
# ---------------------------------------------------------------------------

def whole_fft_db(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Single Hanning-windowed FFT of the full record; returns (freqs, dB)."""
    n = len(samples)
    win = np.hanning(n).astype(np.float32)
    spec = np.fft.rfft(samples * win)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    mag = np.abs(spec) / (n * 0.5)
    return freqs, 20.0 * np.log10(np.maximum(mag, 1e-6))


def stft_db(samples: np.ndarray, rate: int,
            nperseg: int = 512, noverlap: int = 384):
    """Returns (f, t, Sxx_db) for plotting."""
    f, t, Sxx = sps.spectrogram(samples, fs=rate,
                                nperseg=nperseg, noverlap=noverlap,
                                window="hann", scaling="spectrum")
    return f, t, 10.0 * np.log10(np.maximum(Sxx, 1e-10))


# ---------------------------------------------------------------------------
# State encoding helpers
# ---------------------------------------------------------------------------

# sABCDE — A=window a, B=window b, C=window c, D=door AB, E=door BC
# 1 = open, 0 = closed
LABEL_NAMES = ["a", "b", "c", "AB", "BC"]


def state_to_label(state_str: str) -> str:
    """sABCDE -> '全閉' / 'a' / 'a+AB' / 'all open' style human label."""
    bits = state_str[1:]    # strip the 's'
    opens = [LABEL_NAMES[i] for i, b in enumerate(bits) if b == "1"]
    if not opens:
        return "all-closed (s00000)"
    return "+".join(opens) + f" ({state_str})"


def state_bits(state_str: str) -> tuple[int, ...]:
    return tuple(int(b) for b in state_str[1:])


def count_open(state_str: str) -> int:
    return sum(state_bits(state_str))


def class_of(state_str: str) -> str:
    """Return the equivalence-class tag (A1/A2/B1..B4/C1..C8) for an sABCDE label.

    Equivalence rule: doors block observation behind them.
      - AB=0          -> Room A only matters. Class depends on (a).
      - AB=1, BC=0    -> Rooms A+B. Class depends on (a, b).
      - AB=1, BC=1    -> All three rooms. Class depends on (a, b, c).
    """
    a, b, c, AB, BC = state_bits(state_str)
    if AB == 0:
        return "A1" if a == 0 else "A2"
    if BC == 0:
        return {(0, 0): "B1", (1, 0): "B2",
                (0, 1): "B3", (1, 1): "B4"}[(a, b)]
    return "C" + str(1 + a + 2 * b + 4 * c)


# Canonical class display order for the grouped heatmap. Stable across runs.
_CLASS_ORDER = ["A1", "A2", "B1", "B2", "B3", "B4",
                "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def sort_by_class(states: list[str]) -> list[str]:
    """Return states ordered first by class, then by label within class."""
    rank = {c: i for i, c in enumerate(_CLASS_ORDER)}
    return sorted(states, key=lambda s: (rank[class_of(s)], s))


# ---------------------------------------------------------------------------
# Per-state outputs
# ---------------------------------------------------------------------------

def save_fft_png(freqs, mag_db, label: str, out_path: Path,
                 floor_db: float | None = None) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.semilogx(freqs, mag_db, linewidth=0.7)
    ax.set_xlim(50, freqs[-1])
    if floor_db is not None:
        ax.set_ylim(floor_db, mag_db.max() + 5)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title(f"FFT — {label}")
    ax.grid(True, which="both", alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def save_spectrogram_png(f, t, Sxx_db, label: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    im = ax.pcolormesh(t, f, Sxx_db, shading="gouraud", cmap="magma",
                       vmin=Sxx_db.max() - 80, vmax=Sxx_db.max())
    ax.set_yscale("log")
    ax.set_ylim(80, f[-1])
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title(f"Spectrogram — {label}")
    fig.colorbar(im, ax=ax, label="dB")
    plt.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def save_fft_csv(freqs, mag_db, out_path: Path) -> None:
    with out_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["freq_hz", "mag_db"])
        for fh, db in zip(freqs, mag_db):
            w.writerow([f"{fh:.2f}", f"{db:.3f}"])


# ---------------------------------------------------------------------------
# Overview outputs
# ---------------------------------------------------------------------------

def make_overlay(states: dict[str, tuple[np.ndarray, np.ndarray]],
                 out_path: Path) -> None:
    """All 32 states overlaid, colour-coded by number of open elements."""
    fig, ax = plt.subplots(figsize=(11, 6))
    # Colour by open-count: 0=closed (red highlight), 5=all-open (blue)
    cmap = plt.get_cmap("viridis")
    for state, (freqs, db) in sorted(states.items()):
        n_open = count_open(state)
        color = cmap(n_open / 5.0)
        alpha = 0.5 if 0 < n_open < 5 else 0.95   # endpoints emphasised
        lw = 1.3 if n_open in (0, 5) else 0.5
        ax.semilogx(freqs, db, color=color, linewidth=lw, alpha=alpha,
                    label=state if n_open in (0, 5) else None)
    ax.set_xlim(50, freqs[-1])
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title("FFT overlay — 32 states (colour = number of open doors/windows)")
    ax.grid(True, which="both", alpha=0.3)

    # Colour-bar
    sm = plt.cm.ScalarMappable(cmap=cmap,
                               norm=plt.Normalize(vmin=0, vmax=5))
    fig.colorbar(sm, ax=ax, label="# open (windows + doors)")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def make_single_open_overlay(states: dict[str, tuple[np.ndarray, np.ndarray]],
                             out_path: Path) -> None:
    """Show baseline + 5 single-open states (Day-0 minimal contrast)."""
    fig, ax = plt.subplots(figsize=(11, 6))
    keys_of_interest = ["s00000"] + [
        # single-open: bit i = 1, rest 0  (i ∈ {0:a, 1:b, 2:c, 3:AB, 4:BC})
        "s" + "".join("1" if j == i else "0" for j in range(5))
        for i in range(5)
    ]
    palette = ["#222222", "#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd"]
    for state, color in zip(keys_of_interest, palette):
        if state not in states:
            continue
        freqs, db = states[state]
        ax.semilogx(freqs, db, color=color, linewidth=1.1, alpha=0.9,
                    label=state_to_label(state))
    ax.set_xlim(50, freqs[-1])
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title("FFT — baseline vs each single-element open (Day-0 contrast)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def make_diff_heatmap(states: dict[str, tuple[np.ndarray, np.ndarray]],
                      baseline_key: str, out_path: Path,
                      order: list[str] | None = None,
                      title_suffix: str = "",
                      draw_class_dividers: bool = False,
                      label_fn=None,
                      ylabel: str = "State (sABCDE)") -> None:
    """Heatmap: rows=states, cols=freq bins, values=mag_db - baseline.

    order: explicit row order (defaults to alphabetical).
    draw_class_dividers: when True, add horizontal lines between equivalence
        classes and put the class tag in the y-tick label.
    label_fn: 表示専用のラベル変換 (例: state_labels.s_to_h)。内部のキー・
        等価クラス判定は s 表記のまま、軸・タイトルの表示だけ変換する。
    """
    disp = label_fn if label_fn is not None else (lambda k: k)
    if baseline_key not in states:
        print(f"warning: baseline {baseline_key} missing; skipping diff heatmap")
        return
    sorted_keys = order if order is not None else sorted(states.keys())
    freqs, base_db = states[baseline_key]
    n_states = len(sorted_keys)

    # Limit frequency range to where we have content (50 Hz – Nyquist).
    f_mask = (freqs >= 50) & (freqs <= 7000)
    sub_freqs = freqs[f_mask]
    diff = np.zeros((n_states, sub_freqs.size), dtype=np.float32)
    for i, state in enumerate(sorted_keys):
        diff[i, :] = states[state][1][f_mask] - base_db[f_mask]

    fig, ax = plt.subplots(figsize=(12, 7))
    # Symmetric colormap centred on 0
    vmax = float(np.percentile(np.abs(diff), 99))
    im = ax.pcolormesh(sub_freqs, np.arange(n_states), diff,
                        shading="auto", cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_yticks(np.arange(n_states))
    if draw_class_dividers:
        # Tag each label with its equivalence class for quick orientation.
        ytick_labels = [f"{class_of(k):<3} {disp(k)}" for k in sorted_keys]
        ax.set_yticklabels(ytick_labels, fontsize=7, fontfamily="monospace")
        # Horizontal lines between class transitions.
        prev_cls = None
        for i, key in enumerate(sorted_keys):
            cls = class_of(key)
            if prev_cls is not None and cls != prev_cls:
                ax.axhline(i - 0.5, color="#1a1d23", linewidth=0.8, alpha=0.7)
            prev_cls = cls
    else:
        ax.set_yticklabels([disp(k) for k in sorted_keys], fontsize=7)
    ax.set_ylabel(ylabel)
    ax.set_title(f"FFT diff from baseline {disp(baseline_key)} (dB){title_suffix}")
    fig.colorbar(im, ax=ax, label=f"dB vs {disp(baseline_key)}")
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def make_spectrogram_grid(state_keys: list[str], wavs: dict[str, tuple[int, np.ndarray]],
                          out_path: Path,
                          nperseg: int = 512, noverlap: int = 384) -> None:
    """4×8 grid of 32 spectrograms with shared color scale."""
    keys = sorted(state_keys)
    rows, cols = 4, 8
    fig, axes = plt.subplots(rows, cols, figsize=(20, 11), sharex=True, sharey=True)

    # Determine global dB range from first state
    rate, samples0 = wavs[keys[0]]
    _, _, S0 = stft_db(samples0, rate, nperseg=nperseg, noverlap=noverlap)
    vmax_global = S0.max()
    vmin_global = vmax_global - 80

    im = None
    for ax, key in zip(axes.flat, keys):
        rate, samples = wavs[key]
        f, t, S = stft_db(samples, rate, nperseg=nperseg, noverlap=noverlap)
        im = ax.pcolormesh(t, f, S, shading="gouraud", cmap="magma",
                            vmin=vmin_global, vmax=vmax_global)
        ax.set_yscale("log")
        ax.set_ylim(100, f[-1])
        ax.set_title(key, fontsize=8)
        ax.tick_params(labelsize=6)

    for ax in axes[-1, :]:
        ax.set_xlabel("t (s)", fontsize=7)
    for ax in axes[:, 0]:
        ax.set_ylabel("f (Hz)", fontsize=7)

    fig.suptitle("STFT grid — 32 states (Linear chirp 200 Hz → 6 kHz, 2 s)",
                 fontsize=12)
    if im is not None:
        fig.colorbar(im, ax=axes.ravel().tolist(), label="dB", fraction=0.018,
                     pad=0.02)
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyse full_32_v1 dataset.")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parent / "captures" / "full_32_v1",
                    help="dataset root containing sXXXXX/frame_000000.wav")
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir for analysis artefacts (default: <root>/analysis)")
    ap.add_argument("--baseline", default="s00000",
                    help="state key used as zero for the diff heatmap")
    ap.add_argument("--skip-per-state", action="store_true",
                    help="skip per-state PNG generation (saves time on re-runs)")
    args = ap.parse_args(argv)

    if not args.root.exists():
        print(f"FAIL: dataset root not found: {args.root}")
        return 2
    out_dir = args.out if args.out else args.root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Discover state folders
    state_dirs = sorted(d for d in args.root.iterdir()
                         if d.is_dir() and d.name.startswith("s") and len(d.name) == 6)
    if not state_dirs:
        print(f"FAIL: no sXXXXX folders under {args.root}")
        return 2
    print(f"found {len(state_dirs)} state folders")

    # Load all WAVs and compute whole-data FFT once.
    wavs: dict[str, tuple[int, np.ndarray]] = {}
    ffts: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for sd in state_dirs:
        wav_path = sd / "frame_000000.wav"
        if not wav_path.exists():
            print(f"warning: no frame_000000.wav in {sd}")
            continue
        rate, samples = load_wav(wav_path)
        wavs[sd.name] = (rate, samples)
        ffts[sd.name] = whole_fft_db(samples, rate)

    print(f"loaded {len(wavs)} states")

    # Per-state outputs
    if not args.skip_per_state:
        for state, (rate, samples) in wavs.items():
            state_out = out_dir / state
            state_out.mkdir(parents=True, exist_ok=True)
            freqs, mag_db = ffts[state]

            save_fft_csv(freqs, mag_db, state_out / "fft.csv")
            save_fft_png(freqs, mag_db, state_to_label(state),
                         state_out / "fft.png", floor_db=-60.0)
            f, t, Sxx = stft_db(samples, rate)
            save_spectrogram_png(f, t, Sxx, state_to_label(state),
                                  state_out / "spectrogram.png")
        print(f"wrote per-state FFT + spectrogram into {out_dir}/sXXXXX/")

    # Overview outputs
    over_dir = out_dir / "overview"
    over_dir.mkdir(parents=True, exist_ok=True)

    make_overlay(ffts, over_dir / "fft_overlay_all.png")
    print(f"wrote {over_dir/'fft_overlay_all.png'}")

    make_single_open_overlay(ffts, over_dir / "fft_single_open.png")
    print(f"wrote {over_dir/'fft_single_open.png'}")

    make_diff_heatmap(ffts, args.baseline, over_dir / "fft_diff_from_baseline.png")
    print(f"wrote {over_dir/'fft_diff_from_baseline.png'}")

    grouped_order = sort_by_class(list(ffts.keys()))
    make_diff_heatmap(
        ffts, args.baseline,
        over_dir / "fft_diff_from_baseline_by_class.png",
        order=grouped_order,
        title_suffix="  — sorted by equivalence class",
        draw_class_dividers=True,
    )
    print(f"wrote {over_dir/'fft_diff_from_baseline_by_class.png'}")

    make_spectrogram_grid(list(wavs.keys()), wavs,
                          over_dir / "spectrogram_grid.png")
    print(f"wrote {over_dir/'spectrogram_grid.png'}")

    # Quick text summary: in-band diff statistics vs baseline
    if args.baseline in ffts:
        base_freqs, base_db = ffts[args.baseline]
        band = (base_freqs >= 300) & (base_freqs <= 6000)
        print(f"\nIn-band (300-6000 Hz) RMS dB difference vs {args.baseline}:")
        rows = []
        for state in sorted(ffts.keys()):
            diff = ffts[state][1][band] - base_db[band]
            rms = float(np.sqrt((diff ** 2).mean()))
            mx  = float(np.abs(diff).max())
            rows.append((state, rms, mx))
        # Sort by RMS so most-different states float to top
        rows.sort(key=lambda r: -r[1])
        print(f"  {'state':<8} {'RMS_dB':>8} {'max|Δ|':>8}")
        for state, rms, mx in rows[:10]:
            print(f"  {state:<8} {rms:8.2f} {mx:8.2f}")
        print("  ...")
        for state, rms, mx in rows[-3:]:
            print(f"  {state:<8} {rms:8.2f} {mx:8.2f}")

    print(f"\ndone. analysis artefacts under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
