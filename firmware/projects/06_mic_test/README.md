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
| **SCK** | **J1.1** *または* **J5.6 / J2.17** | P3_16 (J1.1) / P1_0 (J5.6・J2.17) | Alt10 (両方) | `SAI1_TX_BCLK` | SJ11: 1-2 (デフォルト) | ビットクロック。MCU が master。**P3_16 と P1_0 の両方を Alt10 でムックスして 2 系統出している** — どちらに INMP441 の SCK を繋いでも良い。SJ11 を経由する J1.1 と SJ なし直結の J5.6/J2.17 のうち、配線が短い方を選ぶ。ドライブ強度は `kPORT_HighDriveStrength` (1 MHz BCLK でジャンパー配線でも edge を保つため) |
| **WS** | **J1.11** | P3_17 | Alt10 | `SAI1_TX_FS` | — (SJ なし、直結) | LRCLK / WS。J1.11 は P3_17 が直結されているのでジャンパ操作不要 |
| **SD** | **J1.15** | P3_21 | Alt10 | `SAI1_RXD0` | — (直結) | INMP441 → MCU データ |

> **WS の J1 ピン番号について**: BUM Table 17 では P3_17 / `SAI1_TX_FS` が **J1.3 (SJ10=2-3 が必要)** と **J1.11 (SJ なし、デフォルト)** の両方に出てくる。IchiPing は出荷時の SJ 位置だけで動く構成にしたいので、**J1.11 を採用**してジャンパ操作を不要にしている。`docs/pdf/FRDM-MCXN947BoardUserManual.pdf` p20-21 の Table 17 参照。
>
> **クロック構成の補足**: 06 単体運用でも BCLK/FS は **TX フレーマ (master)** が生成し、RX フレーマは **sync モード**で TX のクロックを内部共有する設計（`firmware/shared/source/sai_mic.c` の `sai_mic_init`）。これは 07_speaker_test / 08_mic_speaker_test と **同じ配線で動かす**ためで、後者でマイクとスピーカに BCLK/FS を 1 セットで供給できる。
>
> したがって配線上は SAI1_RX_BCLK (P3_18, J1.9) や SAI1_RX_FS (P3_19, J1.13) を使う必要は**ない**。INMP441 の SCK と WS は J1.1 / J1.11 から取る。

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
