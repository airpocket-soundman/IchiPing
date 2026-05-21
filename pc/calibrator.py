"""IchiPing — SPK / mic calibration CLI.

Companion tool to ``collector_client.py``. Each subcommand is one shot:
open serial → run a fixed sequence → close. Designed for non-interactive
use from shell scripts or AI agents.

Subcommands
-----------
  record           Emit white noise from the board (filter on or off) and
                   save the recorded WAV to disk.
  analyze          Compute FFT + STFT 2D spectrogram of a WAV; save PNGs.
  design-filter    From a raw (filter-off) recording, design an 8-stage
                   biquad cascade EQ that flattens the SPK+mic response.
                   Output: filter JSON consumable by ``upload-filter``.
  upload-filter    Send the JSON filter to the board via EQ SET / EQ ENABLE.
  compare          Side-by-side spectrogram + overlaid FFT of two WAVs.

Typical workflow
----------------
  # 1. Take SPK+mic out of the house, cover with a futon (anechoic-ish)
  python calibrator.py record --port COM7 --out captures/raw.wav

  # 2. Inspect the raw response
  python calibrator.py analyze captures/raw.wav

  # 3. Design EQ
  python calibrator.py design-filter captures/raw.wav --out captures/filter.json

  # 4. Push to board and enable
  python calibrator.py upload-filter --port COM7 captures/filter.json --enable

  # 5. Record again with filter ON
  python calibrator.py record --port COM7 --out captures/filtered.wav --filter-on

  # 6. Compare
  python calibrator.py compare captures/raw.wav captures/filtered.wav

Dependencies
------------
  Required: pyserial (always installed via pyproject.toml)
  Optional (analyze / design-filter / compare): numpy, scipy, matplotlib
  Install via: uv sync --extra training
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import time
import wave
from pathlib import Path

import serial

# Reuse helpers from the main collector client. These imports are at module
# level because they're needed by every subcommand that talks to the board.
from collector_client import (
    StreamReader,
    send,
    wait_for_ack,
    wait_for_prefix,
)


DEFAULT_BAUD = 921_600
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_DURATION_MS = 3_000
DEFAULT_VOLUME_PCT = 30
DEFAULT_BOOT_WAIT_S = 6.0


# ---------------------------------------------------------------------------
# Serial helpers (board ↔ PC plumbing)
# ---------------------------------------------------------------------------

def open_board(port: str, baud: int = DEFAULT_BAUD,
               wait_boot: bool = True) -> tuple[serial.Serial, StreamReader]:
    """Open the serial port and start the reader thread.

    Returns (ser, reader). Caller must close them in reverse order via
    ``close_board()``.
    """
    ser = serial.Serial(port, baud, timeout=0.1)
    reader = StreamReader(ser)
    reader.start()
    if wait_boot:
        # If the port just opened, DTR toggle may have reset the MCU. Wait
        # for the boot banner; if it does not arrive in 6 s we assume the
        # MCU was already up and skip the wait.
        ack = wait_for_prefix(reader, "INFO IchiPing", timeout=DEFAULT_BOOT_WAIT_S)
        if ack is None:
            print("warning: no boot banner in 6 s; assuming MCU is already running",
                  file=sys.stderr)
    return ser, reader


def close_board(ser: serial.Serial, reader: StreamReader) -> None:
    """Stop the reader thread and close the serial port. Tolerant to None."""
    if reader is not None:
        reader.stop()
        time.sleep(0.05)
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass


def send_ack(ser: serial.Serial, reader: StreamReader, cmd: str,
             timeout: float = 2.0, echo: bool = True) -> str | None:
    """Send one command, wait for the next OK/ERR/INFO line as ack.

    Returns the ack line, or None on timeout. ``echo=True`` prints the
    sent command and received reply to stdout (useful for debug)."""
    send(ser, cmd)
    ack = wait_for_ack(reader, timeout=timeout)
    if echo:
        print(f"  > {cmd}")
        if ack is not None:
            print(f"  < {ack}")
        else:
            print(f"  < (no ack in {timeout:.1f} s)")
    return ack


def wait_one_frame(reader: StreamReader, timeout: float = 10.0):
    """Pop one ICHP frame from the reader's queue. Returns None on timeout."""
    try:
        return reader.frames.get(timeout=timeout)
    except queue.Empty:
        return None


