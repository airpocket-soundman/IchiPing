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

## 配線（[../../hardware/wiring.md](../../hardware/wiring.md) §2 参照）

| INMP441 | FRDM-MCXN947 | 備考 |
|---|---|---|
| VDD | 3V3 | |
| GND | GND | |
| L/R | GND | Left チャネル選択 |
| WS | SAI1_FS (LRCLK) | pin_mux.c の `SAI1_RX_InitPins` で確定 |
| SCK | SAI1_BCLK | 同上 |
| SD | SAI1_RXD | 同上 |

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
