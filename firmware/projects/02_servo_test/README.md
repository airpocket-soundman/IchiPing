# 02_servo_test — サーボ動作確認

PCA9685 **または** LU9685 経由で SG90 ×5 をスイープして配線確認する単体テストファーム。`servo_driver.h` の共通 API を経由するので、コードを変えずに 2 種類のサーボドライバ IC を切り替えられる。

## MCUXpresso でのインポート手順

1. `File > New > Create a new C/C++ Project`
2. SDK Wizard → ボード **`frdmmcxn947`** → ベース `driver_examples > lpi2c > polling_master`
3. プロジェクト名を `ichiping_servo_test` などに変更
4. 生成された `main.c` を本フォルダの [main.c](main.c) に置換
5. **使うドライバに応じて 1 つだけ** ソースを追加:
   - PCA9685 の場合: [`../../shared/source/pca9685.c`](../../shared/source/pca9685.c)
   - LU9685 の場合: [`../../shared/source/lu9685.c`](../../shared/source/lu9685.c)
6. Include パスを追加:
   ```
   "${ProjDirPath}/../../firmware/shared/include"
   ```
7. **LU9685 を使う場合のみ** Preprocessor Defined symbols に追加:
   ```
   SERVO_BACKEND_LU9685_I2C
   ```
   PCA9685 は既定なのでシンボル追加不要。
8. **Config Tools の Pins ツール**で D14/D15 を `LPI2C4_SDA` / `LPI2C4_SCL` に割当 → `pin_mux.c` に反映
9. ビルド → OpenSDA で書込

## 配線（必読）

詳細: [`../../../hardware/wiring.html`](../../../hardware/wiring.html) §2.2 / §2.5

| デバイス端子 | FRDM-MCXN947 | 注意 |
|---|---|---|
| SDA | D14 (FC4_I2C_SDA) | 4.7 kΩ プルアップ 1 セットのみ |
| SCL | D15 (FC4_I2C_SCL) | 同上 |
| VCC | 3V3 | ロジック電源 |
| **V+** | **外部 5V レール** | **FRDM 3V3 不可。1000 µF 電解必須** |
| GND | GND | サーボ GND も共通バーに |
| PWM0..4 | サーボ信号線 5 本 | 窓 a/b/c, 扉 AB/BC |

## シリアル設定（PRINTF 観測用）

| 項目 | 値 |
|---|---|
| ポート | OpenSDA Debug VCP |
| ボーレート | **115200 bps** |
| 内容 | テキストの進行ログ |

## 動作シーケンス

```
Phase 1 (同期ウェイポイント):
  all → 0°       hold 1.0 s
  all → 90°      hold 1.0 s
  all → 180°     hold 1.0 s
  all → 0°       hold 0.5 s

Phase 2 (ソロスイープ ×5 ch):
  ch_n → 180°    hold 1.0 s
  ch_n → 0°      hold 0.5 s
```

## 確認方法

このファームは **テキスト PRINTF** なので、シリアルモニタを開くだけで OK。PC 側スクリプト不要。

### A. シリアルモニタで進行ログを観測

ツールは何でも可: VS Code Serial Monitor / TeraTerm / PuTTY / `python -m serial.tools.miniterm COM7 115200`

期待ログ例:
```
IchiPing servo_test (backend=PCA9685, addr=0x40)
PCA9685 init OK @ 50 Hz
Phase 1 sync waypoints:
  all -> 0 deg   hold 1000 ms
  all -> 90 deg  hold 1000 ms
  ...
Phase 2 solo sweep:
  ch0 -> 180 deg
  ch0 -> 0 deg
  ...
```

### B. 目視で 5 個のサーボが動くことを確認（本命）

シリアルログより**目視のほうが本命**。配線が間違っていてもログは進むので必ず実物を見る:

1. Phase 1 で 5 個全部が同じ角度に動く（0° → 90° → 180° → 0°）
2. Phase 2 で 1 個ずつ順に 0° ↔ 180° を往復（ch0=窓 a, ch1=窓 b, ch2=窓 c, ch3=扉 AB, ch4=扉 BC）
3. 動かないチャネルがあれば配線か PCA9685 出力か個体不良
4. 全部が同じ角度で固まる → I²C 通信失敗（次項参照）

### C. I²C スキャン

`main.c` の `i2c_scan()` が起動直後に 0x01..0x77 を ACK 確認する。出力例:

```
I2C scan (0x01..0x77):
  ACK at 0x1F
I2C scan: 1 device(s) responded
```

- PCA9685: A0..A5 の I²C アドレス選択ピンを確認、デフォルト 0x40。プルアップ 4.7 kΩ × 2 が SDA/SCL に必要
- LU9685: ジャンパで 0x00..0x1F の範囲（**実機は 0x1F**）。スキャンの ACK 結果と `firmware/shared/include/lu9685.h` の `LU9685_DEFAULT_ADDR` が一致しているか確認

## トラブルシュート

- **PCA9685 が応答しない**: A0..A5 の I²C アドレス選択ピンの位置を確認、デフォルト 0x40
- **LU9685 が ACK は返すのにサーボが 0V で動かない**: 典型的に `LU9685_DEFAULT_ADDR` が 0x00 になっているケース。LU9685 は I²C General Call (0x00) にオプトイン応答するので LPI2C は `kStatus_Success` を返すが、コマンドは broadcast 扱いになり PWM は立たない。`i2c_scan()` の出力で実アドレスを確認し、`lu9685.h` の `LU9685_DEFAULT_ADDR` をそれに合わせる
- **サーボがガタつく**: V+ の 5V 電源容量不足。USB バスパワーでは足りないので外部 5V 電源 + 1000 µF を必ず
