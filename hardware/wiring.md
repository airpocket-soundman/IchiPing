# IchiPing 配線テーブル（v1, FRDM-MCXN947 / INMP441 想定）

主要な配線図は [wiring.svg](wiring.svg)、機械可読ネットリストは [netlist.csv](netlist.csv)。本ドキュメントは **MCU ピン → 接続先デバイス端子** の関係を表形式で整理したもの。

## 1. バス割り当てサマリ

| バス | MCU 周辺 | 接続先デバイス | アドレス/CS | Arduino ヘッダ |
|---|---|---|---|---|
| I²S RX | SAI1 | INMP441（マイク） | — | SAI ブレイクアウト |
| I²S TX | SAI0 | MAX98357A → 8Ω スピーカ | — | SAI ブレイクアウト |
| I²C | FC4（共有） | PCA9685 / SSD1306 / BMP585 | 0x40 / 0x3C / 0x47 | D14 (SDA), D15 (SCL) |
| SPI | FC3 | microSD（FatFs） | CS=D10 | D10–D13 |
| UART | FC2 | ESP32-WROOM（任意） | — | D0, D1 |
| USB | USB0 | PC（CDC データ転送） | — | オンボード USB-C |
| PWM | （PCA9685 経由） | SG90 ×5 | PCA ch 0–4 | — |
| GPIO IN | — | トグル ×5 + EXEC ×1 + BMP INT | 内蔵プルアップ | D2–D9 |
| GPIO OUT | — | LED ×2 | 330Ω 直列 | A0, A1 |

## 2. MCU ピン → デバイス端子 マップ

> **注**: Arduino ヘッダ番号（D0–D15、A0–A5）は FRDM-MCXN947 ボード上の刻印。実 P-port 番号は **MCUXpresso Config Tools の Pins ツール**で確定すること（pin_mux.c に反映）。

### 2.1 I²S（音響系）

| Arduino/Header | MCU 機能 | 接続先 | 信号 | 方向 |
|---|---|---|---|---|
| SAI ブレイクアウト | `SAI1_RX_BCLK` | INMP441 | SCK | MCU → MIC |
| SAI ブレイクアウト | `SAI1_RX_SYNC` | INMP441 | WS（LRCLK） | MCU → MIC |
| SAI ブレイクアウト | `SAI1_RX_DATA` | INMP441 | SD（DATA） | MIC → MCU |
| SAI ブレイクアウト | `SAI0_TX_BCLK` | MAX98357A | BCLK | MCU → DAC |
| SAI ブレイクアウト | `SAI0_TX_SYNC` | MAX98357A | LRC | MCU → DAC |
| SAI ブレイクアウト | `SAI0_TX_DATA` | MAX98357A | DIN | MCU → DAC |

INMP441 の `L/R` ピンは **GND に接続**（左チャネル選択）。MAX98357A の `GAIN` は無接続（9 dB デフォルト）、`SD` は 3.3V 直結（常時 ON）。

### 2.2 I²C（共有バス FC4, 4.7 kΩ プルアップ ×2）

| Arduino | MCU 機能 | I²C アドレス | デバイス | 役割 |
|---|---|---|---|---|
| D14 | `FC4_I2C_SDA` | 0x40 | PCA9685 | サーボ PWM ドライバ（[firmware/shared/source/pca9685.c](../firmware/shared/source/pca9685.c)） |
| D15 | `FC4_I2C_SCL` | 0x00–0x1F | LU9685（代替） | 同上、20 ch 中華製互換品（[firmware/shared/source/lu9685.c](../firmware/shared/source/lu9685.c)） |
|  |  | 0x47 | BMP585 | 気圧センサ（補助） |

> ディスプレイは I²C ではなく **SPI 接続の ILI9341 240×320 カラー TFT** を採用（[display_options.html](display_options.html) §選定理由 参照）。OLED 候補は v1 では非採用。

### 2.3 SPI（microSD + ILI9341, FC3 共有）

| Arduino | MCU 機能 | デバイス端子 | 備考 |
|---|---|---|---|
| D10 | `FC3_SPI_CS` | microSD CS | microSD 専用 CS |
| D11 | `FC3_SPI_MOSI` | microSD MOSI / ILI9341 SDI | バス共有 |
| D12 | `FC3_SPI_MISO` | microSD MISO | ILI9341 SDO は未接続（書込のみ） |
| D13 | `FC3_SPI_SCK` | microSD SCK / ILI9341 SCK | バス共有 |
| **A2** | GPIO 出力 | **ILI9341 CS** | 別 CS（microSD と排他選択） |
| **A3** | GPIO 出力 | **ILI9341 RESET** | Hard reset (init 時のみ low パルス) |
| **A4** | GPIO 出力 | **ILI9341 DC** | Data/Command 切替 |
| **A5** | GPIO 出力 (任意 PWM) | **ILI9341 LED/BL** | バックライト制御 |

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
| 3.3V | FRDM オンボード LDO | MCU、INMP441、SSD1306、BMP585、microSD、PCA9685 VCC（ロジック） |
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
2. **I²C** を結線 → PCA9685 → SSD1306 → BMP585 を `i2cdetect` 相当のコードで全アドレス確認（0x40 / 0x3C / 0x47）
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
- **I²C プルアップは 1 セットのみ**: PCA9685 / SSD1306 / BMP585 のモジュールに既存プルアップがある場合、バス上で合成されて値が変わるので注意（多くのブレイクアウトに 10 kΩ が乗っている）
- **3.3V ロジック で 5V サーボ電源**: PCA9685 のオープンドレイン PWM 出力は 5V トレラントなので問題なし
