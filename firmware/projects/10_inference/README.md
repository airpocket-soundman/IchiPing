# 10_inference — NN 推論オンデバイス firmware (v1 本実装)

[09_collector](../09_collector/README.md) で集めた v1234+ambient 学習データから訓練した **Neutron arch XL 32-class モデル (108 KB INT8 TFLite, NPU 比率 89%)** を MCXN947 上で実行する推論専用ファーム。

09 と同じハード基板 (SAI1 全二重 + ILI9341 + PCA9685 サーボ) を流用し、サーボはテスト用に PC コマンドから動かせる。推論本体は CMSIS-DSP rFFT + NXP TFLite Micro + Neutron NPU。

PC 側対向クライアントは [`pc/inference_client.py`](../../../pc/inference_client.py)。

## 推論パイプライン

```
INFER コマンド受信
 └ pattern 再生 + 録音 (16 kHz × 2 s = 32000 sample, SAI1 全二重)
 └ Welch log-mag PSD (2048-pt Hann, 50% overlap, 30 segment) — CMSIS-DSP arm_rfft_fast_f32
 └ baseline 引き (factory_baseline.h or live RAM の 1024 float から per-bin で減算)
 └ INT8 量子化 (TFLite 入力 scale=0.185412, zero_point=29)
 └ TFLite Micro Invoke (Neutron NPU op が 8/9 op を NPU 実行、~2 ms 想定)
 └ INT8 logits 32 → argmax → 5-bit ABCDE door 状態 decode
 └ ASCII RESULT 行を UART に送信 + TFT 表示
```

## コマンド体系

[`firmware/shared/include/ichp_cmd.h`](../../shared/include/ichp_cmd.h) に追加された推論系 verb 全部:

| verb | 動作 |
|---|---|
| `INFER` | 1 回推論 → `RESULT seq=N state=sABCDE state_idx=N cls=A1 baseline=factory argmax_q=87 ...` |
| `INFER STREAM <N>` | N 回連続推論 (途中で `STOP` 可) |
| `STOP` | INFER STREAM 中断 |
| `BL STATUS` | 現在 baseline モード + 校正状態 |
| `BL FACTORY` | factory (noise_low ハードコード) に切替 |
| `BL LIVE` | RAM 上の live 校正値に切替 (要 CALIBRATE 済) |
| `BL CALIBRATE [N]` | 静粛時に N frame (default 10) 録音 → 平均 → live baseline |
| `BL CLEAR` | live 破棄 → factory に戻す |

09 系のサーボ操作系 (`OPEN/CLOSE/OPEN ALL/CLOSE ALL/SERVO/SET HOME/SET OPEN`) と pattern 管理 (`PAT_*/EMIT`) はそのまま使える。`RUN` と `SET PIN` 系はファームを軽くするため意図的に拒否。

## RESULT 行フォーマット

```
RESULT seq=42 cls32_idx=9 cls32_state=s10010 cls14=B2
       second32_idx=11 second32_state=s11010
       baseline=factory argmax_q=87 second_q=12 margin=75
       infer_us=2100 cap_ms=2010
       doors a=1 b=0 c=0 AB=1 BC=0
```

**32cls (raw argmax) と 14cls (正規等価クラス) を両方明示**。32cls 値も常に出すので「実際に NN が出した生の判定」と「14 等価クラス縮約」が同時に追える。

- `cls32_idx` : 32-class argmax (0..31)、NN の生 output
- `cls32_state` : 同じ値を 5-bit ABCDE で文字列化 (a, b, c, AB, BC)
- `cls14` : 正規 14 等価クラス名 (A1/A2/B1..B4/C1..C8)、firmware 側で `class_of_14` 算出 (pc/training/dataset.py の `class_of` と完全一致)
- `second32_idx` / `second32_state` : 2 位候補。物理的にどの「真隣」と迷ったかが見える
- `argmax_q` / `second_q` : INT8 logit の 1 位/2 位値、`margin = argmax_q - second_q` が信頼度の代理
- `baseline` : 推論時に使った baseline (factory / live)
- `doors a=..AB=..` : `cls32_state` を冗長に bit 単位で展開 (CSV ログ整形しやすいよう)

