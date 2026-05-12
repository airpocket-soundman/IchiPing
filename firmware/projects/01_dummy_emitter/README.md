# 01_dummy_emitter — シリアル疎通テスト

MCU 内で合成した chirp+残響をフレーム化し、OpenSDA UART (921600 bps) で PC に流す v0.1 の本番ファーム。

## MCUXpresso でのインポート手順

1. `File > New > Create a new C/C++ Project`
2. SDK Wizard → ボード **`frdmmcxn947`** → ベース `driver_examples > lpuart > polling_transfer`
3. プロジェクト名を `ichiping_dummy_emitter` などに変更
4. 生成された `main.c` を本フォルダの [main.c](main.c) に置換
5. 以下をプロジェクトに追加（リンクでもコピーでも可）:
   - [`../../shared/source/ichiping_frame.c`](../../shared/source/ichiping_frame.c)
   - [`../../shared/source/dummy_audio.c`](../../shared/source/dummy_audio.c)
6. `Project > Properties > C/C++ Build > Settings > MCU C Compiler > Includes` に追加:
   ```
   "${ProjDirPath}/../../firmware/shared/include"
   ```
7. ビルド → OpenSDA で書込

## シリアル設定

| 項目 | 値 |
|---|---|
| ポート | OpenSDA Debug VCP（デバイスマネージャの「COMx」） |
| ボーレート | **921600 bps** |
| フォーマット | バイナリ ICHP フレーム、8N1 フロー制御なし |
| 受け側 | [`../../../pc/receiver.py`](../../../pc/receiver.py) |

## 動作

3 秒周期で:
1. INFER LED ON
2. seq から決定論的なサーボ角 5 個を生成
3. `dummy_audio_generate()` で 32000 サンプルの chirp+残響
4. `ichp_pack_frame()` で 64038 B のフレーム化
5. `LPUART_WriteBlocking()` で送出 (~556 ms)
6. INFER LED OFF
7. 待機

詳細シーケンスは [firmware/README.html §動作シーケンス（Dummy emitter）](../../README.html) を参照。
