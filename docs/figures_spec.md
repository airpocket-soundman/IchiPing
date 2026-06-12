# 資料用フィギュア仕様 (figures spec)

作成日: 2026-06-12 / 用途: プレゼン・資料用の図

現時点で v21+ の録音データ本体が開発マシンに無い（測定マシンにのみ存在）ため、
**欲しい図の仕様をここに記録**しておく。録音側データが揃ったら測定マシンで生成する
（手順は [capture_machine_todo.md](capture_machine_todo.md) に連携）。

共通条件: サンプルレート 16 kHz（`ICHP_FEAT_RATE_HZ`）、励振 = `noise_2s_prbs`（PRBS 白色雑音 2 s）。
STFT は `calibrator.py` の `_save_spectrogram_plot` 準拠（nperseg=1024, noverlap=512, y 軸 log, magma）。
FFT は `_save_fft_plot` 準拠（semilogx, 平均スペクトル, dB）。

---

## 図1: ping 白色雑音 vs 00000 録音（室共鳴による色付け）

**狙い**: フラットな白色雑音（ping）が、部屋に入る（マイクで受ける）ことで室共鳴により
周波数ごとに強度が変化し「色付く」様子を示す。

| サブ図 | 左 | 右 | 形式 |
|---|---|---|---|
| 1a STFT 比較 | ping 白色雑音の STFT | s00000（全閉）で採取した同雑音の STFT | 横並び 2 枚 |
| 1b FFT 比較 | ping 白色雑音の FFT（ほぼフラット） | s00000 採取雑音の FFT（室共鳴ピーク/ディップ） | 重ね描き または 横並び |

- 左半分（ping 側）は **今すぐ生成可能**。下記「生成済み」参照。
- 右半分（s00000 録音側）は測定マシンの代表 wav が必要。

## 図2: s00000 vs s00001 の STFT と差分（特徴分離）

**狙い**: 状態差（扉1枚の開閉, s00000→s00001）が STFT 差分として分離抽出でき、
状態固有の特徴だけが残ることを示す（noise_diff 特徴量の妥当性の可視化）。

| サブ図 | 内容 |
|---|---|
| 2a | s00000 の STFT |
| 2b | s00001 の STFT |
| 2c | STFT 差分 `(s00001 − s00000)`（dB 差分、発散カラーマップ推奨: coolwarm / bwr, 0 中心） |

- 全て測定マシンの代表 wav（`frame_000000.wav`）が必要。FFT 差分版も併せて作ると図1b と対比しやすい。

## 図3: s00000 vs s00001 の FFT 比較 + 差分 + 差分の帯カラーチャート

**狙い**: 状態差を「FFT 差分」として示し、さらにその差分を**帯（1 行ヒートマップ）の
カラーチャート**で表現することで、`pc/runs/v1_6_fftdiff/delta_v6_vs_v1_5.png`（32 状態版）と
同じ「色で diff を示す」エンコードを1状態ペアで分かりやすく提示する。

縦 3 段・x 軸=周波数（0–8 kHz 線形）で共有:

| 段 | 内容 |
|---|---|
| 段1 | s00000 / s00001 の平均 FFT（Welch PSD, dB）を重ね描き |
| 段2 | 差分線 `(s00001 − s00000)` dB、0 基準・正負で赤/青塗り |
| 段3 | 段2 の差分を**帯カラーチャート**で表示（発散カラーマップ coolwarm, ±6 dB, 右にカラーバー `Δ PSD (dB)`）。参考図と同じ色域。 |

- 生成スクリプト: `pc/gen_fftdiff_band.py`（`--wav0/--wav1` で実データ、`--mock` でレイアウト確認）
- **レイアウト確認用モック（合成データ・実測ではない）**: `docs/img/fftdiff_band_MOCK.png` を生成済み。
  構図確認用であり、ピーク位置・差分は架空。実データ版で置き換える。
- 実データ版コマンド例:
  ```bash
  cd pc
  uv run --extra training python gen_fftdiff_band.py \
      --wav0 captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav1 captures/full_32_eval_v1/s00001/frame_000000.wav \
      --out ../docs/img/fftdiff_band_s00000_vs_s00001.png
  ```

---

## 生成状況

| 図 | 状態 | 出力 |
|---|---|---|
| ping STFT | ✅ 生成済み | `docs/img/ping_noise_stft.png` |
| ping FFT | ✅ 生成済み | `docs/img/ping_noise_fft.png` |
| 図1 右（s00000 録音）STFT/FFT | ⏳ 測定マシン待ち | — |
| 図2（s00000/s00001 STFT + diff） | ⏳ 測定マシン待ち | — |
| 図3 レイアウトモック（合成） | ✅ 生成済み・**構図承認済み** | `docs/img/fftdiff_band_MOCK.png` |
| 図3 実データ版（FFT 比較+diff+帯） | ⏳ 測定マシン待ち | — |

### ping 図の注意（重要）

ping の正確な送出波形は**ビット単位では再現不可**（firmware の seed が
`(uintptr_t)pattern ^ duration_ms` という実行時アドレス依存）。ただし xorshift32 の
±1 PRBS は**シードに依らず統計的にフラットな白色スペクトル**なので、「元はフラット」を
示す資料目的にはスペクトル的に同一で問題ない。生成スクリプト: `pc/gen_ping_figures.py`。

### 測定マシンで録音側図を作るときの指針

代表 wav が揃ったら（`capture_machine_todo.md` 参照）、例:

```bash
cd pc
# s00000 / s00001 の STFT・FFT（calibrator の analyze を流用）
uv run --extra training python calibrator.py analyze \
    captures/full_32_eval_v1/s00000/frame_000000.wav --out-dir ../docs/img/s00000
uv run --extra training python calibrator.py analyze \
    captures/full_32_eval_v1/s00001/frame_000000.wav --out-dir ../docs/img/s00001
```

STFT 差分（図2c）と ping↔00000 並置（図1）は専用スクリプトを別途用意する
（`gen_ping_figures.py` を雛形に、2 wav を読み込んで Sxx_db の差を pcolormesh する）。
