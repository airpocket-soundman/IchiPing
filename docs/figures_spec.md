# 資料用フィギュア仕様 (figures spec)

作成日: 2026-06-12 / 用途: プレゼン・資料用の図

現時点で v21+ の録音データ本体が開発マシンに無い（測定マシンにのみ存在）ため、
**欲しい図の仕様をここに記録**しておく。録音側データが揃ったら測定マシンで生成する
（手順は [capture_machine_todo.md](capture_machine_todo.md) に連携）。

> **2026-06-12 更新**: 測定マシンで録音側の図も含め**全図生成済み**（下記「生成状況」参照）。
> 代表 wav (`frame_000000.wav`) もコミット済みのため、以後はどのクローンでも再生成可能。

**状態ラベル表記**: 資料図では **h 表記**（間取り順 c BC b AB a、[pc/state_labels.py](../pc/state_labels.py) 参照）を使う。
データディレクトリ（`captures/.../sXXXXX/`）は s 表記（サーボ論理順 a b c AB BC）のまま＝正本。
本ドキュメントの wav パスは s、図の表示名・出力ファイル名は h（例: `s00001` の wav → 表示 `h01000`）。

共通条件: サンプルレート 16 kHz（`ICHP_FEAT_RATE_HZ`）、励振 = `noise_2s_prbs`（PRBS 白色雑音 2 s）。
STFT は `calibrator.py` の `_save_spectrogram_plot` 準拠（nperseg=1024, noverlap=512, y 軸 log, magma）。
FFT は `_save_fft_plot` 準拠（semilogx, 平均スペクトル, dB）。

---

## 図1: ping 白色雑音 vs 00000 録音（室共鳴による色付け）

**狙い**: フラットな白色雑音（ping）が、部屋に入る（マイクで受ける）ことで室共鳴により
周波数ごとに強度が変化し「色付く」様子を示す。

| サブ図 | 左 | 右 | 形式 |
|---|---|---|---|
| 1a STFT 比較 | ping 白色雑音の STFT | h00000（全閉）で採取した同雑音の STFT | 横並び 2 枚 |
| 1b FFT 比較 | ping 白色雑音の FFT（ほぼフラット） | h00000 採取雑音の FFT（室共鳴ピーク/ディップ） | 重ね描き または 横並び |

- 左半分（ping 側）は **今すぐ生成可能**。下記「生成済み」参照。
- 右半分（h00000 録音側）は測定マシンの代表 wav が必要。
- 並置版生成スクリプト: `pc/gen_ping_vs_room.py`（`--wav` に録音 wav を渡す。
  1a = STFT 横並び、1b = FFT 重ね描き・中央値 0 dB 正規化）

## 図2: h00000 vs h01000 の STFT と差分（特徴分離）

**狙い**: 状態差（扉1枚の開閉, h00000→h01000）が STFT 差分として分離抽出でき、
状態固有の特徴だけが残ることを示す（noise_diff 特徴量の妥当性の可視化）。

| サブ図 | 内容 |
|---|---|
| 2a | h00000 の STFT |
| 2b | h01000 の STFT |
| 2c | STFT 差分 `(h01000 − h00000)`（dB 差分、発散カラーマップ RdBu_r, 0 中心 = 白背景） |

- 全て測定マシンの代表 wav（`frame_000000.wav`）が必要。FFT 差分版も併せて作ると図1b と対比しやすい。
- 生成スクリプト: `pc/gen_stft_diff.py`（2a/2b は共通カラースケール、2c は coolwarm 0 中心・
  色域は |diff| の 99 パーセンタイルから対称に自動決定）

## 図3: h00000 vs h01000 の FFT 比較 + 差分 + 差分の帯カラーチャート

**狙い**: 状態差を「FFT 差分」として示し、さらにその差分を**帯（1 行ヒートマップ）の
カラーチャート**で表現することで、`pc/runs/v1_6_fftdiff/delta_v6_vs_v1_5.png`（32 状態版）と
同じ「色で diff を示す」エンコードを1状態ペアで分かりやすく提示する。

縦 3 段・x 軸=周波数（0–8 kHz 線形）で共有:

