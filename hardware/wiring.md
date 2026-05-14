# IchiPing 配線テーブル（v1, FRDM-MCXN947 / INMP441 想定）

主要な配線図は [wiring.svg](wiring.svg)、機械可読ネットリストは [netlist.csv](netlist.csv)。本ドキュメントは **MCU ピン → 接続先デバイス端子** の関係を表形式で整理したもの。

## 1. バス割り当てサマリ

| バス | MCU 周辺 | 接続先デバイス | アドレス/CS | Arduino ヘッダ |
|---|---|---|---|---|
| I²S (full-duplex) | SAI1 | INMP441 マイク（RX）＋ MAX98357A スピーカ（TX） | — | J1 SAI 列（PIO3_16/17/20/21） |
| I²C | LPI2C2（FC2） | PCA9685 / LU9685 / BMP585 | 0x40 / 0x1F (jumper 0x00–0x1F) / 0x47 | **D18 (SDA, PIO4_0), D19 (SCL, PIO4_1)** |
| SPI | LPSPI1（FC1） | ILI9341 TFT | CS=A2 (GPIO 駆動) | D11/D12/D13 (J2.8/10/12), A2/A3/A4/A5 (J4.6/8/10/12) |
| UART | LPUART4（FC4） | OpenSDA 仮想 COM（v0.1 デバッグ／フレーム送信） | — | オンボード MCU-Link |
| UART | LPUART2（FC2） | ESP32-WROOM（任意, 将来） | — | D0, D1 |
| USB | USB0 | PC（CDC データ転送） | — | オンボード USB-C |
| PWM | （PCA9685 経由） | SG90 ×5 | PCA ch 0–4 | — |
| GPIO IN | — | トグル ×5 + EXEC ×1 + BMP INT | 内蔵プルアップ | D2–D9 |
| GPIO OUT | — | LED ×2 | 330Ω 直列 | A0, A1 |

## 2. MCU ピン → デバイス端子 マップ

> **注**: Arduino ヘッダ番号（D0–D15、A0–A5）は FRDM-MCXN947 ボード上の刻印。実 P-port 番号は **MCUXpresso Config Tools の Pins ツール**で確定すること（pin_mux.c に反映）。

### 2.1 I²S（音響系, SAI1 full-duplex）

INMP441（マイク）と MAX98357A（スピーカ）を **同一 SAI1 ペリフェラル**にぶら下げる。BCLK/FS は MCU 側 TX 側マスタが発生し、両デバイスが共用する。これでサンプルクロックが内部で揃うので、インパルス応答計測（[08_mic_speaker_test](../firmware/projects/08_mic_speaker_test/), [10_collector](../firmware/projects/10_collector/)）でズレが出ない。

| MCU pin | Alt | SDK 機能 | 接続先 | 信号 | 方向 |
|---|---|---|---|---|---|
| PIO3_16 | **Alt10** | `SAI1_TX_BCLK` | INMP441 SCK + MAX98357A BCLK | BCLK | MCU → MIC / SPK |
| PIO3_17 | **Alt10** | `SAI1_TX_FS`   | INMP441 WS + MAX98357A LRC   | LRCLK/FS | MCU → MIC / SPK |
| PIO3_20 | **Alt10** | `SAI1_TXD0`    | MAX98357A DIN                | TX data | MCU → SPK |
| PIO3_21 | **Alt10** | `SAI1_RXD0`    | INMP441 SD                   | RX data | MIC → MCU |
| PIO1_21 | **Alt10** | `SAI1_MCLK` (任意) | (外部 codec 用)            | MCLK | MCU → ext |

INMP441 の `L/R` ピンは **GND に接続**（左チャネル選択）。MAX98357A の `GAIN` は無接続（9 dB デフォルト）、`SD` は 3.3V 直結（常時 ON）。

> SAI 検証出典: [d:/GitHub/mcuxsdk/.../driver_examples/sai/edma_transfer/pin_mux.c](../../mcuxsdk/mcuxsdk/examples/_boards/frdmmcxn947/driver_examples/sai/edma_transfer/pin_mux.c) 同等構成で動作実績あり。

### 2.2 I²C（LPI2C2 / FC2, 内蔵プルアップ可）

**動作実績**: [d:/GitHub/FRDM-MCXN947_demo/41_lpi2c_vl53l0x_my](../../FRDM-MCXN947_demo/41_lpi2c_vl53l0x_my) で **内蔵プルアップだけで VL53L0X が動いた**実績あり。100 kHz・短いシールド配線・1 〜 2 デバイスなら外付け不要。

| Arduino | MCU pin | Alt | SDK 機能 | I²C アドレス | デバイス |
|---|---|---|---|---|---|
| **D18** (J2 pin 18) | PIO4_0 | **Alt2** | `LP_FLEXCOMM2_P0` (LPI2C2 SDA) | 0x40 | PCA9685 サーボドライバ |
| **D19** (J2 pin 20) | PIO4_1 | **Alt2** | `LP_FLEXCOMM2_P1` (LPI2C2 SCL) | 0x00–0x1F（**現物 0x1F**） | LU9685（代替, [lu9685.c](../firmware/shared/source/lu9685.c)） |
|  |  |  |  | 0x47 | BMP585 気圧センサ（補助） |

