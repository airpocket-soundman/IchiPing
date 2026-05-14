# IchiPing 配線テーブル（v1, FRDM-MCXN947 / INMP441 想定）

主要な配線図は [wiring.svg](wiring.svg)、機械可読ネットリストは [netlist.csv](netlist.csv)。本ドキュメントは **MCU ピン → 接続先デバイス端子** の関係を表形式で整理したもの。

## 1. バス割り当てサマリ

| バス | MCU 周辺 | 接続先デバイス | アドレス/CS | Arduino ヘッダ |
|---|---|---|---|---|
| I²S (full-duplex) | SAI1 | INMP441 マイク（RX）＋ MAX98357A スピーカ（TX） | — | J1 SAI 列（PIO3_16/17/20/21） |
| I²C | LPI2C2（FC2） | PCA9685 / LU9685 / BMP585 | 0x40 / 0x1F (jumper 0x00–0x1F) / 0x47 | **D18 (SDA, PIO4_0), D19 (SCL, PIO4_1)** |
| SPI（ILI9341 + microSD） | LPSPI? | microSD（FatFs）＋ ILI9341 TFT | CS=D10 / 別 GPIO | D10–D13 ※FC は要確認 |
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

### 2.3 SPI（microSD + ILI9341, **FC は要確認**）

| Arduino | MCU pin | SDK 機能 | デバイス端子 | 備考 |
|---|---|---|---|---|
| D10 | TODO | `FC?_SPI_CS` (microSD) | microSD CS | |
| D11 | TODO | `FC?_SPI_MOSI` | microSD MOSI / ILI9341 SDI | バス共有 |
| D12 | TODO | `FC?_SPI_MISO` | microSD MISO | ILI9341 は書込のみで MISO 未接続 |
| D13 | TODO | `FC?_SPI_SCK` | microSD SCK / ILI9341 SCK | |
| **A2** | TODO GPIO | ILI9341 CS | microSD と排他選択 |
| **A3** | TODO GPIO | ILI9341 RESET | Hard reset (init 時) |
| **A4** | TODO GPIO | ILI9341 DC | Data/Command 切替 |
| **A5** | TODO GPIO | ILI9341 LED/BL | バックライト |

> **未確認**: FRDM-MCXN947 で Arduino D11/D13 がどの LP_FLEXCOMM に割り付くかは、SDK の標準サンプルからは特定できなかった（SDK の LPSPI 例は J7 Pmod 等の専用パッドを使う）。**MCUXpresso Pins tool で D11/D12/D13 の pin_signal 列を見て、`FCx_Px` の x を確定してから [03_ili9341_test/.../app.h](../firmware/projects/03_ili9341_test/frdmmcxn947_cm33_core0/cm33_core0/app.h) と [pin_mux.c](../firmware/projects/03_ili9341_test/frdmmcxn947_cm33_core0/pins/pin_mux.c) を更新**してください。
>
> 暫定の経験則:
> - D12 の pin は **LED Green と共用**（`BOARD_LED_GREEN_GPIO_PIN = 27`）の可能性がある → MISO 機能と LED 表示の二者択一
> - SW2 が `GPIO0 P23` (= A5 候補) を使っているので、**A5 を ILI9341 BL に取ると SW2 が効かなくなる**可能性

ILI9341 と microSD を **同一 SPI バス**に乗せ、CS で排他選択。同時アクセスは出来ないが、本プロジェクトでは推論／表示と SD への書込は別フェーズで行うため問題なし。

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
| D2 | BMP585 INT（任意） | センサからのイベント割込 |
| D3 | microSD CD（任意） | カード挿抜検出 |
| D4 | トグル: 窓 a | SPST → GND |
| D5 | トグル: 窓 b | SPST → GND |
| D6 | トグル: 窓 c | SPST → GND |
| D7 | トグル: 扉 AB | SPST → GND |
| D8 | トグル: 扉 BC | SPST → GND |
| D9 | EXEC ボタン | モーメンタリ SPST → GND |

### 2.7 GPIO 出力（330Ω 直列、アノードコモン）

