# IchiPing v12345 — モデル進化 / 環境汎化 / 校正運用 検証レポート

## 0. 要約 (TL;DR)

1. **当初の懸念「変換劣化」は否定された**。PC PyTorch FP32 / PINTO INT8 TFLite / neutron-converter optimize 後 TFLite いずれも eval_quiet 100% で一致。MCU 実機との残差は 学習データ × 現環境のミスマッチに起因 (環境汎化問題)。
2. **対処は学習データに現環境 v5 を追加するだけで足りた**。MCU 実機 32cls 精度は 59% (v1234) → 88% (v12345 10f) → **100%** (v12345 50f) と階段状に改善。
3. **校正 (BL CALIBRATE + LIVE) は精度を絞り出す追加要素であり必須ではない**。v12345_50f は factory baseline のまま 14cls=**100%**、32cls=66%。LIVE 校正で 32cls=**100%** に到達。
4. **ノイズ環境でもノイズ下で校正すれば 32cls=100%、14cls=100% を維持**。LIVE 校正がノイズ床を取り込んで差分計算で打ち消すため、騒音環境でも実用可。
5. **Baseline jittering augmentation で校正不要の 32cls=88% を達成**。学習時に「同じ録音を 5 種類の baseline で diff した 5 サンプル」と見なすことで (36800 sample) baseline 不変性をモデルに焼き込み、factory モードのまま **+22pp 改善** (v12345_50f_factory 66% → v12345_BLJIT_factory 88%)。
6. **推論時間は全モデル 1.89 ms** — Neutron NPU 上で安定して 100% NPU 比率 (7/7 op) を維持。

## 1. 比較対象モデル

8 通りの (モデル, baseline モード, 環境) 組合せを同一ハードで MCU 実機計測:

| 名称 | 学習データ | baseline モード | 環境 |
|---|---|---|---|
| v1234_factory                | v1+v2+v3+v4 (5760 sample) | factory (固定、eval_noise_low 由来) | 静粛 |
| v1234_live                   | 同上                     | LIVE (現環境 10 frame 校正)         | 静粛 |
| v12345_10f_live              | v1+v2+v3+v4+**v5 (10 frame/state)** | LIVE                       | 静粛 |
| v12345_50f_factory           | v1+v2+v3+v4+**v5 (50 frame/state)** | factory                    | 静粛 |
| v12345_50f_live              | 同上                     | LIVE                                 | 静粛 |
| v12345_50f_live_noiselow     | 同上                     | LIVE (ノイズ下で校正)               | **noise_low (TV 等継続音)** |
| **v12345_BLJIT_factory**     | v12345 7360 sample × **5 baselines = 36800 sample** (baseline jittering aug) | factory | 静粛 |
| **v12345_BLJIT_live**        | 同上                     | LIVE                                 | 静粛 |

## 2. 結果サマリ

| モデル | 32cls accuracy | 14cls accuracy | mean margin |
|---|---|---|---|
| v1234_factory | 25.0% (8/32) | 46.9% (15/32) | 11.4 |
| v1234_live | 59.4% (19/32) | 96.9% (31/32) | 19.8 |
| v12345_10f_live | 87.5% (28/32) | 100.0% (32/32) | 32.6 |
| v12345_50f_factory | 65.6% (21/32) | 100.0% (32/32) | 20.8 |
| v12345_50f_live | 100.0% (32/32) | 100.0% (32/32) | 47.4 |
| v12345_50f_live_noiselow | 100.0% (32/32) | 100.0% (32/32) | 47.8 |
| v12345_BLJIT_factory | 87.5% (28/32) | 100.0% (32/32) | 36.4 |
| v12345_BLJIT_live | 100.0% (32/32) | 100.0% (32/32) | 56.2 |

![](figs/accuracy_bars.png)

**読み解き**:
- 校正で 25%→59% (32cls 34pp 向上)
- 現環境 10 frame 追加で 59%→88% (32cls 29pp 向上)
- 現環境 50 frame に増量で 88%→100% (32cls 12pp 向上)
- 14cls は v12345_50f なら校正無しでも **100%**

## 3. 状態別 正解状況

緑 ✓ = 32cls 完全一致 / 黄 ≈ = 14cls 等価のみ一致 / 赤 ✗ = 14cls 不一致

![](figs/per_state_correct.png)

v12345_50f_live (最下行) は全 32 状態 緑。v12345_50f_factory も 14cls 等価レベルでは全黄以上、赤 (14cls 外し) が消えていることが見て取れる。

## 4. Confidence margin 分布

INT8 logit の `argmax_q - second_q`。大きいほど自信あり。

![](figs/margin_distribution.png)

モデル進化に伴い分布が大きく右シフト (mean 11→47)。誤判定が出にくいだけでなく 正解時の confidence も同時に向上。

## 5. 14cls 混同行列

