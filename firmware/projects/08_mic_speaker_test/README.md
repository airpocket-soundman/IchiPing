# 08_mic_speaker_test — マイク × スピーカ閉ループ

スピーカ (MAX98357A) で 200→8 kHz chirp を再生しながらマイク (INMP441) で 2.5 秒キャプチャ。**部屋のインパルス応答**を 1 ICHP フレームに詰めて 921600 bps の UART で PC に送る。

これは IchiPing の本命データ取得経路の最小版で、ここで CRC が通って WAV に保存できるようになると、あとは:
- 連続版 → [09_audio_stream](../09_audio_stream/)
- ラベル付き条件指定版 → [10_collector](../10_collector/)
にスムーズに繋がる。

## 期待する動作

```
[   1] firing chirp + recording 40000 samples...
   ↓ PC 側 receiver.py
[     1] t=  4123ms sr=16000 N=40000 servos=[  0.0, ...] CRC=OK (0.25 fps)
captures/08/frame_000001.wav  ← 1.5 MB の 16-bit mono WAV
```

判定: WAV を Audacity 等で開いて
- chirp の波形が見える（マイク経路 OK）
- chirp 終了後 ~500 ms に **late reflections**（残響）が残っている（部屋応答 OK）
- 高調波歪みやクリッピングが無い

## PC 側受信

```
cd pc
python receiver.py --port COM7 --baud 921600 --out ../captures/08
```

`captures/08/labels.csv` が 4 秒に 1 行ずつ追記される。

## 既知の TODO

- 現状の `play_blocking → record_blocking` は **直列実行**（chirp 終わってから録音開始）。本来は EDMA で **同時並行**にする必要がある（chirp と reflections の時間軸を揃えるため）。
- SAI0/SAI1 を別 PLL で駆動すると **サンプルクロックがずれる**。両方とも同じ PLL ソースに揃えるべき。`hardware_init.c` のクロック接続は要確認。
- INMP441 と MAX98357A は **物理的に離す**こと（直接振動結合で `room IR` ではなく機構的な振動を計測してしまう）。

## 配線

[06_mic_test](../06_mic_test/README.md) + [07_speaker_test](../07_speaker_test/README.md) の和集合。両方とも 5V / GND を共通化、SAI BCLK/FS は別系統。
