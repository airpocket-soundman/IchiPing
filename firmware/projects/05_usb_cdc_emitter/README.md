# 05_usb_cdc_emitter — USB CDC エミッタ（高速版）

01_dummy_emitter の OpenSDA UART 921600 bps を **USB Full-Speed (12 Mbps) CDC ACM** に置き換えたファーム。フレーム転送が ~560 ms → ~50 ms に短縮され、v0.3 以降の常時ストリームに対応できる。

## MCUXpresso でのインポート手順

1. `File > New > Create a new C/C++ Project`
2. SDK Wizard → ボード **`frdmmcxn947`** → ベース `usb_examples > usb_device_cdc_vcom`
3. プロジェクト名を `ichiping_usb_cdc_emitter` などに変更
4. SDK 例の `main.c` を本フォルダの [main.c](main.c) で **置き換え**
5. **以下は SDK 例から触らずに残す**（CDC ACM スタックの本体）:
   - `usb_device_descriptor.c` / `.h`
   - `virtual_com.c` / `.h`
   - `usb_device_config.h`
   - `usb_phy.c` / `.h`
6. プロジェクトに追加:
   - [`../../shared/source/ichiping_frame.c`](../../shared/source/ichiping_frame.c)
   - [`../../shared/source/dummy_audio.c`](../../shared/source/dummy_audio.c)
7. Include パスを追加:
   ```
   "${ProjDirPath}/../../firmware/shared/include"
   ```
8. **`virtual_com.c` に小改造**: `USB_DeviceCdcVcomCallback` の DTR 受信ハンドラで `g_UsbCdcVcomReady` フラグを立てる（本 main.c が参照する）。1〜2 行の追加で済む
9. ビルド → 書込

## 配線

| 用途 | コネクタ |
|---|---|
| 電源 + USB CDC | ボード上の **target USB-C (J21)** ← **OpenSDA USB (J17) ではない** |
| デバッグ + 書込 | OpenSDA USB (J17) を使う場合は SWD 経由で別途接続 |

J21 をホスト PC に挿すと、PC 側に新しい仮想 COM ポートが現れる（Windows なら「USB CDC Device」、Linux なら `/dev/ttyACM*`）。

## PC 側

**変更不要**。[`../../../pc/receiver.py`](../../../pc/receiver.py) と [`../../../pc/verify.py`](../../../pc/verify.py) は両方とも `pyserial` でシリアルポートを開いているだけで、CDC でも UART でも同じインターフェース。

```powershell
# OpenSDA UART のときと同じ
python receiver.py --port COMx --baud 921600 --out ../captures
```

`--baud` は CDC では無視されるが pyserial が値を要求するため適当な値で OK。

## スループット比較

| ファーム | 1 フレーム転送時間 | 最大フレームレート |
|---|---|---|
| 01_dummy_emitter (UART 921 600 bps) | ~560 ms | ~1 fps（3 秒周期想定） |
| **05_usb_cdc_emitter (USB FS 12 Mbps)** | **~50 ms** | **理論 ~10 fps、実用 5 fps 程度** |

これにより、v0.5 で chirp 放射 → 録音 → 即推論 → 即送信 の常時ストリームが現実的になる。

## トラブルシュート

- **PC が仮想 COM を認識しない**:
  - Windows: NXP の `usbser.sys` ドライバが必要なケース。Windows 10 以降は inbox driver で動くはず。デバイスマネージャで「ドライバの更新」
  - Linux/macOS: 標準で認識
- **`USB_DeviceCdcAcmSend` が失敗する**:
  - 大きなバッファ (64 KB) を一度に送ると一部の SDK 版ではエラー。512 B 単位に分割するか、SDK 例の `USB_DeviceCdcVcomTransfer` を使う
- **PC からは見えるがデータが来ない**:
  - `g_UsbCdcVcomReady` フラグが立っていない可能性。`virtual_com.c` の DTR ハンドラ追加を再確認
- **OpenSDA UART と同時利用**:
  - 同じ MCU の別 IF なので干渉なし。デバッグ用に PRINTF は OpenSDA UART (J17) に出したまま、データは USB CDC (J21) に流せる

## SDK の参考リソース

- NXP AN12153 — MCXN94x USB Device Stack Bring-up
- MCUXpresso SDK の `usb_examples/usb_device_cdc_vcom/readme.md`
- このプロジェクトはその readme の手順をベースに、send データを ICHP フレームに差し替えただけ