> **注**: ボードシルクの "D14/D15" 表記は本ボードには存在しない（J2 ヘッダには D8〜D13 と D18/D19 が並ぶ）。Arduino UNO R3 で SDA/SCL に当たる位置のピンが D18/D19 にラベルされている。
>
> 内蔵プルアップは ~50 kΩ で I²C 仕様としては弱いが、上述の通り FRDM ボード上の短い配線では実機動作する。複数デバイス（PCA9685 + BMP585 を同時に）下げる、または 400 kHz 動作時は外付け 4.7 kΩ × 2 を追加すること。

### 2.3 SPI（ILI9341 TFT のみ, LPSPI1 / FC1）

FRDM-MCXN947 Board User Manual Table 18（Arduino J2）と Table 20（J4）に基づく確定構成。出典: [../docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../docs/pdf/FRDM-MCXN947BoardUserManual.pdf)。

| Arduino | J ピン | MCU pin | Alt | SDK 信号 | ILI9341 端子 | ソルダージャンパ | 備考 |
|---|---|---|---|---|---|---|---|
| D11 | J2.8 | P0_24 | Alt2 (FC1) | `FC1_SPI_SDO` | SDI / MOSI | **SJ7: 1-2** (default) | LPSPI1 master out |
| D12 | J2.10 | P0_26 | Alt2 (FC1) | `FC1_SPI_SDI` | SDO / MISO（未接続）| — | ILI9341 は書込のみ。pin はムックスしておくが結線不要 |
| D13 | J2.12 | P0_25 | Alt2 (FC1) | `FC1_SPI_SCK` | SCK | — | LPSPI1 クロック |
| **A2** | J4.6 | P0_14 | Alt0 (GPIO) | `ARD_A2` | CS | — | マニュアル CS（ハード PCS 非使用） |
| **A3** | J4.8 | P0_22 | Alt0 (GPIO) | `ARD_A3` | RESET | — | Hard reset (init 時 LOW パルス) |
| **A4** | J4.10 | P0_15 | Alt0 (GPIO) | `ARD_A4` | DC | **SJ8: 1-2** (default) | Data/Command 切替 |
| **A5** | J4.12 | P0_23 | Alt0 (GPIO) | `ARD_A5` | LED/BL | **SJ9: 1-2** (default) | バックライト。SJ9=2-3 だと Wakeup ピンに切替わる |

> **microSD は本計画では採用しない**。よって D10 (P0_27 / `FC1_SPI_PCS`) は **SJ6=2-3 のまま (default)** で LED_GREEN として残せる。学習データは PC 側（receiver.py / collector_client.py）で受けて保存するので、ボード側ストレージ不要。
>
> **重要 1**: 旧 wiring.md は "FC? / LPSPI?" と "TODO" を残していたが、実機は **LPSPI1 (= FC1)** で確定。出典は Board User Manual Table 18。
>
> **重要 2**: A5 (P0_23) は **SJ9=1-2 (default)** が必要。SJ9=2-3 にすると Wakeup ピンとして使われて GPIO 出力できない。
>
> **検証ソース**: SDK の `examples/_boards/frdmmcxn947/driver_examples/lpspi/polling_b2b_transfer/master/pin_mux.c` も同じ PORT0_24/25/26 + Alt2 を使用。

> 全体ピン配置とコンフリクト解析は [pin_plan.md](pin_plan.md) に集約。

### 2.4 UART（ESP32, FC2）

| Arduino | MCU 機能 | ESP32 ピン |
|---|---|---|
| D0 | `FC2_UART_RX` | U0TXD |
| D1 | `FC2_UART_TX` | U0RXD |

### 2.5 PWM サーボ（PCA9685 経由）

| PCA9685 ch | 機構 | サーボ役割 | 角度範囲（仮） |
|---|---|---|---|
| PWM0 | 窓 a | 開閉 | 0°（閉） – 90°（全開） |
| PWM1 | 窓 b | 開閉 | 0° – 90° |
| PWM2 | 窓 c | 開閉 | 0° – 90° |
| PWM3 | 扉 AB | 開閉 | 0° – 90° |
| PWM4 | 扉 BC | 開閉 | 0° – 90° |

サーボ電源 V+ は **5V 専用レール**（MCU の 3.3V から分離）。V+ ↔ GND 間に 1000 µF 電解で突入電流吸収。

### 2.6 GPIO 入力（プルアップ有効、アクティブ Low）

| Arduino | 用途 | ハード |
|---|---|---|
| D2 | 推論中 LED（出力） | GPIO 出力 330Ω 直列、橙 LED |
| D3 | トグル: 窓 a | SPST → GND |
| D4 | トグル: 窓 b | SPST → GND |
| D5 | トグル: 窓 c | SPST → GND |
| D6 | トグル: 扉 AB | SPST → GND |
| D7 | トグル: 扉 BC | SPST → GND |
| D8 | EXEC ボタン | モーメンタリ SPST → GND |
| D9 | BMP585 INT（任意） | センサからのイベント割込（v0.3 以降） |

