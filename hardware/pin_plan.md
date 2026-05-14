# IchiPing 本番用 ピン配置プラン（v1, FRDM-MCXN947）

実機 [FRDM-MCXN947 Board User Manual](../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) の Tables 17–20 に基づく、**全デバイス同時接続**の確定ピン配置と整合性チェック表。

各プロジェクト（02 servo / 06 mic / 07 speaker / 08 mic+spk / 09 stream / 10 collector / 03 TFT / 04 LVGL）の個別 `pin_mux.c` / `app.h` はこのプランに従って書かれています。

## 採用ペリフェラルとバス

| 用途 | MCU 周辺 | 占有する FC | 排他制約 |
|---|---|---|---|
| OpenSDA debug UART | LPUART4 | FC4 | （固定） |
| サーボ I²C + センサ I²C | LPI2C2 | FC2 | **ESP32 LPUART2 と排他**（FC2 は I²C 専用化） |
| TFT SPI | LPSPI1 | FC1 | — (microSD は本計画では未採用) |
| 音響 I²S 全二重 | SAI1 (= I2S1) | — (SAI 系) | — |
| USB CDC | USB1 HS | — (USB 系) | target USB-C (J21) |

## ピン割当一覧（デバイス → MCU ピン → 物理ヘッダ）

### 音響系（J1 ヘッダ・SAI1）

| デバイス | デバイス端子 | MCU pin | Alt | SDK 信号名 | ヘッダ | SJ |
|---|---|---|---|---|---|---|
| INMP441 | SCK ← | P3_16 | Alt10 | `SAI1_TX_BCLK` | **J1.1** | SJ11=1-2 (デフォルト) |
| INMP441 | WS ← | P3_17 | Alt10 | `SAI1_TX_FS` | **J1.11** | — (SJ なし、直結) |
| INMP441 | SD → | P3_21 | Alt10 | `SAI1_RXD0` | **J1.15** | — |
| MAX98357A | BCLK ← | P3_16 | Alt10 | `SAI1_TX_BCLK` | **J1.1** (共有) | （上と同じ） |
| MAX98357A | LRC ← | P3_17 | Alt10 | `SAI1_TX_FS` | **J1.11** (共有) | （上と同じ） |
| MAX98357A | DIN ← | P3_20 | Alt10 | `SAI1_TXD0` | **J1.5** | — |
| INMP441 | L/R | — | — | GND 接続 | — | （左 ch 選択） |
| MAX98357A | GAIN | — | — | 無接続 | — | （9 dB デフォルト） |
| MAX98357A | SD | — | — | 3V3 直結 | — | （常時 ON） |
| INMP441 + MAX98357A | VDD/VIN | — | — | 3V3 / 5V 外部 | — | — |

**BCLK/WS は同じ J1 ピンを INMP441 と MAX98357A の入力に T 字分岐**で並列接続（broadcast）。DIN と SD は専用 1 本ずつ。

### サーボ + センサ I²C 系（J2 ヘッダ・LPI2C2 / FC2）

| デバイス | バス端子 | MCU pin | Alt | SDK 信号名 | ヘッダ | SJ | I²C アドレス |
|---|---|---|---|---|---|---|---|
| PCA9685（既定）/ LU9685（代替）/ BMP585 | SDA | P4_0 | Alt2 | `FC2_I2C_SDA` | **J2.18 (D18)** | SJ14=1-2 デフォルト | — |
| 同上 | SCL | P4_1 | Alt2 | `FC2_I2C_SCL` | **J2.20 (D19)** | SJ15=1-2 デフォルト | — |
| PCA9685 | I²C addr | — | — | — | — | — | 0x40 |
| LU9685 | I²C addr | — | — | — | — | — | **0x1F**（実機, ジャンパで 0x00–0x1F） |
| BMP585 | I²C addr | — | — | — | — | — | 0x47 |

> **排他**: FC2 を I²C 専用化したため、J1.2 (D0) / J1.4 (D1) の `FC2_UART` は使えません。将来 ESP32-WROOM を繋ぐなら、I²C を FC3（mikroBUS J5 経由）に移すか LPI2C ペリフェラルを別 FC に再割当が必要。

### TFT SPI 系（J2 + J4 ヘッダ・LPSPI1 / FC1）

