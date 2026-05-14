# 06_mic_test — INMP441 マイク疎通

INMP441（I²S MEMS, 24-bit）を SAI1 RX に繋ぎ、0.5 秒ごとに窓を取って **peak / RMS / DC / zero-crossing rate** を OpenSDA UART に PRINTF する。実音を PC まで届ける前の **配線とクロックの 1 段目検証**用。

## 期待する出力

```
IchiPing 06_mic_test  ─  INMP441 on SAI1 RX @ 16000 Hz
[   1] peak=   42  rms=    8  dc=   +1  zcr=  120Hz   ← 静かな部屋
[   2] peak=  280  rms=   52  dc=   +1  zcr= 1820Hz   ← 拍手すると跳ね上がる
[   3] peak=14820  rms= 3200  dc=   +2  zcr= 4400Hz   ← 喋ると上がる
```

判定:
- 全て 0 → SD（データ線）が来ていない、または BCLK/WS が止まっている
- peak が常に 32767 / -32768 に張り付く → ゲイン過大か L/R 線が浮いている
- DC が ±10 以上ずれ続ける → INMP441 起動直後の DC 過渡（最初の数秒で収束する）

## 配線（実機準拠 — SAI**1**, J1 ヘッダ）

FRDM-MCXN947 Board User Manual Table 17（Arduino compatible header J1 pinout）に従う。出典: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) p20-21。

| INMP441 | J1 ピン | MCU pin (GPIO) | Alt | SDK 信号 | ソルダージャンパ | 備考 |
|---|---|---|---|---|---|---|
| VDD | 3V3 | — | — | — | — | INMP441 は 1.8–3.3 V 駆動 |
| GND | GND | — | — | — | — | |
| L/R | GND | — | — | — | — | **Left チャネル選択**。VDD に繋ぐと Right になり、現在のドライバ（kSAI_MonoLeft）では音が取れない |
| **SCK** | **J1.1** | P3_16 | Alt10 | `SAI1_TX_BCLK` | **SJ11: 1-2** (P3_16 を選択) | ビットクロック。MCU が master。SJ11 が 2-3 だと P4_5 (SINC0) に切替わるので注意 |
| **WS** | **J1.3** | P3_17 | Alt10 | `SAI1_TX_FS` | **SJ10: 2-3** (P3_17 を選択) | LRCLK / WS。同上、MCU master |
| **SD** | **J1.15** | P3_21 | Alt10 | `SAI1_RXD0` | — (直結) | INMP441 → MCU データ |

> **クロック構成の補足**: 06 単体運用でも BCLK/FS は **TX フレーマ (master)** が生成し、RX フレーマは **sync モード**で TX のクロックを内部共有する設計（`firmware/shared/source/sai_mic.c` の `sai_mic_init`）。これは 07_speaker_test / 08_mic_speaker_test と **同じ配線で動かす**ためで、後者でマイクとスピーカに BCLK/FS を 1 セットで供給できる。
>
> したがって配線上は SAI1_RX_BCLK (P3_18, J1.9) や SAI1_RX_FS (P3_19, J1.13) を使う必要は**ない**。INMP441 の SCK と WS は J1.1 / J1.3 から取る。
>
> **SJ10/SJ11 注意**: FRDM-MCXN947 出荷時のデフォルト位置によっては SAI1 信号が別ペリフェラル（SINC0 / モータ制御）にルーティングされていることがある。Board User Manual Table 17 の SJ 設定を確認。

## ビルド・実行

```
cd firmware/projects/06_mic_test
# MCUXpresso for VS Code → Import Project → debug preset → build → run
```

OpenSDA 仮想 COM を **115200 8N1** で開き、上のログを観察。

## 既知の TODO

- `pins/pin_mux.c` の SAI ピン Alt 値は **MCUXpresso Pins tool で必ず確認**（プレースホルダ）
- `cm33_core0/app.h` の `BOARD_MIC_SAI_CLK_ATTACH` も SDK バージョンによってシンボル名が違うので確認
- EDMA リング版が必要になったら `sai_mic.c` の `sai_mic_start_streaming` を実装