| Arduino | 用途 | デバイス |
|---|---|---|
| A0 | PWR LED | 緑 LED、電源 ON で常時点灯 |
| A1 | 推論中 LED | 橙 LED、推論サイクル実行中点灯 |
| **A2** | **ILI9341 CS** | TFT chip select（GPIO 直駆動、330Ω 不要） |
| **A3** | **ILI9341 RESET** | TFT hard reset 線（GPIO 直駆動） |
| **A4** | **ILI9341 DC** | TFT data/command 切替（GPIO 直駆動） |
| **A5** | **ILI9341 BL** | バックライト制御（GPIO ON/OFF か PWM 減光） |

### 2.8 USB（PC データ転送）

| 機能 | MCU | 用途 |
|---|---|---|
| USB0 D+/D- | オンボード USB-C | CDC で生音声・ラベルを PC に送信／プログラム書込 |
| VBUS | USB-C | 5V 電源入力（LiPo 充電も兼用） |

## 3. 電源系統

| レール | 供給 | 消費先 |
|---|---|---|
| 5V | USB-C VBUS or LiPo→ブースト | MAX98357A VIN、PCA9685 V+（サーボ系）、ESP32 |
| 3.3V | FRDM オンボード LDO | MCU、INMP441、ILI9341、BMP585、microSD、PCA9685 VCC（ロジック） |
| GND | 全共通 | サーボ電源 GND も必ずここに集約 |

LiPo（3.7V, 2000 mAh） + TP4056 充電 IC で携帯動作可能。USB-C 接続時は USB 給電に切替。

## 4. ピン使用統計（参考）

| 種別 | 使用数 | 余裕 |
|---|---|---|
| I²S（SAI 占有） | 6 ピン | SAI0/1 専有なので干渉なし |
| I²C（共有） | 2 ピン | 4 デバイス目以上追加可 |
| SPI（microSD + ILI9341 共有） | 4 ピン + 別 CS 1 | CS を増やせばさらに追加可 |
| UART | 2 ピン | 別 FC で増設可 |
| GPIO IN（トグル/EXEC/INT） | 8 ピン | FRDM 拡張ヘッダで余り |
| GPIO OUT（LED + ILI9341 制御線） | 2 + 4 = 6 ピン | A0..A5 を全消費 |
| **合計** | **27 ピン** | Arduino ヘッダ 36 本中 |

## 5. 配線手順の推奨順序

1. **電源**（3.3V/5V/GND）を先に配り、テスタで各レール電圧を確認
2. **I²C** を結線 → PCA9685 (0x40) / LU9685 (0x1F) / BMP585 (0x47) を `i2cdetect` 相当のコードで全アドレス確認
3. **GPIO 入力**（トグル + EXEC）を結線 → MCU ポーリングで各ビット確認
4. **GPIO 出力**（LED）を結線 → ブリンクテスト
5. **SPI microSD** を結線 → FatFs マウント、ファイル書き込みテスト
6. **I²S DAC**（MAX98357A）→ 440 Hz サイン波テスト、スピーカから音が出ることを確認
7. **I²S MIC**（INMP441）→ 16 kHz 録音 → microSD に WAV 保存、PC で再生確認
8. **PCA9685 → SG90 ×5** → スイープテスト、各サーボの角度キャリブレーション
9. **ESP32 UART**（任意） → AT コマンド疎通
10. **USB CDC** → PC との双方向通信テスト（[firmware/](../firmware/) と [pc/](../pc/) のスケルトン使用）

## 6. 既知の注意点

- **MAX98357A → INMP441 の機械クロストーク**: スピーカと MEMS マイクは筐体内で 40 mm 以上離し、防振フォームで挟む（spec.html §7.5 参照）
- **サーボ動作音の chirp 混入**: サーボ完了後 500 ms 以上の待機を入れてから chirp 放射（[spec.html §7.5](../docs/spec.html) シーケンス）
- **I²C プルアップは 1 セットのみ**: PCA9685 / LU9685 / BMP585 のモジュールに既存プルアップがある場合、バス上で合成されて値が変わるので注意（多くのブレイクアウトに 10 kΩ が乗っている）
- **3.3V ロジック で 5V サーボ電源**: PCA9685 のオープンドレイン PWM 出力は 5V トレラントなので問題なし