| デバイス | デバイス端子 | MCU pin | Alt | SDK 信号名 | ヘッダ | SJ |
|---|---|---|---|---|---|---|
| ILI9341 | MOSI ← | P0_24 | Alt2 | `FC1_SPI_SDO` | **J2.8 (D11)** | **SJ7=1-2** (デフォルト) |
| ILI9341 | SCK ← | P0_25 | Alt2 | `FC1_SPI_SCK` | **J2.12 (D13)** | — |
| ILI9341 | MISO → | P0_26 | Alt2 | `FC1_SPI_SDI` | **J2.10 (D12)** | — | (ILI 書込専用なので未接続でも OK、ピンは muxed) |
| ILI9341 | CS ← (GPIO) | P0_14 | Alt0 | GPIO | **J4.6 (A2)** | — |
| ILI9341 | RESET ← | P0_22 | Alt0 | GPIO | **J4.8 (A3)** | — |
| ILI9341 | DC ← | P0_15 | Alt0 | GPIO | **J4.10 (A4)** | SJ8=1-2 (デフォルト) |
| ILI9341 | BL ← | P0_23 | Alt0 | GPIO | **J4.12 (A5)** | SJ9=1-2 (デフォルト) |

> **microSD は本計画では採用しない**。学習データの永続化は PC 側（receiver.py / collector_client.py）が担当する。よって **D10 (P0_27 = `FC1_SPI_PCS`) は SJ6=2-3 デフォルトのまま**、`LED_GREEN` として残せる。後で microSD が必要になったら SJ6=1-2 + D10 を HW PCS or GPIO CS として追加。
>
> ILI9341 の CS は HW PCS（D10）ではなく **A2 の GPIO 手動制御**にしているのは、(1) HW PCS は LPSPI セッション毎にしか制御できず ILI の連続バーストに不向きで、(2) D10 を GPIO のまま空けておけば microSD を後付けする時にも干渉しないため。

### GPIO 入力（J1 + J2 ヘッダ・UI とセンサ）

| 用途 | MCU pin | ヘッダ | デフォルト機能との衝突 |
|---|---|---|---|
| **推論中 LED** out | P0_29 | J1.6 (D2) | — |
| トグル: 窓 a | P1_23 | J1.8 (D3) | SJ1 デフォルト OK |
| トグル: 窓 b | P0_30 | J1.10 (D4) | SJ2 デフォルト OK |
| トグル: 窓 c | P1_21 | J1.12 (D5) | SJ3 デフォルト OK。**SAI1_MCLK / ENET と同じ pin**（IchiPing は MCLK 不使用なので可） |
| トグル: 扉 AB | P1_2 | J1.14 (D6) | SJ4 デフォルト OK。**LED_BLUE と共用**（トグル使用時 LED は消灯） |
| トグル: 扉 BC | P0_31 | J1.16 (D7) | — |
| EXEC ボタン | P0_28 | J2.2 (D8) | — |

5 個のトグル + EXEC ボタン + 1 個の状態 LED が J1/J2 に収まる。

### LED / 状態表示

| LED | 駆動方式 |
|---|---|
| **PWR LED**（緑、常時 ON） | 外付け（3V3 → 330Ω → LED → GND）。MCU GPIO 不要 |
| **推論中 LED**（橙） | D2 (P0_29) GPIO 出力で能動駆動 |
| LED_RED (P0_10) | D9 として SJ5 デフォルト経路を使えば点灯可能。未使用なら GPIO もしくは LED として活用余地あり |
| LED_GREEN (P0_27) | **常時使用可**（microSD 不採用、SJ6=2-3 デフォルトのまま）。GPIO 出力で点灯制御も可能 |
| LED_BLUE (P1_2) | **トグル: 扉 AB と共用**。点灯はトグル状態に依存 |

### USB CDC + OpenSDA UART（オンボード固定）

| 用途 | コネクタ | MCU |
|---|---|---|
| OpenSDA debug + フレーム送信（v0.1） | J17 (MCU-Link USB-C) | LPUART4 (P1_8/P1_9, FC4 Alt2) |
| USB CDC ACM (v0.3+ 高速ストリーム) | **J21 (target USB-C)** | USB1 HS controller (専用ピン、ヘッダ非経由) |

## SJ ジャンパ設定まとめ

すべて FRDM-MCXN947 出荷時デフォルトと**異なる**設定を要するもの、デフォルトのままでよいものを一覧化:

| SJ | 必要設定 | 用途 | 出荷時デフォルト | 動作の影響 |
|---|---|---|---|---|
| SJ6 | **2-3** (デフォルト) | D10 = LED_GREEN（microSD 採用しないため） | 2-3 (デフォルト) | microSD を採用するなら 1-2 に切替えて D10 = SPI_PCS |
| SJ7 | **1-2** | D11 = SPI_SDO | 1-2 (デフォルト) | — |
| SJ8 | 1-2 | A4 = GPIO（ILI9341 DC） | 1-2 (デフォルト) | — |
| SJ9 | 1-2 | A5 = GPIO（ILI9341 BL） | 1-2 (デフォルト) | Wakeup_B 機能不使用 |
| SJ11 | 1-2 (デフォルト) | J1.1 = `SAI1_TX_BCLK` | 1-2 (デフォルト) | SINC0_MBIT4 不使用 |
| SJ14 | 1-2 (デフォルト) | D18 = `FC2_I2C_SDA` | 1-2 (デフォルト) | I3C1_SDA 不使用 |
| SJ15 | 1-2 (デフォルト) | D19 = `FC2_I2C_SCL` | 1-2 (デフォルト) | I3C1_SCL 不使用 |