PC client は firmware の `cls14` を信頼しつつ、`cls32_idx` から自力で再計算した値と一致するか毎回検算する。divergence があれば `cls14=B2[MCU=A1!]` のように可視化される (firmware と PC の dataset.py 規約ズレ検出用)。

## Baseline 切替

| モード | 出元 | 永続性 |
|---|---|---|
| **factory** (default) | [`source/factory_baseline.h`](source/factory_baseline.h) (eval_noise_low/s00000 10 frame 平均、INT8 量子化済み) | ROM 焼込み |
| **live** | `BL CALIBRATE N` で実環境 N frame 録音 → Welch 平均 | RAM のみ、電源切で消去 |

実運用想定: `pc/inference_client.py` 起動 → `BL CALIBRATE 10` で 20 秒静粛して校正 → `BL LIVE` → `INFER STREAM`。ノイズ環境が変わったら再 calibrate。`BL CLEAR` で factory に戻せる。

## TFT 表示レイアウト

```
┌──────────────────────────────────────────┐
│ IchiPing infer                           │  ヘッダ (NAVY)
├──────────────────────────────────────────┤
│ seq    42                                │  推論連番
│ s10010                                   │  32cls state ABCDE (大字, 橙)
│ idx=9/32                                 │  32cls 整数値 (灰)
│ cls14=B2                                 │  14cls 正規分類 (緑)
│ factory baseline                         │  baseline モード (cyan)
│ 2100 us                                  │  推論時間 (灰)
└──────────────────────────────────────────┘
```

### 推論結果評価バナー（下段）

画面下段（y≈175〜221）に、トグルスイッチで設定した真状態と推論結果を突き合わせた
3 段階の評価を、色付き矩形＋白文字のバナーで表示する。EXEC ボタンでの推論後に確定し、
トグルや扉操作で物理状態が変わると推論結果は無効化されバナーは消える（`verdict` 変化時のみ再描画）。

| 判定 | 条件 | 矩形色（文字は白） |
|---|---|---|
| Complete Success | 32cls 完全一致（5 bit すべて一致） | 青 `ILI9341_BLUE` |
| Conditional Success | 14cls 等価クラスのみ一致（扉で遮断され観測不能な変数の取り違えは許容） | 緑 `ILI9341_GREEN` |
| Failure | 14cls も不一致 | 赤 `ILI9341_RED` |

## 配線

09_collector と完全に同一。サーボもテスト用に使うので **PCA9685 も接続必須**:

| 信号 | ピン | 備考 |
|---|---|---|
| SAI1 BCLK / FS / TXD / RXD | J1.1 / J1.11 / J1.5 / J1.15 | 08/09 と同じ |
| OpenSDA UART | LPUART4 (**921600 bps**) | コマンド + RESULT |
| ILI9341 TFT | LPSPI1 + A2..A5 GPIO | 03/09 と同じ |
| LPI2C2 (PCA9685) | D18 / D19 | 02/09 と同じ |
| 5V 外部 | MAX98357A 1 系統 + PCA9685 V+ 1 系統 | 09 と同じ |

## ビルド + SDK 依存

09_collector のビルド設定 (board_files.cmake, prj.conf) を流用。追加で **TFLite Micro + Neutron** middleware を有効化する必要がある:

```cmake
mcux_set_variable(component_cmsis_dsp_lib_GCC true)
mcux_set_variable(component_eiq_tensorflow_lite_micro true)
mcux_set_variable(component_eiq_tensorflow_lite_micro_neutron true)
mcux_set_variable(component_eiq_neutron_lib true)
```

これらは **MCUXpresso SDK + eIQ middleware 拡張パック** に含まれる:

