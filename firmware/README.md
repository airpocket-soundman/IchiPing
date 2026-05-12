# IchiPing firmware

MCUXpresso 用の最小スケルトン。**まだ実ハード（INMP441 マイク / MAX98357A / PCA9685 など）には触らず**、合成 chirp + 残響テールを生成して OpenSDA デバッグ UART に流すだけ。PC 側 ([../pc/receiver.py](../pc/receiver.py)) と組み合わせて、シリアル経路と保存パイプラインを先に通すための足場。

## 構成

```
firmware/
├── include/
│   ├── ichiping_frame.h   フレーム形式定義（PC 側と共有する単一の真実）
│   ├── dummy_audio.h
│   ├── servo_driver.h     共通サーボ API（PCA9685 / LU9685 をビルド時切替）
│   ├── pca9685.h          PCA9685 ネイティブ API（16ch, NXP）
│   └── lu9685.h           LU9685 ネイティブ API（20ch, 中国製互換品）
├── source/
│   ├── main.c             [Dummy emitter] SysTick + LPUART + フレーム送信ループ
│   ├── main_servo_test.c  [Servo test] servo_driver.h 経由でどちらでも動く
│   ├── ichiping_frame.c   ヘッダ + CRC-16/CCITT パッカー
│   ├── dummy_audio.c      合成 chirp 200→8 kHz + xorshift32 残響
│   ├── pca9685.c          PCA9685 ドライバ実装（PWM tick burst write）
│   └── lu9685.c           LU9685 ドライバ実装（角度バイト + bulk update）
├── host_build/            gcc/MinGW でホストビルドする足場
│   ├── Makefile           .so/.dll を作って ctypes 突合テストに使う
│   └── README.md
└── README.md / README.html
```

本リポは **2 つの独立ファーム**を提供する:

| ファーム | main | 用途 | SDK ベース |
|---|---|---|---|
| Dummy emitter | `main.c` | シリアル疎通とフレーム形式の確立（v0.1） | `lpuart/polling_transfer` |
| Servo test | `main_servo_test.c` | PCA9685 **または** LU9685 + SG90 ×5 の動作確認（v0.4 準備） | `lpi2c/polling_master` |

### サーボドライバの選択

Servo test は **PCA9685（NXP, 16 ch）** と **LU9685-20CU（中国製, 20 ch）** のどちらでも同じコードでビルドできる。`firmware/include/servo_driver.h` がビルド時シンボルでバックエンドを切り替える:

| バックエンドシンボル | チップ | チャネル数 | I²C アドレス | キャリブ | 必要なソース |
|---|---|---|---|---|---|
| `SERVO_BACKEND_PCA9685`（既定） | PCA9685 | 16 | 0x40（A0..A5 で 0x40..0x7F） | パルス幅を MIN/MAX_TICK で校正 | `pca9685.c` |
| `SERVO_BACKEND_LU9685_I2C` | LU9685-20CU | 20 | 0x00（ジャンパで 0x00..0x1F） | チップが角度→パルス変換するので不要 | `lu9685.c` |

`main_servo_test.c` は `servo_init` / `servo_set_deg` / `servo_set_first_n_deg` / `servo_all_off` の **共通 API** のみ呼ぶので、ハード差し替え時のコード変更はゼロ（Preprocessor シンボルを変えるだけ）。

## ビルド手順（MCUXpresso IDE / MCUXpresso for VS Code）

### A. Dummy emitter プロジェクト

1. **MCUXpresso SDK for FRDM-MCXN947** をインストール（11.9 以降）
2. `File > New > Create a new C/C++ Project` → `frdmmcxn947` → 例として `driver_examples > lpuart > polling_transfer` をベースに作成
3. プロジェクト名を `ichiping_dummy` などに変更
4. デフォルトの `main.c` を本リポの [source/main.c](source/main.c) で置き換え
5. プロジェクトに以下のソースを追加:
   - [source/ichiping_frame.c](source/ichiping_frame.c)
   - [source/dummy_audio.c](source/dummy_audio.c)
6. `Properties > C/C++ Build > Settings > Includes` に `${ProjDirPath}/../firmware/include` を追加（あるいはヘッダを `source/` 直下にコピー）
7. ビルドして OpenSDA で書き込み → 緑の `PWR` LED 想定箇所 (Arduino A0) は未配線でも問題なし

### B. Servo test プロジェクト

1. 同 SDK から、こんどは `driver_examples > lpi2c > polling_master` をベースに新規プロジェクトを作成
2. プロジェクト名を `ichiping_servo_test` などに変更
3. デフォルトの `main.c` を [source/main_servo_test.c](source/main_servo_test.c) で置き換え
4. **使うドライバに応じて 1 つだけ**ソースを追加:
   - PCA9685 の場合: [source/pca9685.c](source/pca9685.c)
   - LU9685 の場合: [source/lu9685.c](source/lu9685.c)
