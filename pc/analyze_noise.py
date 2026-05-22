"""Compare two silent recording conditions (quiet vs TV) to map out which
frequencies are vulnerable to external noise.

Inputs (default: captures/silence_2cond_v1/):
    silence_quiet/frame_000000..N.wav
    silence_tv/frame_000000..M.wav

Outputs (under <root>/analysis/):
    quiet_mean_std.png      mean spectrum + ±1σ shaded band
    tv_mean_std.png         same for the TV condition
    overlay.png             both conditions overlaid in dB
    excess_over_quiet.png   tv − quiet (per-frequency noise increment)
    snr_vs_full32.png       if --chirp-root points at a chirp dataset,
                            compute SNR = chirp_signal − each_noise_floor

Run:
    cd pc
    uv run --extra training python analyze_noise.py
    uv run --extra training python analyze_noise.py \
        --root captures/silence_2cond_v1 \
        --chirp-root captures/full_32_v1
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# WAV loading
# ---------------------------------------------------------------------------

def load_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"unexpected format: {path}")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return rate, np.frombuffer(raw, dtype=np.int16).astype(np.float32)


def load_condition(condition_dir: Path) -> tuple[int, np.ndarray, np.ndarray]:
    """Load all frames in a condition folder; return (rate, freqs, dB_matrix).

    dB_matrix shape: (n_frames, n_freq_bins) — each row is one frame's FFT in dB.
    """
    wavs = sorted(condition_dir.glob("frame_*.wav"))
    if not wavs:
        raise RuntimeError(f"no frame_*.wav files under {condition_dir}")

    rate, first = load_wav(wavs[0])
    n_samples = len(first)
    win = np.hanning(n_samples).astype(np.float32)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / rate)

    rows = []
    for wav_path in wavs:
        _, samples = load_wav(wav_path)
        if len(samples) != n_samples:
            samples = samples[:n_samples]
            if len(samples) < n_samples:
                samples = np.pad(samples, (0, n_samples - len(samples)))
        mag = np.abs(np.fft.rfft(samples * win)) / (n_samples * 0.5)
        rows.append(20.0 * np.log10(np.maximum(mag, 1e-6)))
    return rate, freqs, np.array(rows, dtype=np.float64)


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def plot_mean_std(freqs: np.ndarray, db_matrix: np.ndarray, label: str,
                  out_path: Path, rate: int) -> None:
    mean = db_matrix.mean(axis=0)
    std = db_matrix.std(axis=0)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogx(freqs, mean, linewidth=1.0, color="C0", label=f"mean (N={len(db_matrix)})")
    ax.fill_between(freqs, mean - std, mean + std, color="C0", alpha=0.25,
                    label="±1σ")
    ax.set_xlim(20, rate / 2)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB, arbitrary ref)")
    ax.set_title(f"Noise floor — {label}")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_overlay(freqs: np.ndarray,
                 conds: dict[str, np.ndarray],
                 out_path: Path, rate: int) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = {"silence_quiet": "C0", "silence_tv": "C3"}
    for name, mat in conds.items():
        mean = mat.mean(axis=0)
        std = mat.std(axis=0)
        c = colors.get(name, None)
        ax.semilogx(freqs, mean, linewidth=1.2, label=f"{name} (N={len(mat)})",
                    color=c)
        ax.fill_between(freqs, mean - std, mean + std, alpha=0.18, color=c)
    ax.set_xlim(20, rate / 2)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB, arbitrary ref)")
    ax.set_title("Noise floor — quiet vs TV (mean ± 1σ)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_excess(freqs: np.ndarray,
                quiet: np.ndarray, tv: np.ndarray,
                out_path: Path, rate: int) -> None:
    quiet_mean = quiet.mean(axis=0)
    tv_mean = tv.mean(axis=0)
    excess = tv_mean - quiet_mean

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogx(freqs, excess, linewidth=1.0, color="#c2185b")
    ax.axhline(0, linewidth=0.6, color="k", alpha=0.6)
    ax.set_xlim(20, rate / 2)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("dB excess (tv − quiet)")
    ax.set_title("Noise increment from TV vs quiet (positive = TV louder)")
    ax.grid(True, which="both", alpha=0.3)

    # Shade bands of interest
    for lo, hi, name in [(50, 300, "low"), (300, 1500, "mid"),
                         (1500, 6000, "speech/upper"), (6000, 8000, "ultra")]:
        ax.axvspan(lo, hi, alpha=0.07,
                   color={"low": "blue", "mid": "green",
                          "speech/upper": "orange", "ultra": "purple"}[name])
        ax.text(np.sqrt(lo * hi), excess.max() * 0.9 if excess.max() > 0 else -2,
                name, alpha=0.5, ha="center", fontsize=9)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_snr_vs_chirp(freqs_noise: np.ndarray,
                      conds: dict[str, np.ndarray],
                      chirp_root: Path,
                      out_path: Path, rate: int) -> None:
    """Estimate SNR = chirp_signal − noise_floor for each condition.

    Uses the s00000 (all-closed) chirp recording as the canonical signal level.
    If the chirp dataset is at a different sample rate this won't be aligned;
    we resample by interpolation onto the noise FFT bins. For 16 kHz vs 16 kHz
    it's a no-op.
    """
    chirp_wav = chirp_root / "s00000" / "frame_000000.wav"
    if not chirp_wav.exists():
        print(f"  (no {chirp_wav}; skipping SNR plot)")
        return
    rate_c, samples = load_wav(chirp_wav)
    win = np.hanning(len(samples)).astype(np.float32)
    mag_c = np.abs(np.fft.rfft(samples * win)) / (len(samples) * 0.5)
    db_c = 20.0 * np.log10(np.maximum(mag_c, 1e-6))
    freqs_c = np.fft.rfftfreq(len(samples), d=1.0 / rate_c)

    # Interpolate chirp dB onto noise freqs (handles small length differences).
    db_c_on_noise = np.interp(freqs_noise, freqs_c, db_c, left=db_c[0], right=db_c[-1])

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = {"silence_quiet": "C0", "silence_tv": "C3"}
    for name, mat in conds.items():
        snr = db_c_on_noise - mat.mean(axis=0)
        c = colors.get(name)
        ax.semilogx(freqs_noise, snr, linewidth=1.2, label=f"SNR in {name}", color=c)
    ax.axhline(20, linewidth=0.8, color="green", linestyle=":",
               alpha=0.8, label="20 dB threshold")
    ax.axhline(10, linewidth=0.8, color="orange", linestyle=":",
               alpha=0.8, label="10 dB threshold")
    ax.axhline(0,  linewidth=0.5, color="k", alpha=0.4)
    ax.set_xlim(20, rate / 2)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("SNR (dB) — chirp s00000 − noise floor")
    ax.set_title("Effective SNR per band — chirp signal vs noise floor")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare silent recordings under different conditions.")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parent / "captures" / "silence_2cond_v1",
                    help="dataset root containing silence_quiet/ and silence_tv/")
    ap.add_argument("--chirp-root", type=Path,
                    default=Path(__file__).resolve().parent / "captures" / "full_32_v1",
                    help="optional chirp dataset for SNR overlay (looks at s00000/frame_000000.wav)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: <root>/analysis)")
    args = ap.parse_args(argv)

    if not args.root.exists():
        print(f"FAIL: dataset root not found: {args.root}")
        return 2
    out_dir = args.out if args.out else args.root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    conds: dict[str, np.ndarray] = {}
    rate = None
    freqs = None
    for cond_name in ("silence_quiet", "silence_tv"):
        cond_dir = args.root / cond_name
        if not cond_dir.exists():
            print(f"warning: missing {cond_dir}, skipping")
            continue
        r, f, mat = load_condition(cond_dir)
        if rate is None:
            rate = r
            freqs = f
        elif r != rate:
            print(f"FAIL: sample rate mismatch ({r} vs {rate})")
            return 2
        conds[cond_name] = mat
        plot_mean_std(freqs, mat, cond_name, out_dir / f"{cond_name}_mean_std.png", rate)
        print(f"  wrote {out_dir/f'{cond_name}_mean_std.png'} ({len(mat)} reps)")

    if "silence_quiet" not in conds or "silence_tv" not in conds:
        print("FAIL: need both silence_quiet and silence_tv folders for full comparison")
        return 0  # partial output already produced

    plot_overlay(freqs, conds, out_dir / "overlay.png", rate)
    print(f"  wrote {out_dir/'overlay.png'}")
    plot_excess(freqs, conds["silence_quiet"], conds["silence_tv"],
                out_dir / "excess_over_quiet.png", rate)
    print(f"  wrote {out_dir/'excess_over_quiet.png'}")

    if args.chirp_root and args.chirp_root.exists():
        plot_snr_vs_chirp(freqs, conds, args.chirp_root,
                          out_dir / "snr_vs_chirp.png", rate)
        print(f"  wrote {out_dir/'snr_vs_chirp.png'}")

    # ----- Text summary -----
    quiet_mean = conds["silence_quiet"].mean(axis=0)
    tv_mean = conds["silence_tv"].mean(axis=0)
    excess = tv_mean - quiet_mean

    print()
    print("Noise floor summary (dB) by band:")
    print(f"  {'band (Hz)':<14} {'quiet':>9} {'tv':>9} {'tv-quiet':>10} {'tv std':>8}")
    for lo, hi in [(50, 100), (100, 200), (200, 400), (400, 800),
                   (800, 1500), (1500, 3000), (3000, 6000), (6000, 8000)]:
        mask = (freqs >= lo) & (freqs < hi)
        q = float(quiet_mean[mask].mean())
        t = float(tv_mean[mask].mean())
        ex = float(excess[mask].mean())
        ts = float(conds["silence_tv"][:, mask].std())
        print(f"  {lo:>5}-{hi:<5}    {q:>9.2f} {t:>9.2f} {ex:>+10.2f} {ts:>8.2f}")

    print(f"\ndone. outputs under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
