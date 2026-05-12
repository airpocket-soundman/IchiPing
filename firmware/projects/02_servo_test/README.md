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

## トラブルシュート

- **PCA9685 が応答しない**: A0..A5 の I²C アドレス選択ピンの位置を確認、デフォルト 0x40
- **LU9685 が応答しない**: ジャンパで設定された I²C アドレス（0x00..0x1F）を確認、0x00 は General Call と被るので 0x01 以上にずらすのが安全
- **サーボがガタつく**: V+ の 5V 電源容量不足。USB バスパワーでは足りないので外部 5V 電源 + 1000 µF を必ず
