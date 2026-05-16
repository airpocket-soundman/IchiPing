# 10_inference — NN 推論オンデバイスデモ

09_collector で集めたデータで [`pc/training/`](../../../pc/training/) が訓練した NN を **MCU 上で動かす**ためのデモプロジェクト。08 と同じハード（SAI1 全二重 + ILI9341）を使い、サーボは触らない。

「audio 取込 → 特徴抽出 → NN 推論 → 結果を TFT に表示」という end-to-end 経路を最短で立ち上げ、後で eIQ Toolkit が吐く INT8 モデルを差し替えれば本番化できる構成。

## 動作シーケンス

```
boot
 └ SAI1 init (08 と同じ全二重)
 └ ILI9341 init
 └ render_multiband(s_excite)   ← 09 と同じ multiband click 列を事前生成
 └ "press SW3 to start" を TFT に表示
loop (SW3 ON 時):
 ├ play_and_capture(s_excite → s_audio)   2 s SAI1 同期 TX+RX
 ├ extract_features(s_audio → s_spectrum) 128 bin（STUB: 帯域ごとの RMS）
 ├ infer(s_spectrum → class_id, prob)     STUB: peak ビン → クラス
 ├ display_result(class, prob, spectrum)  クラス名 + 確率 + バー
 └ sleep until 3 s 周期
```

## TFT 表示レイアウト

```
┌──────────────────────────────────────────┐
│ IchiPing inference                       │  header (NAVY)
├──────────────────────────────────────────┤
│ seq    7                                 │  trial カウンタ
│ door_open                                │  推論クラス（橙, 大字）
│ p = 0.74                                 │  信頼度（緑）
│                                          │
│ ▌█ ▌▌▌ █▌▌█ ▌▌█▌▌█▌█▌▌▌                  │  spectrum bars (128 ch)
│ ▌█ ▌▌▌ █▌▌█ ▌▌█▌▌█▌█▌▌▌                  │
│ ...                                      │
└──────────────────────────────────────────┘
```

## 現状: スケルトン（STUB 実装）

ファームは end-to-end で動くが、特徴抽出と推論は**プレースホルダ**:

| 機能 | スタブ実装 | 本実装で置換 |
|---|---|---|
| 特徴抽出 | 128 帯域の RMS 直 | 整合フィルタ → 1024 pt FFT → log-mag → 128 bin（PowerQuad 経由） |
| 推論 | 最大エネルギー帯 → 線形にクラス id | INT8 1D-CNN（eIQ Toolkit 生成 C array + CMSIS-NN） |
| クラス | `door_closed` / `door_half` / `door_open` / `amb_silence` 固定 4 種 | `pc/training/dataset.py` のラベル順に同期 |

スタブでも実機で動かすと「マイクに音を入れるとクラスとバーが変わる」のが見えるので、表示パイプライン・SAI・SW3 ゲートの動作確認はこれで完結する。

## 本実装ロードマップ

1. **`pc/training/` で best.pt + best.onnx を出す** — IP-0.5.1（[訓練 README](../../../pc/training/README.md)）
2. **eIQ Toolkit で ONNX → INT8 量子化 → C source 生成** — IP-0.5.2（[mcu_deployment.html](../../../docs/mcu_deployment.html)）
3. **生成された `model_data.c` を `10_inference/` に取り込み**、`infer()` を `cmsis_nn` 呼出に置換
4. **`extract_features()` を PowerQuad FFT 実装に置換**（同 IP-0.5.3）
5. **クラス名配列 `INF_CLASS_NAMES[]` を訓練ラベルに同期** — `pc/training/dataset.py` の auto-discover 順から拾うのが安全
6. **レイテンシ計測** — `play_and_capture` 後の SysTick で extract + infer の所要時間を測り、TFT に "infer X ms" を表示

## 配線

[09_collector](../09_collector/README.md) と完全に同じ（**サーボは未使用**, PCA9685 は接続不要）:

| 信号 | ピン | 備考 |
|---|---|---|
| SAI1 BCLK / FS / TXD / RXD | J1.1 / J1.11 / J1.5 / J1.15 | 08 と同じ |
| OpenSDA UART | LPUART4 (115200 bps, テキスト) | デバッグログのみ |
| ILI9341 TFT | LPSPI1 + A2/A3/A4/A5 GPIO | 03 と同じ |
| SW3 | PORT0_6 オンボード | 開始/停止トグル |

サーボは未使用なので 5V 外部レール（MAX98357A 用 1 系統）だけで足りる。

## ビルド手順

09_collector のビルド手順とほぼ同じ。差分:
- LPI2C2 / PCA9685 関連ファイルは不要
- 追加ソース: `firmware/shared/source/{ichiping_frame, sai_mic, sai_speaker, ili9341}.c` + 本 main.c
- `ichp_cmd.c` / `servo_config.c` / `collector_display.c` / `pca9685.c` は **不要**

## 既知の制約 / TODO

- スタブ推論は学習済み NN を反映していない — IP-0.5.2 まで本物にならない
- 特徴抽出スタブは spectrum を「平均 RMS」で代用しているので**周波数分解能ゼロ**、本来必要な整合フィルタが入っていない
- PC 連携なし（ICHP 送信もコマンド受信もしない）。実機 1 体で完結するスタンドアロンデモ
- 推論結果のロギング機能なし。検証時は OpenSDA UART デバッグ出力で確認する想定（v0.5 後半で追加）
