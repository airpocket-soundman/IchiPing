"""Plot FFT magnitude of every WAV under captures/repro_test/, and save
the per-frame + mean + std spectra into each class folder as fft.csv.

Outputs per class folder:
  fft.csv             freq_hz, frame_0_db, frame_1_db, ..., mean_db, std_db
  (one row per FFT bin from 0 Hz to Nyquist)

Cross-class summary plots are written into the run root:
  fft_per_class.png   each class on its own panel, 5 frames overlaid
  fft_class_means.png the 3 means overlaid for direct comparison
  fft_class_diff.png  each non-reference class minus all_closed mean

Run:
    cd pc
    uv run python analyze_repro.py
"""
from __future__ import annotations

import csv
from pathlib import Path
import wave

import numpy as np
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent / "captures" / "repro_test"
CLASSES = ["all_closed", "AB_open", "a_open"]


def load_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise RuntimeError(f"unexpected format: {path}")
        rate = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return rate, samples


def fft_db(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(samples)
    win = np.hanning(n)
    sig = samples * win
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    # Normalise so dB is roughly comparable across runs.
    mag = np.abs(spec) / (n * 0.5)
    mag = np.maximum(mag, 1e-6)  # floor before log
    return freqs, 20.0 * np.log10(mag)


def main() -> None:
    if not ROOT.exists():
        raise SystemExit(f"no repro_test data at {ROOT}")

    class_avgs: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    fig.suptitle("FFT magnitude per class (5 frames overlaid)")

    for ax, cls in zip(axes, CLASSES):
        folder = ROOT / cls
        wavs = sorted(folder.glob("frame_*.wav"))
        if not wavs:
            ax.set_title(f"{cls}  (no WAVs)")
            continue
        stack_db: list[np.ndarray] = []
        freqs = None
        for i, w in enumerate(wavs):
            rate, samples = load_wav(w)
            freqs, mag_db = fft_db(samples, rate)
            stack_db.append(mag_db)
            ax.plot(freqs, mag_db, lw=0.7, alpha=0.7,
                    label=f"frame {i}" if i == 0 else None)
        stack = np.stack(stack_db, axis=0)
        avg = stack.mean(axis=0)
        spread = stack.std(axis=0)
        class_avgs[cls] = (freqs, avg)

        # Write per-bin CSV next to the WAVs.
        csv_path = folder / "fft.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            header = ["freq_hz"] + [f"frame_{i}_db" for i in range(stack.shape[0])] \
                     + ["mean_db", "std_db"]
            w.writerow(header)
            for j in range(len(freqs)):
                row = [f"{freqs[j]:.3f}"]
                row.extend(f"{stack[i, j]:.3f}" for i in range(stack.shape[0]))
                row.append(f"{avg[j]:.3f}")
                row.append(f"{spread[j]:.3f}")
                w.writerow(row)
        print(f"saved {csv_path}  ({len(freqs)} bins x {stack.shape[0]} frames)")
        ax.plot(freqs, avg, color="black", lw=1.3, label="mean")
        ax.set_title(f"{cls}  (n={len(wavs)}, mean±std spread={spread.mean():.1f} dB)")
        ax.set_ylabel("magnitude (dB)")
        ax.set_xlim(0, 8000)
        ax.set_ylim(-80, 0)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("frequency (Hz)")
    fig.tight_layout()
    out1 = ROOT / "fft_per_class.png"
    fig.savefig(out1, dpi=120)
    print(f"saved {out1}")

    # 2nd figure: class means overlaid
    fig2, ax2 = plt.subplots(figsize=(10, 5))
    for cls, (freqs, avg) in class_avgs.items():
        ax2.plot(freqs, avg, lw=1.3, label=cls)
    ax2.set_title("Class means overlaid (look for divergent regions)")
    ax2.set_xlabel("frequency (Hz)")
    ax2.set_ylabel("magnitude (dB)")
    ax2.set_xlim(0, 8000)
    ax2.set_ylim(-80, 0)
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    fig2.tight_layout()
    out2 = ROOT / "fft_class_means.png"
    fig2.savefig(out2, dpi=120)
    print(f"saved {out2}")

    # 3rd figure: pairwise difference vs all_closed reference
    if "all_closed" in class_avgs:
        ref_freqs, ref_avg = class_avgs["all_closed"]
        fig3, ax3 = plt.subplots(figsize=(10, 5))
        for cls, (freqs, avg) in class_avgs.items():
            if cls == "all_closed":
                continue
            diff = avg - ref_avg
            ax3.plot(freqs, diff, lw=1.3, label=f"{cls} − all_closed")
        ax3.axhline(0, color="black", lw=0.5)
        ax3.set_title("Difference from all_closed (peaks = bands where NN gets info)")
        ax3.set_xlabel("frequency (Hz)")
        ax3.set_ylabel("ΔdB")
        ax3.set_xlim(0, 8000)
        ax3.grid(True, alpha=0.3)
        ax3.legend()
        fig3.tight_layout()
        out3 = ROOT / "fft_class_diff.png"
        fig3.savefig(out3, dpi=120)
        print(f"saved {out3}")


if __name__ == "__main__":
    main()
