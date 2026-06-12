"""ping (PRBS 白色雑音) の STFT / FFT 図を生成する。

firmware/shared/source/pattern_lib.c の render_noise (PATTERN_NOISE_SHAPE_PRBS)
を厳密に複製して、実機が送出するのと同じ ±1 二値 PRBS 白色雑音を 16 kHz / 2 s で
生成し、STFT (spectrogram) と FFT を docs/img/ に保存する。

注意: 実機の正確な波形は再現できない (seed = pattern ポインタ番地 ^ duration_ms
という実行時アドレス依存)。ただし xorshift32 の ±1 PRBS はシードに依らず統計的に
フラットな白色スペクトルなので、「元のホワイトノイズはフラット」を示す資料図としては
スペクトル的に完全同一で十分。図のタイトルにもその旨を明記する。

使い方:
    cd pc
    uv run --extra training python gen_ping_figures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# calibrator の描画ヘルパを再利用 (録音側の解析図と体裁を揃える)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibrator import _save_fft_plot, _save_spectrogram_plot  # noqa: E402

SAMPLE_RATE_HZ = 16000           # ICHP_FEAT_RATE_HZ
DURATION_MS = 2000               # noise_2s_prbs
SEED = 0xC0FFEE                  # firmware の seed==0 フォールバック値を流用 (任意)


def xorshift32_prbs(n: int, seed: int = SEED) -> np.ndarray:
    """firmware pattern_lib.c の xorshift32 + PRBS(±1) を厳密複製。

    out[i] = (xorshift32() & 0x80000000) ? +1 : -1
    最上位ビットで ±1 を決める二値ノイズ (crest factor 0 dB)。
    """
    s = np.uint32(seed)
    out = np.empty(n, dtype=np.float32)
    mask = np.uint32(0x80000000)
    for i in range(n):
        x = s
        x ^= np.uint32(x << np.uint32(13))
        x ^= np.uint32(x >> np.uint32(17))
        x ^= np.uint32(x << np.uint32(5))
        s = x
        out[i] = 1.0 if (x & mask) else -1.0
    return out


def main() -> int:
    n = SAMPLE_RATE_HZ * DURATION_MS // 1000
    print(f"generating {n} samples ({DURATION_MS} ms @ {SAMPLE_RATE_HZ} Hz) PRBS ping...")
    samples_f = xorshift32_prbs(n)

    out_dir = Path(__file__).resolve().parents[1] / "docs" / "img"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ヘルパはタイトルに wav_path.name を使うだけなので、説明的な名前を渡す
    label = Path("ping_noise_2s_prbs (PRBS white noise, spectrally representative)")
    fft_path = out_dir / "ping_noise_fft.png"
    spec_path = out_dir / "ping_noise_stft.png"

    _save_fft_plot(label, samples_f, SAMPLE_RATE_HZ, fft_path)
    _save_spectrogram_plot(label, samples_f, SAMPLE_RATE_HZ, spec_path)
    print(f"saved {fft_path}")
    print(f"saved {spec_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
