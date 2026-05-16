# 02_servo_test — サーボ動作確認

PCA9685 **または** LU9685 経由で SG90 ×5 をスイープして配線確認する単体テストファーム。`servo_driver.h` の共通 API を経由するので、コードを変えずに 2 種類のサーボドライバ IC を切り替えられる。基板上の **SW3** でデモを開始/停止できる（起動直後は停止状態。サーボは 0° で待機）。

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

開始/停止トグルに使う **SW3 はオンボードボタン**（PORT0_6, 内部プルアップ + パッシブフィルタ済み）なので、追加配線は不要。

## 操作方法（SW3 で開始/停止）

| 状態 | シリアル表示 | サーボの挙動 |
|---|---|---|
| 起動直後 | `Press SW3 to start the demo; press again to pause.` | 0° にパーク（待機） |
| SW3 押下 | `[SW3] cycle RUNNING` | 動作シーケンス開始 |
| 動作中に SW3 再押下 | `[SW3] cycle PAUSED` | **次の phase 境界**で 0° に戻して待機 |

- 200 ms ソフトウェアデバウンス入り。チャタリングで多重トグルされない。
- 押下は GPIO0 falling-edge 割り込みで捕捉するので、`delay_ms()` ブロック中でも取りこぼさない。ただし停止指示が**実際にサーボへ反映されるのは現在の phase が終わるタイミング**（最大 ~1 s 遅延）。

## シリアル設定（PRINTF 観測用）

| 項目 | 値 |
|---|---|
| ポート | OpenSDA Debug VCP |
| ボーレート | **115200 bps** |
| 内容 | テキストの進行ログ |

## 動作シーケンス

SW3 で開始すると、以下を **1 サイクル**として繰り返す。再度 SW3 を押すと次の phase 境界で停止し 0° へ戻る。

| Phase | 内容 | 保持時間 |
|---|---|---|
| 1 — 同期ウェイポイント | 全 5 ch 一斉に `0° → 90° → 180° → 0°` | 各 1.0 s（末尾 0° のみ 0.5 s） |
| 2 — ソロスイープ | ch0 → ch4 の順に単独で `180° → 0°` を 1 往復<br>(ch0 = 窓 a, ch1 = 窓 b, ch2 = 窓 c, ch3 = 扉 AB, ch4 = 扉 BC) | 180° hold 1.0 s, 0° hold 0.5 s |
| 3 — スタガ・スロースイープ | 全 5 ch 同期で `0° → 180°` までスタガ付き段階移動（25 ms/step）、終了時に一括 `0°` 復帰 | 0° 復帰後 0.5 s |

## 確認方法

このファームは **テキスト PRINTF** なので、シリアルモニタを開くだけで OK。PC 側スクリプト不要。

### A. シリアルモニタで進行ログを観測

ツールは何でも可: VS Code Serial Monitor / TeraTerm / PuTTY / `python -m serial.tools.miniterm COM7 115200`

期待ログ例（**SW3 を押すまでは I²C スキャンの後で止まって見える**点に注意）:
```
IchiPing servo test  --  backend=PCA9685  addr=0x40  freq=50Hz  chans=5
Press SW3 to start the demo; press again to pause.

I2C scan (0x01..0x77):
  ACK at 0x40
I2C scan: 1 device(s) responded

OK: PCA9685 initialised (addr=0x40)
park bulk-write status=0 (0=OK, !=0=I2C error)

# ここで SW3 を 1 回押す
[SW3] cycle RUNNING

[cycle 1] phase 1: synchronised waypoints
  all -> 0°   (status=0)
  all -> 90°  (status=0)
  all -> 180° (status=0)
  all -> 0°   (status=0, rest)
[cycle 1] phase 2: solo sweep
  ch0 (window a) -> 180° (status=0)
  ch0 (window a) -> 0°   (status=0)
  ...
[phase 3] slow staggered sweep to 180°
  all -> 0° (instant return, status=0)
```

### B. 目視で 5 個のサーボが動くことを確認（本命）

シリアルログより**目視のほうが本命**。配線が間違っていてもログは進むので必ず実物を見る:

1. 電源投入直後はサーボが 0° で静止。**SW3 を 1 回押すと動き出す**。
2. Phase 1 で 5 個全部が同じ角度に動く（0° → 90° → 180° → 0°）
3. Phase 2 で 1 個ずつ順に 0° ↔ 180° を往復（ch0=窓 a, ch1=窓 b, ch2=窓 c, ch3=扉 AB, ch4=扉 BC）
4. Phase 3 で 5 個がスタガ付きでゆっくり 180° まで上昇し、最後に一括 0° へ戻る
5. 動作中に **SW3 を再度押す**と、シリアルに `[SW3] cycle PAUSED` が出て、次の phase 境界で 0° に戻って静止
6. 動かないチャネルがあれば配線か PCA9685 出力か個体不良
7. 全部が同じ角度で固まる → I²C 通信失敗（次項参照）

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
- **SW3 を押しても反応しない**: ① シリアルに `[SW3] cycle RUNNING` が出ているか確認 → 出ているのにサーボが動かなければ I²C/電源側。② 出ていなければ pin_mux で `SW3_InitPins()` が `BOARD_InitBootPins()` から呼ばれているか、`EnableIRQ(BOARD_SW3_IRQ)` が main で叩かれているかを確認。③ チャタリングで多重トグルしている場合は ISR 内の 200 ms デバウンス窓を伸ばす
