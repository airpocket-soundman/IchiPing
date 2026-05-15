# 07_speaker_test — MAX98357A スピーカ疎通

> **✅ 実機ブリングアップ済 (2026-05-16, commit `385b619`)** — FRDM-MCXN947 + MAX98357A + 0.25 W 8 Ω 45 mm スピーカで 200/1k/5k Hz 純音と 200→6 kHz チャープが鳴ることを耳で確認。

MAX98357A（クラス D, I²S→アナログ）に **200 Hz / 1 kHz / 5 kHz 正弦波**、**200→6 kHz クリーン チャープ**、**無音** を順に流して耳で確認する。PC ソフトは不要、スピーカが鳴れば合格。

## 期待する動作

```
[SAI] sai_clk=48000000 Hz TCSR=0x9017xxxx TCR2=0x07000016 TCR4=0x10011f3b MCR=0x40000000
[cycle 1]
  200 Hz tone                                ← 低音 1秒
  1 kHz tone                                 ← 中音 1秒（一番聞きやすい）
  5 kHz tone                                 ← 高音 1秒（耳キンキンする）
  chirp 200->6k (clean linear, 5 ms fade)    ← 推論で使うチャープ系の音 2秒
  silence 1 s                                ← 完全に止まればクラス D シャットダウン OK
```

`[SAI]` 行は init 時の SAI レジスタダンプ — bit31 (TE) と bit28 (BCE) が立っていれば TX 動作中、`TCR4` bit28 (FCONT) と `MCR` bit30 (MOE) が立っていれば framer が underflow で止まらない設定。期待値は [06_mic_test/BRINGUP_NOTES.md](../06_mic_test/BRINGUP_NOTES.md#L24) と同じ。

判定:
- 無音 → SAI ダンプを確認。`TCR4` bit28 / `MCR` bit30 が立っていれば SAI 側 OK で、ハード側 (VIN=5V / SD=高 / GND 共通 / OUT± にスピーカ) を疑う
- ザザザ系のノイズ → `SAI_WriteBlocking` のスタック過剰読み出しバグに当たっている可能性 (このリビジョンでは fix 済)
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

## 音量調整

`main.c` 冒頭の `SPK_GAIN` (現在 `0.15f` = -16 dB) で全シグナル一律にデジタル減衰している。これより下げると MAX98357A の class-D アイドル ノイズ (ジリジリ) が相対的に増えるので、更に静かにしたい場合は **MAX98357A の GAIN ピンを VDD or 100kΩ to GND** に変更してハード ゲインを 6 dB / 3 dB に落とす方が音質を保てる。

## ブリングアップ時に踏んだ落とし穴 (記録)

1. **`SAI_WriteBlocking` がスタックを過剰読み出しする** — 内部で `(FIFO_DEPTH-watermark) * bytesPerWord` バイトを 1 バースト書き込みする実装。4 バイトのローカル `uint32_t` を渡すと残り 24 バイトをスタックから読んで FIFO に流す。対策: per-sample `SAI_WriteData` + `kSAI_FIFORequestFlag` 待ち
2. **TX フレーマが FIFO underflow で停止** — `TCR4.FCONT=1` を立てないと一度 underflow すると BCLK/FS が止まる。8 ワードの zero プライマも必要
3. **`MCR.MOE=0` だと BCLK が永久 0 V** — MCLK ピンが入力モードに設定され、`TCR2.MSEL=01` の BCLK source が外部 MCLK を待ち続ける。`SAI_SetMasterClockConfig(mclkOutputEnable=true)` で内部 MCLK divider に切替
4. **SAI ピンのドライブ強度** — 1 MHz BCLK を J1 ヘッダ → ジャンパー線で引き回すなら `kPORT_HighDriveStrength` 必須。Low だと edge が崩れて MAX98357A が Fs を誤検出する

これら 4 件は [shared/source/sai_speaker.c](../../shared/source/sai_speaker.c) と [06_mic_test/BRINGUP_NOTES.md](../06_mic_test/BRINGUP_NOTES.md) に詳細あり。

## 既知の TODO

- 連続再生（DMA リング）に切り替えるときは `sai_speaker_start_streaming` を実装
- 08_mic_speaker_test で同じ SAI1 を全二重で使う際、speaker_init と mic_init を同居させても矛盾しないか確認 (TX framer は両方で master 想定で同じ設定)