### v1234_factory
![](figs/confusion_14_v1234_factory.png)

### v1234_live
![](figs/confusion_14_v1234_live.png)

### v12345_10f_live
![](figs/confusion_14_v12345_10f_live.png)

### v12345_50f_factory
![](figs/confusion_14_v12345_50f_factory.png)

### v12345_50f_live
![](figs/confusion_14_v12345_50f_live.png)

### v12345_50f_live_noiselow
![](figs/confusion_14_v12345_50f_live_noiselow.png)

### v12345_BLJIT_factory
![](figs/confusion_14_v12345_BLJIT_factory.png)

### v12345_BLJIT_live
![](figs/confusion_14_v12345_BLJIT_live.png)

## 6. 32cls 混同行列

### v1234_factory
![](figs/confusion_32_v1234_factory.png)

### v1234_live
![](figs/confusion_32_v1234_live.png)

### v12345_10f_live
![](figs/confusion_32_v12345_10f_live.png)

### v12345_50f_factory
![](figs/confusion_32_v12345_50f_factory.png)

### v12345_50f_live
![](figs/confusion_32_v12345_50f_live.png)

### v12345_50f_live_noiselow
![](figs/confusion_32_v12345_50f_live_noiselow.png)

### v12345_BLJIT_factory
![](figs/confusion_32_v12345_BLJIT_factory.png)

### v12345_BLJIT_live
![](figs/confusion_32_v12345_BLJIT_live.png)

## 7. PC eval (同入力 WAV) vs MCU 実機 — 環境汎化 vs 変換劣化の分離

現環境で 09_collector 録音した同一 WAV を PC PyTorch FP32 と MCU 実機両方で推論し精度比較。
差が小さければ「変換劣化はゼロ」が証明される。

| モデル | PC 32cls | PC 14cls | MCU 32cls | MCU 14cls | 差分 32cls | 差分 14cls |
|---|---|---|---|---|---|---|
| v1234_live | 61.3% | 95.6% | 59.4% | 96.9% | -1.9pp | +1.2pp |
| v12345_50f_live | 96.2% | 100.0% | 100.0% | 100.0% | +3.8pp | +0.0pp |

→ MCU 実機が PC FP32 を上回るケースもあり、**Neutron 変換 + CMSIS-DSP Welch + INT8 量子化 は数値的にゼロ劣化** と確認。

## 8. 校正運用の実用ガイドライン

用途別の推奨構成:

| 用途 | 推奨構成 | 起動時間 | 期待精度 |
|---|---|---|---|
| 「窓・扉が開いてるか」だけ知りたい (14cls) | **v12345_BLJIT + factory (校正なし)** | 即時 | **14cls 100%** |
| サブ状態 (32cls) も校正なしで | **v12345_BLJIT + factory** | 即時 | **32cls 88%** |
| サブ状態 (32cls) を完璧に | v12345_50f + LIVE | 校正 25 秒 | **32cls 100%** |
| 騒音環境 (TV / 空調 / 雨音) 下で運用 | v12345_50f + LIVE (ノイズ下で校正) | 校正 25 秒 | **32cls 100%** (静粛時と同等) |
| 季節跨ぎ / 部屋移設 後 | 設置先で BL CALIBRATE 1 回 | 25 秒 | 14cls 100%, 32cls ~95%+ 想定 |

**ノイズ耐性の重要な発見**: ノイズ環境下で校正すれば、ノイズ床が baseline に取り込まれて 差分がきれいに乗る → 静粛時と同等の精度 (32cls 100%) を維持できる。ノイズが「常時鳴ってる」前提なら問題なし。

**Baseline jittering aug の本質**: 学習データの各録音を **N 種類の baseline で diff** して **N サンプル分** に増殖。同じラベルが N 個の異なる特徴ベクトルとして登場するため、モデルは「baseline に依存しないラベル決定境界」を獲得する。従来 (各 captures dir 自分の s00000) は baseline 環境がラベルに紐づいてしまい、推論時に違う baseline が来ると分布外。Jitter で破壊的に解決。

## 9. 残課題 / 今後

- 別環境 (別室・別季節) でも v12345_50f が同等性能を出すか検証 (汎化の更なる証明)
- 起動時自動 calibrate フックを PC client / firmware に組み込み (運用簡略化)
- 大きな環境変化検出 (推論 confidence が連続低下したら自動 re-calibrate)

## 10. 成果物

- 学習: `runs/neutron_v12345_50f_32cls_ambient_XL/best.pt` (104k params, INT8 後 108 KB)
- Neutron TFLite: `runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_neutron_sdk26_03.tflite`
  - **NPU 比率 7/7 = 100%**, cycle est 208,879, **推論 1.89 ms**
- model_data.h: `firmware/projects/10_inference/source/model_data.h`
- 32 state sweep 生 log: `sweeps/*.log`
- 比較 CSV: `sweep_summary.csv`