1. MCUXpresso SDK で MCXN947 用 SDK を生成する際、**Middleware → eIQ → TensorFlow Lite for Microcontrollers** と **eIQ → Neutron NPU library** にチェック
2. SDK 内で:
   ```
   middleware/eiq/tensorflow-lite/         # TFLite Micro
   middleware/eiq/tensorflow-lite/third_party/cmsis_nn/
   middleware/eiq/neutron/                  # Neutron driver + op
   ```
3. component 名は SDK バージョンで微妙に変わる。`mcux_set_variable` が認識されない場合は SDK の `middleware/eiq/*/CMakeLists.txt` で実際の component 名を確認

prj.conf 側に Kconfig 経由で有効化する場合は (推奨):
```
CONFIG_MCUX_COMPONENT_middleware.eiq.tensorflow_lite_micro=y
CONFIG_MCUX_COMPONENT_middleware.eiq.tensorflow_lite_micro.neutron=y
CONFIG_MCUX_COMPONENT_middleware.eiq.neutron_lib=y
CONFIG_MCUX_COMPONENT_component.cmsis_dsp_lib=y
```

## メモリ予算 (XL Neutron モデル 108 KB)

| 領域 | 用途 | サイズ |
|---|---|---|
| Flash | `ichp_model_data[108336]` (model_data.h) | 108 KB |
| Flash | `ichp_factory_baseline[1024]` + 推論コード | 約 30 KB |
| SRAM | TFLite tensor arena (静的) | **64 KB** (s_tflite_arena) |
| SRAM | Welch 中間バッファ (Hann/seg/FFT out/accum) | 約 28 KB |
| SRAM | 録音 32000×int16 + 励振 32000×int16 | 128 KB |
| SRAM | features 出力 + INT8 入出力 | 約 5 KB |
| SRAM | live baseline + accum | 8 KB |
| **合計 SRAM** | | **約 233 KB / 384 KB (61%)** |

arena 64 KB は安全寄り。実際は 32 KB で足りる想定。`ichp_tflite_init` が `ICHP_TFLITE_ERR_ALLOC` (-2) を返した場合のみ `INF_TFLITE_ARENA_BYTES` を増やす。

## PC client での使い方

```bash
cd pc

# REPL
uv run python inference_client.py --port COM7

# 単発推論 5 回 + CSV ログ
uv run python inference_client.py --port COM7 --infer 5 --csv runs/infer_log.csv

# 校正 → live → 10 回
uv run python inference_client.py --port COM7 \
    --once "BL CALIBRATE 10" --once "BL LIVE" --once "INFER STREAM 10"

# サーボ動作 + 推論
uv run python inference_client.py --port COM7 --once "OPEN a" --once "INFER"
```

REPL helper:
- `:truth <state|cls14|none>` — 結果と照合するラベル設定 (例: `:truth s10010` で次以降の RESULT に `✓` / `✗`)
- `:csv <path>` / `:csv off` — REPL 中の CSV 取得切替
- `:infer [N]` — INFER STREAM N (default 5) のショートカット
- `:select <name|idx>` — patterns.yaml の名前/インデックスから PAT SELECT
- `:reload` — patterns.yaml 再読込 + MCU 再 push

## 既知の制約 / TODO

- TFLite Micro / Neutron 統合は SDK バージョン依存性が高い。新 SDK で `Register_NEUTRON_GRAPH()` のシンボル名や `Neutron/Driver/include/neutron.h` のパスが変わったら [`firmware/shared/source/ichp_tflite.cpp`](../../shared/source/ichp_tflite.cpp) 冒頭の `#include` を調整
- 現状 calibration ノイズは pattern 再生中の Welch 平均なので、本当の「無音 baseline」ではなく「励振 + 部屋の音響」の合成値。固定環境で運用すれば問題ない (学習時の noise_diff と同じ条件) が、運用前提を意識する
- 5-bit 状態 decode は learning 時と完全一致 (a + b*2 + c*4 + AB*8 + BC*16)。state_idx を 14 等価クラス名に展開するのは PC 側 (`inference_client.state_to_14cls`)