5. Include パスに `${ProjDirPath}/../firmware/include` を追加
6. **LU9685 を使う場合のみ**、`Project > Properties > C/C++ Build > Settings > Preprocessor > Defined symbols` に `SERVO_BACKEND_LU9685_I2C` を追加（PCA9685 は既定なのでシンボル追加不要）
7. **Config Tools の Pins ツール**で D14/D15 (Arduino) を `LPI2C4_SDA` / `LPI2C4_SCL` に割り当て、`pin_mux.c` に反映
8. ビルドして書き込み

> **配線必須:** 試験前に [../hardware/wiring.html §2.5](../hardware/wiring.html) を確認。
> 特に **V+ は外部 5V レール**（FRDM 3.3V では駆動電流が足りない）、
> V+ ↔ GND 間に 1000 µF 電解、サーボ GND は MCU GND と共通バーに集約。

> **LU9685 で動かない場合の確認順:**
> 1. 基板上のジャンパで設定された I²C アドレス（0x00..0x1F のどれか）を、現物の説明書 or テスターで確認 → `SERVO_DEFAULT_ADDR` を override する場合は `main_servo_test.c` の `servo_init` 呼び出し引数を編集
> 2. 0x00 はリザーブドアドレスなので、ジャンパで 0x01 以上にずらしておくのが安全
> 3. それでも応答が無ければボーレートを 100 kHz から 50 kHz に下げる（LU9685 はクロックストレッチが緩い個体がある）

## シリアル設定

| ファーム | ボーレート | フォーマット | 受け側 |
|---|---|---|---|
| Dummy emitter | **921600 bps** | バイナリ ICHP フレーム、8N1 | [../pc/receiver.py](../pc/receiver.py) で受ける（端末ソフトでは開かない） |
| Servo test | **115200 bps** | テキスト（PRINTF ステータス）、8N1 | TeraTerm / PuTTY / VS Code Serial Monitor で OK |

## 動作シーケンス（Dummy emitter）

![MCU v0.1 メインループ](../docs/img/mcu_sequence_v01.svg)

## 動作シーケンス（Servo test）

```
[boot] → BOARD init → SysTick 1ms → LPI2C4 init → pca9685_init(50Hz)
   │
   ▼
[main loop]                            ← 1 サイクル ≈ 11 秒
   Phase 1 — 同期ウェイポイント:
     all → 0°       hold 1.0 s
     all → 90°      hold 1.0 s
     all → 180°     hold 1.0 s
     all → 0°       hold 0.5 s
   Phase 2 — ソロスイープ (5 ch 順番):
     ch_n → 180°    hold 1.0 s
     ch_n → 0°      hold 0.5 s
```

各動作は OpenSDA UART (115200 bps) に PRINTF で進捗を出す。観測は VS Code 拡張 *Serial Monitor* か TeraTerm。

## フレーム形式

[include/ichiping_frame.h](include/ichiping_frame.h) が単一の真実（single source of truth）。PC 側は [../pc/ichp_frame.py](../pc/ichp_frame.py) の `HEADER_FMT` を同期させる。ドリフトすると CRC が通らず無音状態になるため、**ヘッダ変更時は必ず両方を更新**し `python -m unittest test_frame_format -v` で 9 テスト全パスを確認すること。

![ICHP フレーム形式](../docs/img/frame_format.svg)

> **ホストビルドの可能性**: `ichiping_frame.c` / `dummy_audio.c` は MCUXpresso SDK 非依存（`<stdint.h>` / `<string.h>` / `<math.h>` のみ）。gcc / MSVC で .dll/.so 化し、Python から ctypes で呼んで PC 側パッカーと突合テストできる（[host_build/](host_build/) 参照、タスク IP-0.1.7）。

## TODO（実ハード接続フェーズ）

- [ ] `BOARD_LED_INFER_*` を MCUXpresso Config Tools で定義 → `infer_led_on/off()` の `GPIO_PinWrite` を有効化
- [ ] `dummy_audio_generate` → I²S SAI1 RX からの DMA リングバッファ受信に置き換え
- [ ] サーボ角は PCA9685 への現在指令角度を返すように接続
- [ ] OpenSDA UART → **USB CDC** にアップグレード（フレーム転送 556 ms → 50 ms 程度）
- [ ] ホスト送 → MCU 受向きを足し、PC からのコマンド（収集セッション開始/サーボ角度指定）を受け付ける

## 参考

- バスとピンの割当: [../hardware/wiring.md](../hardware/wiring.md)
- 視覚配線図: [../hardware/wiring.svg](../hardware/wiring.svg)
- システム全体仕様: [../docs/spec.html](../docs/spec.html)