**実機検証で要対応**: **なし** — IchiPing v1 は FRDM-MCXN947 出荷時のジャンパ位置だけで全機能が動く構成にしてある。`SAI1_TX_FS` は当初 J1.3 (SJ10=2-3 が必要) で計画していたが、**J1.11 が同信号をデフォルト位置で出している**のでこちらを使う。SJ10 はデフォルト 1-2 のままで J1.3 = MC_ENC_I (未使用) のまま放置。

## 衝突マトリクス（同時に使えない組合せ）

| 衝突 | 理由 | 解消方法 |
|---|---|---|
| FC2 I²C (PCA9685) vs FC2 UART (ESP32-WROOM) | FlexComm 2 は一度に 1 ペリフェラルのみ | ESP32 は v1 では未採用。必要なら I²C を FC3 へ移行 |
| D10 SPI_PCS vs LED_GREEN | 同じ P0_27 ピン | 本計画は microSD 不採用なので SJ6=2-3 で LED_GREEN を維持。将来 microSD 採用時のみ問題化 |
| D6 GPIO vs LED_BLUE | 同じ P1_2 ピン | トグル: 扉 AB のステートで LED 点灯。実用上は無視 |
| D5 GPIO vs SAI1_MCLK | 同じ P1_21 ピン | IchiPing は MCLK 不使用 → 影響なし |
| D9 (P0_10) vs LED_RED | 同じピン | v1 で D9 未使用なら LED_RED 自由 |

## 全デバイス同時接続チェック

下記すべて同時に接続して**ピン重複なし**で動作可能なことを確認:

✅ INMP441 マイク（SAI1: 3 pin）
✅ MAX98357A スピーカ（SAI1: 3 pin、BCLK/WS を mic と共有）
✅ PCA9685 サーボドライバ（LPI2C2: 2 pin）
✅ LU9685 サーボドライバ代替（同じ I²C バス、addr 違いで両立可）
✅ BMP585 気圧センサ（同じ I²C バス）
✅ ILI9341 TFT（LPSPI1: 2 pin + GPIO 4 pin）
⏸️ microSD は本計画では未採用（PC 側 receiver.py / collector_client.py で代替）
✅ SG90 サーボ ×5（PCA9685 経由、ヘッダ占有なし）
✅ トグルスイッチ ×5 + EXEC ボタン（J1 D3..D7 + J2 D8）
✅ 推論中 LED（D2）
✅ PWR LED（外付け、ヘッダ占有なし）
✅ OpenSDA UART（J17, ヘッダ占有なし）
✅ USB CDC（J21, ヘッダ占有なし）

**ピン使用合計**: J1 = 9 pin / J2 = 6 pin / J4 = 4 pin

**未使用ヘッダピン**（将来拡張枠）: J1.7 (P1_21 SAI1_MCLK)、J1.16 (D7) は EXEC か trigger 何かに転用可。J3 全部、J4.A0/A1 (アナログ専用) も未使用。

## 各プロジェクトとの対応

| プロジェクト | 使うピン |
|---|---|
| 01_dummy_emitter | OpenSDA UART のみ |
| 02_servo_test | LPI2C2 (D18/D19) |
| 03_ili9341_test | LPSPI1 (D11/D13) + 4 GPIO (A2..A5) |
| 04_lvgl_test | 同上 |
| 05_usb_cdc_emitter | USB CDC (J21) |
| 06_mic_test | SAI1 RX (J1.1/J1.11/J1.15) |
| 07_speaker_test | SAI1 TX (J1.1/J1.11/J1.5) |
| 08_mic_speaker_test | SAI1 全二重 (J1.1/J1.11/J1.5/J1.15) |
| 09_audio_stream | SAI1 RX のみ（06 と同じ） |
| 10_collector | SAI1 全二重 (08 と同じ) + ASCII コマンド (OpenSDA UART) |
| v1 統合版 | **上記すべて同時** |

## 関連

- 配線・テーブル形式の詳細: [wiring.md](wiring.md)
- 部品表: [bom.html](bom.html)
- ディスプレイ採用判断: [display_options.html](display_options.html)
- 出典 PDF: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../docs/pdf/FRDM-MCXN947BoardUserManual.pdf)
