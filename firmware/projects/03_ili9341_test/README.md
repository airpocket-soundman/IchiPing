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
7. **Pins tool 不要** — 本リポの [pin_mux.c](frdmmcxn947_cm33_core0/pins/pin_mux.c) は FRDM-MCXN947 Board User Manual Tables 18 / 20 通りに既に設定済（LPSPI1 = FC1 on D11/D13, GPIO on A2..A5）
8. ビルド → OpenSDA で書込

## 配線（実機準拠 — LPSPI1 / FC1, J2 + J4 ヘッダ）

出典: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) Tables 18 & 20。

| ILI9341 | Arduino | FRDM J ピン | MCU pin | Alt | 信号名 | ソルダージャンパ | 備考 |
|---|---|---|---|---|---|---|---|
| VCC | — | — | — | — | — | — | 3V3（FRDM オンボード LDO） |
| GND | — | — | — | — | — | — | 共通グランド |
| **CS** | A2 | **J4.6** | P0_14 | Alt0 (GPIO) | `ARD_A2` | — (直結) | Chip Select、ソフト駆動 |
| **RESET** | A3 | **J4.8** | P0_22 | Alt0 (GPIO) | `ARD_A3` | — (直結) | Hard reset（init 時 LOW パルス） |
| **DC** | A4 | **J4.10** | P0_15 | Alt0 (GPIO) | `ARD_A4` | **SJ8: 1-2** (default) | Data/Command 切替 |
| **SDI / MOSI** | D11 | **J2.8** | P0_24 | Alt2 (FC1) | `FC1_SPI_SDO` | **SJ7: 1-2** (default) | LPSPI1 master out |
| **SCK** | D13 | **J2.12** | P0_25 | Alt2 (FC1) | `FC1_SPI_SCK` | — (直結) | LPSPI1 クロック |
| **LED / BL** | A5 | **J4.12** | P0_23 | Alt0 (GPIO) | `ARD_A5` | **SJ9: 1-2** (default) | バックライト、ON/OFF or PWM。SJ9=2-3 だと Wakeup ピンになる |
| SDO / MISO | D12 | J2.10 | P0_26 | Alt2 (FC1) | `FC1_SPI_SDI` | — | ILI は書込専用なので未接続でも OK。本計画では microSD 不採用なので使用しないが、ピンは muxed したまま残してある |
| T_* (touch) | — | — | — | — | — | — | 本プロジェクトでは未使用 |

> **重要 1**: 旧 README は SPI を "FC3 / LPSPI3" と書いていたが、これは **誤り**。FRDM-MCXN947 では D10..D13 は **FC1** にルーティングされている（BUM Table 18）。`app.h` の `BOARD_ILI_SPI_BASE` も `LPSPI1` に修正済。
>
> **重要 2**: A5 (P0_23) は **SJ9 を default の 1-2** にしておく必要あり。2-3 だと Wakeup ピンに切り替わって GPIO 出力できない。
>
> **重要 3**: D10 (P0_27 = `FC1_SPI_PCS`) は SPI のハードウェア CS だが、本プロジェクトでは **A2 (P0_14) をマニュアル CS** として使うので D10 は muxed せず GPIO のまま。これにより **SJ6 は default 2-3 のままで LED_GREEN がオンボードで点灯可能**。本計画では microSD は採用しないので、D10 と LED_GREEN は引き続き SJ6=2-3 デフォルトで利用できる。

## 動作

cycle 内で次の 5 フェーズが順に流れる:

1. **全画面塗りつぶし**: 黒/赤/緑/青/白の順 — RGB565 バイト順と MADCTL 回転の確認
2. **入れ子矩形**: シアン/マゼンタ/黄/白の同心矩形 — CASET/PASET の non-zero 起点動作
3. **テキスト + バックライトパルス**: タイトル「IchiPing」+ バージョン行 — 5×7 フォント描画と BL 配線
4. **5 部屋タイル モック**: window a/b/c + door AB/BC の色付きタイル — 状態 UI の予習
5. **ピクセル スイープ**: 4 行ずつ色帯 — partial blit / window rollover の検出

`OpenSDA Debug VCP @ 115200` で進行ログが流れる。

## 確認方法

このファームは **視覚での目視確認が本命**。PC 側スクリプト不要。シリアルは進行ログだけ。

### A. TFT の目視確認

5 フェーズの 1 サイクル ≈ 8 秒。期待される画面:

| フェーズ | 見え方 | 失敗時の意味 |
|---|---|---|
| 1. 単色塗り | 黒→赤→緑→青→白 | 真っ白＝ドライバ未反応 / 色が逆＝BGR/RGB |
| 2. 入れ子矩形 | シアン/マゼンタ/黄/白の同心矩形 | 位置ずれ＝座標設定が壊れている |
| 3. テキスト | "IchiPing" + "v0.1" の文字 | 文字化けあり＝SPI クロック過剰 |
| 4. 5 タイル | 5 個の色付き矩形（window/door） | 枠線ズレ＝MADCTL 回転設定 |
| 5. ピクセルスイープ | 4 行ずつ色帯が流れる | 縞模様の崩れ＝幅指定誤り |

### B. シリアルモニタで進行ログを補助確認

115200 bps テキスト。VS Code Serial Monitor / TeraTerm / PuTTY のいずれでも可:

```
ILI9341 init OK
Phase 1: solid fills...
Phase 2: nested rects...
...
cycle done
```

視覚と組み合わせて、どのフェーズで止まったか・化けたかを切り分ける。

### C. PC 側スクリプト

**不要**。このファームはホスト送信を一切しない。

## トラブルシュート

- **画面が真っ白**: バックライト ON でドライバ反応なし → CS / DC / RESET 配線、SPI クロック極性
- **色が逆**（赤と青が入れ替わる）: MADCTL の BGR ビットを反転、または `ili9341.c` の `MADCTL_BGR` を外して再ビルド
- **画面が 90°回転している**: `lcd.rotation` を `ILI9341_ROT_PORTRAIT` 系に変更（既定 `LANDSCAPE`）
- **SPI クロック早すぎでノイズ**: `ILI_SPI_BAUD_HZ` を 20 MHz → 8 MHz に下げる
