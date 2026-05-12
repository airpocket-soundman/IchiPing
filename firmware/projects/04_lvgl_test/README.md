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

## トラブルシュート

- **画面が真っ黒のまま**: テスト 1 が通っているか先に確認。LVGL 統合より前にドライバ単体問題の可能性
- **`lv_init` が固まる**: `LV_MEM_SIZE` がスタック領域を食い潰している → `48 * 1024` を下げるかリンカで `_Min_Heap_Size` を増やす
- **FPS が 5 以下**: SPI クロックが遅い (8 MHz) かバスがバウンスしている → `ILI_SPI_BAUD_HZ` を 20–30 MHz に
- **タイル色が逆**: テスト 1 のトラブルシュート §「色が逆」と同じく MADCTL_BGR の問題