| 段 | 内容 |
|---|---|
| 段1 | h00000 / h01000 の平均 FFT（Welch PSD, dB）を重ね描き |
| 段2 | 差分線 `(h01000 − h00000)` dB、0 基準・正負で赤/青塗り |
| 段3 | 段2 の差分を**帯カラーチャート**で表示（発散カラーマップ RdBu_r 0=白, ±10 dB, 右にカラーバー `Δ PSD (dB)`）。全図共通基準。 |

- 生成スクリプト: `pc/gen_fftdiff_band.py`（`--wav0/--wav1` で実データ、`--mock` でレイアウト確認）
- **レイアウト確認用モック（合成データ・実測ではない）**: `docs/img/fftdiff_band_MOCK.png` を生成済み。
  構図確認用であり、ピーク位置・差分は架空。実データ版で置き換える。
- 実データ版コマンド例:
  ```bash
  cd pc
  uv run --extra training python gen_fftdiff_band.py \
      --wav0 captures/full_32_eval_v1/s00000/frame_000000.wav \
      --wav1 captures/full_32_eval_v1/s00001/frame_000000.wav \
      --label0 h00000 --label1 h01000 \
      --out ../docs/img/fftdiff_band_h00000_vs_h01000.png
  ```

---

## 生成状況

| 図 | 状態 | 出力 |
|---|---|---|
| ping STFT | ✅ 生成済み | `docs/img/ping_noise_stft.png` |
| ping FFT | ✅ 生成済み | `docs/img/ping_noise_fft.png` |
| 図1 右（h00000 録音）STFT/FFT | ✅ 生成済み（2026-06-12, eval_v1 実データ） | `docs/img/h00000/{spectrogram,fft}.png`（h01000 も同様） |
| 図1 並置版（1a STFT / 1b FFT） | ✅ 生成済み（2026-06-12） | `docs/img/ping_vs_h00000_{stft,fft}.png` |
| 図2（h00000/h01000 STFT + diff） | ✅ 生成済み（2026-06-12） | `docs/img/stft_diff_h00000_vs_h01000.png` |
| 図3 レイアウトモック（合成） | ✅ 生成済み・**構図承認済み** | `docs/img/fftdiff_band_MOCK.png` |
| 図3 実データ版（FFT 比較+diff+帯） | ✅ 生成済み（2026-06-12, eval_v1 実データ） | `docs/img/fftdiff_band_h00000_vs_h01000.png` |

実データはすべて `pc/captures/full_32_eval_v1/s0000{0,1}/frame_000000.wav`（コミット済み代表 wav）。

### ping 図の注意（重要）

ping の正確な送出波形は**ビット単位では再現不可**（firmware の seed が
`(uintptr_t)pattern ^ duration_ms` という実行時アドレス依存）。ただし xorshift32 の
±1 PRBS は**シードに依らず統計的にフラットな白色スペクトル**なので、「元はフラット」を
示す資料目的にはスペクトル的に同一で問題ない。生成スクリプト: `pc/gen_ping_figures.py`。

### 測定マシンで録音側図を作るときの指針

代表 wav が揃ったら（`capture_machine_todo.md` 参照）、例:

```bash
cd pc
# h00000 / h01000 の STFT・FFT（calibrator の analyze を流用、出力先は h 表記）
uv run --extra training python calibrator.py analyze \
    captures/full_32_eval_v1/s00000/frame_000000.wav --out-dir ../docs/img/h00000
uv run --extra training python calibrator.py analyze \
    captures/full_32_eval_v1/s00001/frame_000000.wav --out-dir ../docs/img/h01000
```

STFT 差分（図2c）は `pc/gen_stft_diff.py`、ping↔00000 並置（図1）は
`pc/gen_ping_vs_room.py` で生成する（どちらも 2026-06-12 に用意・生成済み）:

```bash
cd pc
uv run --extra training python gen_stft_diff.py \
    --wav0 captures/full_32_eval_v1/s00000/frame_000000.wav \
    --wav1 captures/full_32_eval_v1/s00001/frame_000000.wav \
    --label0 h00000 --label1 h01000 \
    --out ../docs/img/stft_diff_h00000_vs_h01000.png
uv run --extra training python gen_ping_vs_room.py \
    --wav captures/full_32_eval_v1/s00000/frame_000000.wav --label h00000 \
    --out-stft ../docs/img/ping_vs_h00000_stft.png \
    --out-fft ../docs/img/ping_vs_h00000_fft.png
```