# ---------------------------------------------------------------------------
# WAV I/O (no numpy dependency — bytes-level)
# ---------------------------------------------------------------------------

def save_wav_int16(samples: bytes | bytearray | memoryview,
                   sample_rate: int, path: Path) -> None:
    """Save raw int16 LE bytes as a mono WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(bytes(samples))


def load_wav(path: Path):
    """Load a WAV; return (sample_rate, numpy int16 array). Imports numpy lazily."""
    import numpy as np
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
        n = f.getnframes()
        raw = f.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16)
    return rate, samples


# ---------------------------------------------------------------------------
# Subcommand: record
# ---------------------------------------------------------------------------

def cmd_record(args) -> int:
    """Emit white noise from the board and save the recording."""
    ser = None
    reader = None
    try:
        ser, reader = open_board(args.port, args.baud)

        # EQ state: explicit so the operator knows what is being recorded.
        # Calibration recordings MUST use --filter-off; running with EQ ON
        # would create a circular dependency on the filter being designed.
        ack = send_ack(ser, reader, "EQ ENABLE" if args.filter_on else "EQ DISABLE")
        if not ack or not ack.startswith("OK"):
            print(f"FAIL: EQ toggle did not ack ({ack})", file=sys.stderr)
            return 2

        # Register a temporary noise pattern. Wipe any previous patterns first
        # so the index we select is known.
        send_ack(ser, reader, "PAT CLEAR")
        pat_cmd = (f"PAT NOISE _calnoise {args.duration_ms} "
                   f"{args.volume} {args.shape}")
        ack = send_ack(ser, reader, pat_cmd)
        if not ack or not ack.startswith("OK"):
            print(f"FAIL: PAT NOISE not accepted ({ack})", file=sys.stderr)
            return 2

        send_ack(ser, reader, "PAT SELECT 0")
        send_ack(ser, reader, "SET REPEATS 1")

        print(f"\nrecording {args.duration_ms} ms PRBS noise @ vol={args.volume}% "
              f"(filter {'ON' if args.filter_on else 'OFF'})...")
        send(ser, "RUN")
        started = wait_for_prefix(reader, "OK RUN started", timeout=3.0)
        if started is None:
            print("FAIL: RUN did not start", file=sys.stderr)
            return 2

        # Read one frame (RUN was set to 1 repeat).
        frame = wait_one_frame(reader, timeout=args.duration_ms / 1000.0 + 5.0)
        if frame is None:
            print("FAIL: no ICHP frame received", file=sys.stderr)
            return 2
        if not frame.crc_ok:
            print("warning: CRC mismatch on received frame", file=sys.stderr)
        # Drain RUN done message.
        wait_for_prefix(reader, "OK RUN done", timeout=2.0)

        save_wav_int16(frame.samples, frame.rate_hz, args.out)
        print(f"\nsaved {args.out}  ({frame.n_samples} samples @ {frame.rate_hz} Hz)")
        return 0
    finally:
        close_board(ser, reader)


# ---------------------------------------------------------------------------
# Subcommand: analyze
# ---------------------------------------------------------------------------

def _compute_avg_fft(samples_f, sample_rate: int, n_fft: int = 4096):
    """Time-averaged magnitude FFT (Hann-windowed, dropping tail)."""
    import numpy as np
    n_chunks = len(samples_f) // n_fft
    mag_avg = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    if n_chunks == 0:
        return np.fft.rfftfreq(n_fft, d=1.0 / sample_rate), mag_avg
    hann = np.hanning(n_fft).astype(np.float32)
    for i in range(n_chunks):
        chunk = samples_f[i * n_fft : (i + 1) * n_fft]
        mag = np.abs(np.fft.rfft(chunk * hann))
        mag_avg += mag
    mag_avg /= n_chunks
    return np.fft.rfftfreq(n_fft, d=1.0 / sample_rate), mag_avg


def _save_fft_plot(wav_path: Path, samples_f, sample_rate: int, out_path: Path) -> None:
    import numpy as np
    import matplotlib.pyplot as plt
    freqs, mag = _compute_avg_fft(samples_f, sample_rate)
    mag_db = 20.0 * np.log10(np.maximum(mag, 1e-10))
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogx(freqs, mag_db, linewidth=0.8)
    ax.set_xlim(20, sample_rate / 2)
    floor = np.percentile(mag_db, 5) - 5
    ceil_ = np.percentile(mag_db, 99) + 5
    ax.set_ylim(floor, ceil_)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB, arbitrary ref)")
    ax.set_title(f"FFT — {wav_path.name}")
    ax.grid(True, which="both", alpha=0.3)
    plt.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _save_spectrogram_plot(wav_path: Path, samples_f, sample_rate: int,
                           out_path: Path) -> None:
    import numpy as np
    import matplotlib.pyplot as plt
    from scipy import signal
    f, t, Sxx = signal.spectrogram(samples_f, fs=sample_rate,
                                   nperseg=1024, noverlap=512)
    Sxx_db = 10.0 * np.log10(np.maximum(Sxx, 1e-10))
    fig, ax = plt.subplots(figsize=(10, 5))
    im = ax.pcolormesh(t, f, Sxx_db, shading="gouraud", cmap="magma")
    ax.set_yscale("log")
    ax.set_ylim(50, sample_rate / 2)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title(f"Spectrogram — {wav_path.name}")
    fig.colorbar(im, ax=ax, label="dB")
    plt.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def cmd_analyze(args) -> int:
    """Generate FFT + spectrogram PNGs for a WAV."""
    try:
        import numpy as np  # noqa: F401
        import matplotlib
        matplotlib.use("Agg")
    except ImportError as e:
        print(f"FAIL: missing dependency ({e}). Install with: uv sync --extra training",
              file=sys.stderr)
        return 2

    rate, samples = load_wav(args.wav)
    import numpy as np
    samples_f = samples.astype(np.float32) / 32768.0

    out_dir = args.out_dir if args.out_dir else args.wav.parent / args.wav.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    fft_path = out_dir / "fft.png"
    spec_path = out_dir / "spectrogram.png"

    _save_fft_plot(args.wav, samples_f, rate, fft_path)
    _save_spectrogram_plot(args.wav, samples_f, rate, spec_path)
    print(f"saved {fft_path}")
    print(f"saved {spec_path}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: design-filter
# ---------------------------------------------------------------------------

def _peaking_biquad(f0: float, Q: float, gain_db: float, fs: int):
    """RBJ Audio EQ Cookbook peaking biquad. Returns (b, a) with a0 = 1."""
    import numpy as np
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / fs
    cosw0 = np.cos(w0)
    sinw0 = np.sin(w0)
    alpha = sinw0 / (2.0 * Q)
    b0 = 1.0 + alpha * A
    b1 = -2.0 * cosw0
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cosw0
    a2 = 1.0 - alpha / A
    return [b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0]


def cmd_design_filter(args) -> int:
    """Design an N-stage peaking-EQ biquad cascade from a raw recording.

    Approach: compute the time-averaged magnitude spectrum, smooth it with
    a 1/3-octave-ish median filter to get the "in-band average" reference,
    and place a peaking biquad at each of the N worst dips. Gain per stage
    is clamped to ``--max-gain-db``.
    """
    try:
        import numpy as np
        from scipy import signal as sps  # noqa: F401
    except ImportError as e:
        print(f"FAIL: missing dependency ({e}). Install with: uv sync --extra training",
              file=sys.stderr)
        return 2

    rate, samples = load_wav(args.raw_wav)
    samples_f = samples.astype(np.float32) / 32768.0
    freqs, mag = _compute_avg_fft(samples_f, rate)
    if mag.max() <= 0:
        print("FAIL: input WAV is silent or too short", file=sys.stderr)
        return 2
    mag_db = 20.0 * np.log10(np.maximum(mag, 1e-10))

    # Smoothed reference (running median over ~21 bins ≈ 80 Hz at 4096-point FFT @ 16 kHz)
    from scipy.signal import medfilt
    smoothed_db = medfilt(mag_db, kernel_size=21)
    boost_db = smoothed_db - mag_db  # positive = lift this frequency

    # ROI: SPK has practical output 200 Hz – 6 kHz (BOM speaker spec). Don't
    # bother boosting outside this band — SPK can't deliver and we'd
    # amplify quantization noise.
    f_min, f_max = args.f_min, args.f_max
    in_band = (freqs >= f_min) & (freqs <= f_max)
    boost_in_band = np.where(in_band, boost_db, -np.inf)

    # Greedy peak picking with min frequency spacing.
    n_stages = args.stages
    min_log_gap = np.log(args.min_freq_ratio)  # natural log
    peaks: list[tuple[float, float]] = []
    sorted_idx = np.argsort(boost_in_band)[::-1]
    for idx in sorted_idx:
        if len(peaks) >= n_stages:
            break
        b = boost_in_band[idx]
        if not np.isfinite(b) or b < args.min_gain_db:
            continue
        f = float(freqs[idx])
        if any(abs(np.log(f / pf)) < min_log_gap for pf, _ in peaks):
            continue
        gain = min(float(b), args.max_gain_db)
        peaks.append((f, gain))

    # Build coefficients table; pad with identity stages so the JSON always
    # has exactly N entries (firmware-side EQ has 8 stages).
    coefs: list[dict] = []
    for f0, gain_db in peaks:
        b, a = _peaking_biquad(f0, args.Q, gain_db, rate)
        coefs.append({
            "f_hz":    f0,
            "Q":       args.Q,
            "gain_db": gain_db,
            "b0":      b[0], "b1": b[1], "b2": b[2],
            "a1":      a[1], "a2": a[2],
        })
    while len(coefs) < n_stages:
        coefs.append({
            "f_hz": 0.0, "Q": 0.0, "gain_db": 0.0,
            "b0": 1.0, "b1": 0.0, "b2": 0.0,
            "a1": 0.0, "a2": 0.0,
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fp:
        json.dump({
            "source":           str(args.raw_wav),
            "sample_rate_hz":   int(rate),
            "n_stages":         n_stages,
            "n_active":         len(peaks),
            "design": {
                "max_gain_db":     args.max_gain_db,
                "min_gain_db":     args.min_gain_db,
                "Q":               args.Q,
                "freq_band_hz":    [f_min, f_max],
                "min_freq_ratio":  args.min_freq_ratio,
            },
            "stages":           coefs,
        }, fp, indent=2)

    print(f"designed {len(peaks)} active biquads (rest = identity)")
    for c in coefs:
        if c["f_hz"] > 0:
            print(f"  f={c['f_hz']:6.0f} Hz  Q={c['Q']:.1f}  gain={c['gain_db']:+.1f} dB")
    print(f"saved {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: upload-filter
# ---------------------------------------------------------------------------

def cmd_upload_filter(args) -> int:
    """Send a filter JSON to the board via EQ SET commands."""
    with args.filter_json.open() as fp:
        data = json.load(fp)
    stages = data["stages"]
    if len(stages) != 8:
        print(f"warning: filter has {len(stages)} stages, firmware has 8; "
              f"extra dropped or identity-padded", file=sys.stderr)
    stages = stages[:8]
    while len(stages) < 8:
        stages.append({
            "b0": 1.0, "b1": 0.0, "b2": 0.0, "a1": 0.0, "a2": 0.0,
        })

    ser = None
    reader = None
    try:
        ser, reader = open_board(args.port, args.baud)
        send_ack(ser, reader, "EQ RESET")
        for i, c in enumerate(stages):
            cmd = (f"EQ SET {i} "
                   f"{c['b0']:.6f} {c['b1']:.6f} {c['b2']:.6f} "
                   f"{c['a1']:.6f} {c['a2']:.6f}")
            ack = send_ack(ser, reader, cmd)
            if not ack or not ack.startswith("OK"):
                print(f"FAIL: EQ SET {i} not accepted ({ack})", file=sys.stderr)
                return 2

        if args.enable:
            send_ack(ser, reader, "EQ ENABLE")
            print(f"\nuploaded {len(stages)} stages and EQ enabled")
        else:
            print(f"\nuploaded {len(stages)} stages (EQ stays disabled; "
                  f"run 'EQ ENABLE' to activate)")
        return 0
    finally:
        close_board(ser, reader)


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------

def cmd_compare(args) -> int:
    """Side-by-side spectrogram + FFT overlay of two WAVs."""
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from scipy import signal
    except ImportError as e:
        print(f"FAIL: missing dependency ({e}). Install with: uv sync --extra training",
              file=sys.stderr)
        return 2

    rate1, s1 = load_wav(args.wav1)
    rate2, s2 = load_wav(args.wav2)
    if rate1 != rate2:
        print(f"FAIL: sample rate mismatch ({rate1} Hz vs {rate2} Hz)", file=sys.stderr)
        return 2
    rate = rate1
    s1_f = s1.astype(np.float32) / 32768.0
    s2_f = s2.astype(np.float32) / 32768.0

    out_dir = args.out_dir if args.out_dir else Path("comparison")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Side-by-side spectrograms (shared dB range so they're directly comparable)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    im = None
    for ax, samples, label in zip(axes, [s1_f, s2_f], [args.label1, args.label2]):
        f, t, Sxx = signal.spectrogram(samples, fs=rate, nperseg=1024, noverlap=512)
        Sxx_db = 10.0 * np.log10(np.maximum(Sxx, 1e-10))
        im = ax.pcolormesh(t, f, Sxx_db, shading="gouraud", cmap="magma",
                            vmin=-100, vmax=-20)
        ax.set_yscale("log")
        ax.set_ylim(50, rate / 2)
        ax.set_xlabel("Time (s)")
        ax.set_title(label)
    axes[0].set_ylabel("Frequency (Hz)")
    if im is not None:
        fig.colorbar(im, ax=axes, label="dB", fraction=0.04)
    plt.suptitle("Spectrogram comparison")
    spec_path = out_dir / "spectrogram_compare.png"
    fig.savefig(spec_path, dpi=120, bbox_inches="tight")
    plt.close(fig)

    # Overlaid FFT + diff
    freqs, m1 = _compute_avg_fft(s1_f, rate)
    _, m2     = _compute_avg_fft(s2_f, rate)
    m1_db = 20.0 * np.log10(np.maximum(m1, 1e-10))
    m2_db = 20.0 * np.log10(np.maximum(m2, 1e-10))
    diff_db = m2_db - m1_db

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axes[0].semilogx(freqs, m1_db, label=args.label1, linewidth=0.8, alpha=0.85)
    axes[0].semilogx(freqs, m2_db, label=args.label2, linewidth=0.8, alpha=0.85)
    axes[0].set_xlim(20, rate / 2)
    axes[0].set_ylabel("Magnitude (dB)")
    axes[0].grid(True, which="both", alpha=0.3)
    axes[0].legend()
    axes[0].set_title("Average-FFT overlay")

    axes[1].semilogx(freqs, diff_db, color="C2", linewidth=0.8)
    axes[1].axhline(0, color="k", linewidth=0.5, alpha=0.5)
    axes[1].set_xlim(20, rate / 2)
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel(f"Diff (dB)\n{args.label2} − {args.label1}")
    axes[1].grid(True, which="both", alpha=0.3)
    plt.tight_layout()
    fft_path = out_dir / "fft_compare.png"
    fig.savefig(fft_path, dpi=120)
    plt.close(fig)

    print(f"saved {spec_path}")
    print(f"saved {fft_path}")

    # Quick text summary over the SPK in-band region (200–6000 Hz).
    mask = (freqs >= 200) & (freqs <= 6000)
    if mask.any():
        in_band = diff_db[mask]
        print(f"\nIn-band (200–6000 Hz) {args.label2} − {args.label1} summary:")
        print(f"  mean diff:   {float(in_band.mean()):+.2f} dB")
        print(f"  std diff:    {float(in_band.std()):.2f} dB")
        print(f"  max boost:   {float(in_band.max()):+.2f} dB")
        print(f"  max cut:     {float(in_band.min()):+.2f} dB")
    return 0


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibrator",
        description="IchiPing SPK/mic calibration helper (non-interactive)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # record
    sp = sub.add_parser("record", help="emit noise + capture WAV")
    sp.add_argument("--port", required=True)
    sp.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    sp.add_argument("--out", type=Path, required=True, help="output WAV path")
    sp.add_argument("--duration-ms", type=int, default=DEFAULT_DURATION_MS,
                    dest="duration_ms")
    sp.add_argument("--volume", type=int, default=DEFAULT_VOLUME_PCT,
                    help="0..100 percent (default 30, keep low to avoid SPK saturation)")
    sp.add_argument("--shape", type=int, default=0,
                    help="0 = PRBS (default, crest 0 dB), 1 = uniform")
    sp.add_argument("--filter-on", action="store_true",
                    help="enable EQ during recording (default: EQ DISABLED — "
                         "required for calibration measurements)")
    sp.set_defaults(func=cmd_record)

    # analyze
    sp = sub.add_parser("analyze", help="FFT + spectrogram PNG of a WAV")
    sp.add_argument("wav", type=Path)
    sp.add_argument("--out-dir", type=Path, default=None,
                    help="output directory (default: <wav stem>/)")
    sp.set_defaults(func=cmd_analyze)

    # design-filter
    sp = sub.add_parser("design-filter",
                        help="design EQ from raw WAV → JSON consumable by upload-filter")
    sp.add_argument("raw_wav", type=Path)
    sp.add_argument("--out", type=Path, required=True, help="output JSON path")
    sp.add_argument("--stages", type=int, default=8,
                    help="number of biquad stages (default 8 = firmware max)")
    sp.add_argument("--max-gain-db", type=float, default=9.0, dest="max_gain_db",
                    help="hard cap on per-stage boost in dB (default 9, "
                         "higher risks SPK distortion)")
    sp.add_argument("--min-gain-db", type=float, default=1.0, dest="min_gain_db",
                    help="ignore dips below this depth (default 1 dB)")
    sp.add_argument("--Q", type=float, default=5.0,
                    help="biquad Q value (default 5; higher = narrower peak)")
    sp.add_argument("--f-min", type=float, default=200.0, dest="f_min")
    sp.add_argument("--f-max", type=float, default=6000.0, dest="f_max")
    sp.add_argument("--min-freq-ratio", type=float, default=1.15,
                    dest="min_freq_ratio",
                    help="peaks must be at least this factor apart in frequency")
    sp.set_defaults(func=cmd_design_filter)

    # upload-filter
    sp = sub.add_parser("upload-filter", help="push filter JSON to board")
    sp.add_argument("filter_json", type=Path)
    sp.add_argument("--port", required=True)
    sp.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    sp.add_argument("--enable", action="store_true",
                    help="run EQ ENABLE after uploading (default: leave disabled)")
    sp.set_defaults(func=cmd_upload_filter)

    # compare
    sp = sub.add_parser("compare", help="side-by-side spectrogram + FFT overlay")
    sp.add_argument("wav1", type=Path)
    sp.add_argument("wav2", type=Path)
    sp.add_argument("--label1", default="raw")
    sp.add_argument("--label2", default="filtered")
    sp.add_argument("--out-dir", type=Path, default=None)
    sp.set_defaults(func=cmd_compare)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
