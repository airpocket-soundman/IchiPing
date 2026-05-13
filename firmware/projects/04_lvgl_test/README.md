# 04_lvgl_test — LVGL 統合テスト

ILI9341 + LVGL v9 で IchiPing 状態 UI のモックを描画するファーム。テスト 1 が通ったあと、UI ライブラリの統合確認に使う。

## MCUXpresso でのインポート手順

1. `File > New > Create a new C/C++ Project`
2. SDK Wizard → ボード **`frdmmcxn947`** → ベース `driver_examples > lpspi > polling_b2b`
3. プロジェクト名を `ichiping_lvgl_test` などに変更
4. 生成された `main.c` を本フォルダの [main.c](main.c) に置換
5. プロジェクトに追加:
   - [`../../shared/source/ili9341.c`](../../shared/source/ili9341.c)
   - [`../../shared/source/lv_port_disp.c`](../../shared/source/lv_port_disp.c)
6. Include パスを追加:
   ```
   "${ProjDirPath}/../../firmware/shared/include"
   ```
7. **LVGL を取り込む**（本リポにはバンドルされていない）:
   - **SDK 経由**: SDK Wizard で `Components > Middleware > LVGL` を選択（v9.x 推奨）
   - **手動**: [github.com/lvgl/lvgl](https://github.com/lvgl/lvgl) v9 ブランチを clone して `firmware/third_party/lvgl/` に展開、ソース全部を Include / Source path に追加
8. **`lv_conf.h` を用意**（SDK の `lv_conf_template.h` をコピーして以下を上書き）:
   ```c
   #define LV_COLOR_DEPTH      16
   #define LV_COLOR_16_SWAP    0
   #define LV_USE_PERF_MONITOR 1
   #define LV_TICK_CUSTOM      0
   #define LV_MEM_SIZE         (48 * 1024)
   ```
9. **Config Tools の Pins ツール** はテスト 1 と同じ（D11/D13 → LPSPI3、A2..A5 → GPIO 出力）
10. ビルド → OpenSDA で書込

## 配線

[03_ili9341_test/README.md](../03_ili9341_test/README.md) と完全に同じ。配線はテスト 1 から変更不要。

## 動作

`SysTick_Handler` 内で `lv_tick_inc(1)` を呼び、main loop で `lv_timer_handler()` を回す。
UI は以下を 1.5 秒ステップで更新:

- **タイトル**: "IchiPing v0.1" (緑、24pt)
- **フェーズラベル**: 右上に step 番号
- **5 部屋タイル**: window a/b/c + door AB/BC、closed (緑) → half (橙) → open (赤) を index ずらしで巡回
- **信頼度バー**: 0–100% の sinusoidal-ish 動き
- **LV_USE_PERF_MONITOR**: 左下に FPS / CPU 使用率（lv_conf.h で有効化）

## メモリ使用量

| 項目 | サイズ |
|---|---|
| LVGL ライン buffer (40 行 × 320 px × 2 B × 2) | ~50 KB |
| LV_MEM_SIZE | 48 KB |
| LVGL コード | ~80 KB（Flash） |
| ILI9341 ドライバ | ~14 KB（Flash） |

MCXN947 (Flash 2 MB / SRAM 768 KB) には余裕の構成。

## 確認方法

このファームも **視覚での目視確認が本命**。PC 側スクリプト不要。

### A. TFT の目視確認

期待される表示:
1. 起動直後に "IchiPing v0.1" のタイトルが緑で出る
2. 5 個のタイル（window a, b, c, door AB, BC）が並ぶ
3. 1.5 秒ごとに各タイルの色が closed (緑) → half (橙) → open (赤) を巡回
4. 信頼度バーが画面下部で動き続ける
5. 左下の `LV_USE_PERF_MONITOR` に **FPS と CPU 使用率**がリアルタイム表示

### B. パフォーマンス確認（重要）

**FPS 20 以上 / CPU 50% 以下**を目標。これを満たさないと v1.0 の実画面更新でカクつく:

| 状態 | 判定 | 対処 |
|---|---|---|
| FPS 30+, CPU 30% | ◎ 余裕 | そのまま v1.0 統合へ |
| FPS 20-30, CPU 50-70% | ○ ボーダー | partial blit の窓サイズを調整 |
| FPS < 20 | ✗ NG | SPI クロックを 20→30 MHz、または LV_MEM_SIZE 増 |
| FPS < 5 | ✗ 致命的 | SPI 配線がバウンスしている疑い、まず 03 に戻る |

### C. シリアルログを補助確認

115200 bps テキストで `lv_log` 出力が見える:
```
LVGL v9.x init OK
Display registered: 320x240
[Info] frame rate 24 fps
```

### D. PC 側スクリプト

**不要**。LVGL のレンダリング結果は MCU 側で完結する。

## トラブルシュート

- **画面が真っ黒のまま**: テスト 1 が通っているか先に確認。LVGL 統合より前にドライバ単体問題の可能性
- **`lv_init` が固まる**: `LV_MEM_SIZE` がスタック領域を食い潰している → `48 * 1024` を下げるかリンカで `_Min_Heap_Size` を増やす
- **FPS が 5 以下**: SPI クロックが遅い (8 MHz) かバスがバウンスしている → `ILI_SPI_BAUD_HZ` を 20–30 MHz に
- **タイル色が逆**: テスト 1 のトラブルシュート §「色が逆」と同じく MADCTL_BGR の問題
