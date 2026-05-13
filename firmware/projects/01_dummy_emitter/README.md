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

## 確認方法

> **⚠ 注意:** このファームはバイナリフレームを吐くので、TeraTerm / PuTTY / VS Code Serial Monitor で開いても文字化けする。**必ず [pc/receiver.py](../../../pc/receiver.py) で受ける**こと。

### A. PC 側受信スクリプトで実機フレームを受け取る（本筋）

```powershell
cd pc
conda activate ichiping
python receiver.py --port COM7 --baud 921600 --out ../captures
```

期待ログ:
```
[     0] t=    3001ms sr=16000 N=32000 servos=[ 17.0, ... ] CRC=OK (0.33 fps)
[     1] t=    6002ms sr=16000 N=32000 servos=[ 33.0, ... ] CRC=OK (0.33 fps)
```

成功条件:
- `CRC=OK` が連続で出る
- `../captures/labels.csv` に行が追記される
- `../captures/frame_000000.wav` が増えていく（メディアプレーヤで再生すれば chirp が聞こえる）

### B. verify.py で 8 項目自動チェック（推奨）

```powershell
python verify.py --port COM7 --frames 20 --strict
```

seq 単調増加、CRC 全数 OK、サンプル数固定、chirp の整合フィルタピーク位置などをまとめて自動判定。1 件でも FAIL なら exit 1。

### C. PC 受信パイプラインの単体テスト（実機なしで先に通す）

ハードを繋ぐ前に、受信・パッカー・CRC 実装が壊れていないことを単体テストで確認:

```powershell
cd pc
python -m unittest test_frame_format test_loopback -v
```

| テスト | 件数 | カバレッジ |
|---|---|---|
| `test_frame_format` | 9 | ヘッダ長 36 B、各フィールドのバイトオフセット、CRC-16/CCITT-FALSE の既知ベクタ、pack→unpack ラウンドトリップ |
| `test_loopback` | 6 | `emulator.py` の偽フレームを `receiver.py` で E2E 解釈、chirp の構造、サーボ角の C 互換性 |

gcc/MinGW があれば `test_ctypes_packer` も走り、C 側 `ichp_pack_frame` と Python 側 `pack_frame` のバイト一致を 2 件で検証（無ければ自動 skip）。

### D. 実機なしで loopback テスト

MCU が無い段階でも `pc/emulator.py` で同じフレーム形式の偽ストリームを生成できる:

```powershell
# Terminal 1: 偽 MCU が TCP で配信
python emulator.py --tcp 127.0.0.1:5000 --frames 10

# Terminal 2: 受信
python receiver.py --tcp 127.0.0.1:5000 --out ../captures_dummy
```

または file 経由:

```powershell
python emulator.py --out fake_stream.bin --frames 10
python receiver.py --in fake_stream.bin --out ../captures_dummy
```

### E. OpenSDA 起動ログ（補助）

main.c 冒頭の `PRINTF("IchiPing dummy emitter: ...\r\n")` が起動直後に 1 行だけテキストで流れる。直後にバイナリフレームが流れ込むので一瞬しか見えない — 完全に死んでないかの最後の砦として扱う。本筋の確認は A/B/C/D で。