### 2.7 GPIO 出力（330Ω 直列、アノードコモン）

| Arduino | 用途 | デバイス |
|---|---|---|
| — | PWR LED | 外付け（3V3 直結 + 330Ω + 緑 LED → GND）、GPIO 不要で常時点灯。A0/A1 はアナログ専用ピンで GPIO 駆動不可なので別途用意 |
| **D2** | 推論中 LED | 橙 LED、推論サイクル実行中点灯（GPIO 出力、330Ω 直列） |
| **A2** | **ILI9341 CS** | TFT chip select（GPIO 直駆動、330Ω 不要） |
| **A3** | **ILI9341 RESET** | TFT hard reset 線（GPIO 直駆動） |
| **A4** | **ILI9341 DC** | TFT data/command 切替（GPIO 直駆動） |
| **A5** | **ILI9341 BL** | バックライト制御（GPIO ON/OFF か PWM 減光） |

> **A0/A1 は ADC 専用ピン**（Board User Manual Table 20 で `ARD_A0=ADC0_A0`、`ARD_A1=ADC0_B0` と確定）で、GPIO 機能は持っていません。LED 駆動用には使えないので、PWR LED は外付け（電源直結）、推論中 LED は D2 に変更しました。

### 2.8 USB（PC データ転送）

| 機能 | MCU | 用途 |
|---|---|---|
| USB0 D+/D- | オンボード USB-C | CDC で生音声・ラベルを PC に送信／プログラム書込 |
| VBUS | USB-C | 5V 電源入力（LiPo 充電も兼用） |

## 3. 電源系統

| レール | 供給 | 消費先 |
|---|---|---|
| 5V | USB-C VBUS or LiPo→ブースト | MAX98357A VIN、PCA9685 V+（サーボ系）、ESP32 |
| 3.3V | FRDM オンボード LDO | MCU、INMP441、ILI9341、BMP585、PCA9685 VCC（ロジック） |
| GND | 全共通 | サーボ電源 GND も必ずここに集約 |

LiPo（3.7V, 2000 mAh） + TP4056 充電 IC で携帯動作可能。USB-C 接続時は USB 給電に切替。

## 4. ピン使用統計（参考）

| 種別 | 使用数 | 余裕 |
|---|---|---|
| I²S（SAI1 占有） | 4 ピン | SAI1 専有、TX/RX 共有クロックでサンプルロック |
| I²C（FC2） | 2 ピン | 4 デバイス目以上追加可 |
| SPI（ILI9341 のみ） | 2 ピン + GPIO 4 | microSD 採用なら +1 CS。LPSPI1 (FC1) |
| UART | 2 ピン | OpenSDA 経由、別 FC で増設可 |
| GPIO IN（トグル/EXEC/INT） | 7 ピン | D3..D9（D9 はオプションの BMP INT） |
| GPIO OUT（推論中 LED） | 1 ピン | D2 のみ。PWR LED は外付け電源直結 |
| **合計** | **20 ピン** | Arduino ヘッダ 36 本中、約半数 |

## 5. 配線手順の推奨順序

1. **電源**（3.3V/5V/GND）を先に配り、テスタで各レール電圧を確認
2. **I²C** を結線 → PCA9685 (0x40) / LU9685 (0x1F) / BMP585 (0x47) を `i2cdetect` 相当のコードで全アドレス確認
3. **GPIO 入力**（トグル + EXEC）を結線 → MCU ポーリングで各ビット確認
4. **GPIO 出力**（LED）を結線 → ブリンクテスト
5. **SPI ILI9341** を結線 → 03_ili9341_test ファームで 5 フェーズ描画
6. **I²S DAC**（MAX98357A）→ 07_speaker_test で 200/1k/5k Hz + chirp を耳で確認
7. **I²S MIC**（INMP441）→ 06_mic_test で peak/RMS/ZCR を観測（学習データの永続化は PC 側 receiver.py で WAV 保存）
8. **PCA9685 → SG90 ×5** → スイープテスト、各サーボの角度キャリブレーション
9. **ESP32 UART**（任意） → AT コマンド疎通
10. **USB CDC** → PC との双方向通信テスト（[firmware/](../firmware/) と [pc/](../pc/) のスケルトン使用）

## 6. 既知の注意点

- **MAX98357A → INMP441 の機械クロストーク**: スピーカと MEMS マイクは筐体内で 40 mm 以上離し、防振フォームで挟む（spec.html §7.5 参照）
- **サーボ動作音の chirp 混入**: サーボ完了後 500 ms 以上の待機を入れてから chirp 放射（[spec.html §7.5](../docs/spec.html) シーケンス）
- **I²C プルアップは 1 セットのみ**: PCA9685 / LU9685 / BMP585 のモジュールに既存プルアップがある場合、バス上で合成されて値が変わるので注意（多くのブレイクアウトに 10 kΩ が乗っている）
- **3.3V ロジック で 5V サーボ電源**: PCA9685 のオープンドレイン PWM 出力は 5V トレラントなので問題なし
