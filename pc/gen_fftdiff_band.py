"""s00000 vs s00001 の FFT 比較 + FFT 差分 + 差分の「帯カラーチャート」複合図。

レイアウト (縦に 3 段、x 軸=周波数で共有):
  段1: 両 state の平均 FFT (Welch PSD, dB) を重ね描き  — s00000 / s00001
  段2: 差分線 (s00001 - s00000) [dB]、0 基準、塗りつぶし
  段3: その差分を「帯」(1 行ヒートマップ) として発散カラーマップで表示 + カラーバー
       → pc/runs/v1_6_fftdiff/delta_v6_vs_v1_5.png と同じ「色で diff を示す」表現の
         1 状態ペア版。「diff をこの帯カラーチャートで示している」ことが一目で分かる図。

状態ラベルは h 表記 (間取り順、state_labels.py 参照) で表示する。
wav パスのディレクトリ名は s 表記のまま (データ正本は s)。

使い方:
  # 実データ (測定マシンで代表 wav が揃ったら):
  uv run --extra training python gen_fftdiff_band.py \
      --wav0 captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav1 captures/full_32_eval_v1/s00001/frame_000000.wav \
      --label0 h00000 --label1 h01000 \
      --out ../docs/img/fftdiff_band_h00000_vs_h01000.png

  # レイアウト確認用モック (合成データ、実測ではない):
  uv run --extra training python gen_fftdiff_band.py --mock \
      --out ../docs/img/fftdiff_band_MOCK.png
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

FMAX_HZ = 8000.0   # 表示上限 (16 kHz / 2)
DIVERGE_CMAP = "RdBu_r"  # 0 = 白背景 (coolwarm は 0 がグレーになるため不採用)
DIVERGE_CLIM = 10.0  # ±dB (gen_fftdiff_band_pair.py と同一基準。小変化の視認性優先)


def load_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return rate, samples


def welch_psd_db(samples_f: np.ndarray, rate: int):
    from scipy import signal
    f, pxx = signal.welch(samples_f, fs=rate, nperseg=1024, noverlap=512)
    return f, 10.0 * np.log10(np.maximum(pxx, 1e-12))


def composite(freqs, psd0_db, psd1_db, title, out_path: Path,
              label0="h00000", label1="h01000"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    m = freqs <= FMAX_HZ
    freqs = freqs[m]
    psd0_db = psd0_db[m]
    psd1_db = psd1_db[m]
    diff = psd1_db - psd0_db

    fig = plt.figure(figsize=(10, 8))
    # 右列はカラーバー専用 (最下段のみ使用)。主要 3 軸の幅を揃えて x 軸を整列させる
    gs = fig.add_gridspec(3, 2, height_ratios=[3, 2, 0.8],
                          width_ratios=[1, 0.02], hspace=0.30, wspace=0.04)

    # 段1: FFT 重ね描き
    ax0 = fig.add_subplot(gs[0, 0])
    ax0.plot(freqs, psd0_db, lw=0.8, color="#1f77b4", label=label0)
    ax0.plot(freqs, psd1_db, lw=0.8, color="#ff7f0e", label=label1, alpha=0.85)
    ax0.set_ylabel("PSD (dB)")
    ax0.set_title(title)
    ax0.legend(loc="upper right")
    ax0.grid(True, alpha=0.3)
    ax0.set_xlim(0, FMAX_HZ)

    # 段2: 差分線 (= noise_diff 特徴量)
    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax1.axhline(0, color="k", lw=0.6)
    ax1.plot(freqs, diff, lw=0.8, color="#7f2fa0", label=f"{label1} diff")
    ax1.fill_between(freqs, diff, 0, where=diff >= 0, color="#d62728", alpha=0.35)
    ax1.fill_between(freqs, diff, 0, where=diff < 0, color="#1f5fd6", alpha=0.35)
    ax1.set_ylabel(f"Δ PSD (dB)\nvs {label0}")
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, FMAX_HZ)

    # 段3: diff の帯カラーチャート (1 行ヒートマップ、y 方向一様)
    ax2 = fig.add_subplot(gs[2, 0], sharex=ax0)
    band = diff[np.newaxis, :]
    im = ax2.imshow(band, aspect="auto", cmap=DIVERGE_CMAP,
                    vmin=-DIVERGE_CLIM, vmax=DIVERGE_CLIM,
                    extent=[0, FMAX_HZ, 0, 1], interpolation="nearest")
    ax2.set_yticks([])
    ax2.set_xlabel("Frequency (Hz)")
    ax2.set_ylabel("diff band")
    cax = fig.add_subplot(gs[2, 1])
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("Δ PSD (dB)")

    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


def make_mock():
    """合成 PSD でレイアウトを確認するためのモック (実測ではない)。

    h00000 = フラット白色雑音が室共鳴で色付いた想定 (緩い包絡 + 数本の共鳴ピーク)。
    h01000 = h00000 とほぼ同じだが、扉1枚開で一部のモードが移動/増減した想定。
    → diff は大部分 0 付近、局所的にだけ立つ = 「特徴だけ分離」を表現。
    振幅は実測の 1 ビット状態差 (diff ±10 dB 級) に合わせ、±20 dB 色域でも
    帯カラーチャートに色が乗るスケールにしてある。
    """
    freqs = np.linspace(0, 8000, 1024)
    rng = np.random.default_rng(12345)

    def resonances(peaks):
        y = -6.0 * (freqs / 8000.0)  # 緩い高域ロールオフ
        for f0, amp, q in peaks:
            y += amp * np.exp(-0.5 * ((freqs - f0) / (f0 / q)) ** 2)
        y += rng.normal(0, 0.8, freqs.shape)  # 測定ばらつき相当
        return y

    base = [(450, 12, 12), (1300, 11, 16), (2600, 8, 18), (4200, 10, 14), (6100, 7, 20)]
    psd0 = resonances(base)
    # h01000: 1300Hz のモードが弱まり、3000Hz 付近に新たなモードが出る想定
    mod = [(450, 12, 12), (1300, 3.0, 16), (2600, 8, 18),
           (3000, 10.0, 22), (4200, 10, 14), (6100, 7, 20)]
    psd1 = resonances(mod)
    return freqs, psd0, psd1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav0", type=Path, help="s00000 の wav")
    ap.add_argument("--wav1", type=Path, help="s00001 の wav")
    ap.add_argument("--mock", action="store_true", help="合成データでレイアウト確認")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label0", default="h00000")
    ap.add_argument("--label1", default="h01000")
    args = ap.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.mock:
        freqs, psd0, psd1 = make_mock()
        title = ("LAYOUT MOCK — synthetic data, NOT measured  |  "
                 "FFT compare + diff + diff-band colorchart")
        composite(freqs, psd0, psd1, title, args.out,
                  label0=args.label0, label1=args.label1)
        return 0

    if not (args.wav0 and args.wav1):
        print("error: --wav0 と --wav1 (または --mock) が必要", file=sys.stderr)
        return 2
    r0, s0 = load_wav(args.wav0)
    r1, s1 = load_wav(args.wav1)
    f0, p0 = welch_psd_db(s0, r0)
    _, p1 = welch_psd_db(s1, r1)
    title = f"FFT compare + diff + diff-band  |  {args.label1} vs {args.label0}"
    composite(f0, p0, p1, title, args.out, label0=args.label0, label1=args.label1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
