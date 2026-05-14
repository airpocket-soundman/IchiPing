# 07_speaker_test — MAX98357A スピーカ疎通

MAX98357A（クラス D, I²S→アナログ）に **200 Hz / 1 kHz / 5 kHz 正弦波**、**chirp 200→8 kHz**、**無音** を順に流して耳で確認する。PC ソフトは不要、スピーカが鳴れば合格。

## 期待する動作

```
[cycle 1]
  200 Hz tone        ← 低音 1秒
  1 kHz tone         ← 中音 1秒（一番聞きやすい）
  5 kHz tone         ← 高音 1秒（耳キンキンする）
  chirp 200->8k      ← 推論で使うパッタリ系の音 2秒
  silence 1 s        ← 完全に止まればクラス D シャットダウン OK
```

判定:
- 無音 → BCLK が出ていない、もしくは VIN が 3V3 のままで 5V が来ていない
- ザザザ系のノイズ → DIN フローティング、または BCLK ↔ LRCLK の順序が逆
- 5 kHz だけ大きく歪む → Fs と BCLK の比率が 32× になっていない（MAX98357A は 32×Fs か 64×Fs しか受け付けない）

## 配線（実機準拠 — SAI**1** TX, J1 ヘッダ）

FRDM-MCXN947 Board User Manual Table 17（Arduino compatible header J1 pinout）に従う。出典: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) p20-21。

| MAX98357A | J1 ピン | MCU pin (GPIO) | Alt | SDK 信号 | ソルダージャンパ | 備考 |
|---|---|---|---|---|---|---|
| VIN | **外部 5V レール** | — | — | — | — | 3V3 では音量取れない。1000 µF 電解で平滑化推奨 |
| GND | GND（外部 5V と共通バー） | — | — | — | — | サーボ・スピーカ・MCU すべて 1 点接地 |
| **BCLK** | **J1.1** | P3_16 | Alt10 | `SAI1_TX_BCLK` | SJ11: 1-2 (デフォルト) | ビットクロック。SJ11 を 2-3 に動かすと P4_5 (SINC0) に切替わるが、IchiPing はデフォルトの 1-2 のまま使う |
| **LRC** | **J1.11** | P3_17 | Alt10 | `SAI1_TX_FS` | — (SJ なし、直結) | LRCLK / WS。J1.11 は P3_17 が直結されているのでジャンパ操作不要 |
| **DIN** | **J1.5** | P3_20 | Alt10 | `SAI1_TXD0` | — (直結) | TX データ |
| GAIN | **GND 直結 (3 dB)** | — | — | — | — | デモ用 0.25 W スピーカ保護。v1+ で 1〜3 W に上げるならフローティング (9 dB) に戻す |
| SD | VIN（3V3 直結） | — | — | — | — | 常時 ON。GPIO でミュート切替したい場合は別ピンへ |

> **重要 1**: ファームは **SAI1**（`I2S1`）を使用。README 旧版に "SAI0_*" と書かれていたのは誤り。`frdmmcxn947_cm33_core0/cm33_core0/app.h` の `BOARD_SPK_SAI_BASE = I2S1` が正本。
>
> **重要 2**: LRC は当初 J1.3 (SJ10=2-3 が必要) で計画していたが、BUM Table 17 によれば **J1.11 にも同じ P3_17 / `SAI1_TX_FS` が SJ 無しで直結**されているため、IchiPing は J1.11 を採用。FRDM-MCXN947 出荷時のジャンパ位置のまま動く構成になっている。SJ11 (J1.1 = BCLK 用) もデフォルトの 1-2 で OK。
>
> **重要 3**: SAI1 は INMP441（マイク, RX 側）と共用ペリフェラル。08_mic_speaker_test / 10_collector では同じ SAI1 を全二重で使うので、**J1.7 (`SAI1_MCLK`)** / **J1.9 (`SAI1_RX_BCLK`)** / **J1.13 (`SAI1_RX_FS`)** / **J1.15 (`SAI1_RXD0`)** も合わせて配線済にしておくと後の bring-up が楽。

## ビルド・実行

```
cd firmware/projects/07_speaker_test
# MCUXpresso for VS Code → Import Project → debug preset → build → run
```

## 既知の TODO

- `pins/pin_mux.c` の SAI0 ピン Alt 値は **MCUXpresso Pins tool で確認必須**
- 連続再生（DMA リング）に切り替えるときは `sai_speaker_start_streaming` を実装
