# 03_ili9341_test — ILI9341 単体描画テスト

ILI9341 240×320 RGB565 TFT を SPI で叩いて、配線・回転設定・色順を 5 フェーズで一気に検証するファーム。LVGL を載せる前の最小確認。

## MCUXpresso でのインポート手順

1. `File > New > Create a new C/C++ Project`
2. SDK Wizard → ボード **`frdmmcxn947`** → ベース `driver_examples > lpspi > polling_b2b`
3. プロジェクト名を `ichiping_ili9341_test` などに変更
4. 生成された `main.c` を本フォルダの [main.c](main.c) に置換
5. プロジェクトに追加:
   - [`../../shared/source/ili9341.c`](../../shared/source/ili9341.c)
6. Include パスを追加:
   ```
   "${ProjDirPath}/../../firmware/shared/include"
   ```
7. **Config Tools の Pins ツール** で以下を設定:
   - **D11** → `LPSPI3_SDO`
   - **D13** → `LPSPI3_SCK`
   - **A2, A3, A4, A5** → GPIO 出力（push-pull、プルなし）
   - 必要に応じて `pin_mux.c` 内の `BOARD_*_PORT/_PIN` マクロ定義名と `main.c` 冒頭の `ILI_*_GPIO/PIN` 定義を一致させる
8. ビルド → OpenSDA で書込

## 配線

詳細: [`../../../hardware/wiring.html`](../../../hardware/wiring.html) §2.3 / §2.7

| ILI9341 | FRDM-MCXN947 | 機能 |
|---|---|---|
| VCC | 3V3 | 電源 |
| GND | GND | 共通グランド |
| CS | A2 | Chip Select (GPIO out) |
| RESET | A3 | Hard reset (GPIO out) |
| DC | A4 | Data/Command 切替 (GPIO out) |
| SDI/MOSI | D11 (FC3_SPI_MOSI) | microSD と共有 |
| SCK | D13 (FC3_SPI_SCK) | microSD と共有 |
| LED/BL | A5 | バックライト (GPIO out or PWM) |
| SDO/MISO | 未接続 | 書込のみ |

## 動作

cycle 内で次の 5 フェーズが順に流れる:

1. **全画面塗りつぶし**: 黒/赤/緑/青/白の順 — RGB565 バイト順と MADCTL 回転の確認
2. **入れ子矩形**: シアン/マゼンタ/黄/白の同心矩形 — CASET/PASET の non-zero 起点動作
3. **テキスト + バックライトパルス**: タイトル「IchiPing」+ バージョン行 — 5×7 フォント描画と BL 配線
4. **5 部屋タイル モック**: window a/b/c + door AB/BC の色付きタイル — 状態 UI の予習
5. **ピクセル スイープ**: 4 行ずつ色帯 — partial blit / window rollover の検出

`OpenSDA Debug VCP @ 115200` で進行ログが流れる。

## トラブルシュート

- **画面が真っ白**: バックライト ON でドライバ反応なし → CS / DC / RESET 配線、SPI クロック極性
- **色が逆**（赤と青が入れ替わる）: MADCTL の BGR ビットを反転、または `ili9341.c` の `MADCTL_BGR` を外して再ビルド
- **画面が 90°回転している**: `lcd.rotation` を `ILI9341_ROT_PORTRAIT` 系に変更（既定 `LANDSCAPE`）
- **SPI クロック早すぎでノイズ**: `ILI_SPI_BAUD_HZ` を 20 MHz → 8 MHz に下げる
