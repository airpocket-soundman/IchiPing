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
- INMP441 と MAX98357A は **物理的に離す**こと（直接振動結合で `room IR` ではなく機構的な振動を計測してしまう）。

## 配線（実機準拠 — SAI1 全二重, J1 ヘッダ）

[06_mic_test](../06_mic_test/README.md) + [07_speaker_test](../07_speaker_test/README.md) の **完全な和集合**。BCLK / LRC は **両デバイスで物理的に同じ J1 ピンを共有**（クロック源は SAI1 の TX フレーマ 1 個、サンプル間でドリフトしない）。出典: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) Table 17。

| 信号 | INMP441 | MAX98357A | J1 ピン | MCU pin | Alt | SDK 信号 | ソルダージャンパ |
|---|---|---|---|---|---|---|---|
| BCLK | SCK | BCLK | **J1.1** | P3_16 | Alt10 | `SAI1_TX_BCLK` | **SJ11: 1-2** |
| LRC / WS | WS | LRC | **J1.3** | P3_17 | Alt10 | `SAI1_TX_FS` | **SJ10: 2-3** |
| 録音データ | SD | — | **J1.15** | P3_21 | Alt10 | `SAI1_RXD0` | — |
| 再生データ | — | DIN | **J1.5** | P3_20 | Alt10 | `SAI1_TXD0` | — |
| VDD / VIN | 3V3 | **外部 5V レール** | — | — | — | — | — |
| GND | GND | GND | — | — | — | — | — |
| L/R | GND（Left 選択） | — | — | — | — | — | — |
| GAIN | — | フローティング (9 dB) | — | — | — | — | — |
| SD | — | VIN 直結（常時 ON） | — | — | — | — | — |

> **クロック構成**: TX フレーマが master（BCLK/FS を P3_16/P3_17 に出す）、RX フレーマは `kSAI_ModeSync` で TX のクロックを内部共有。`shared/source/sai_mic.c` + `sai_speaker.c` で同じ SAI1 ベースを共有しているので、`sai_mic_init` と `sai_speaker_init` を続けて呼ぶと同じ TX 設定が確立される（idempotent）。
>
> **重要 (注意したハマり)**: `sai_speaker_play_blocking` は完了時に **TX を disable しない**実装に変更済。disable してしまうと続けて呼ぶ `sai_mic_record_blocking` で RX が無音になる（sync mode は TX クロックに依存しているため）。明示停止は `sai_speaker_stop` で。
>
> **SJ10/SJ11**: 出荷時のデフォルトが SAI1 経路かは要確認。SJ11 が 2-3 だと P4_5 (SINC0)、SJ10 が 1-2 だと P4_13 (MC_ENC_I) にルーティングされる。
>
> **電源**: 外部 5V レールに **1000 µF 電解**を入れて MAX98357A の inrush を吸収。INMP441 の VDD（3V3）は MCU 3V3 から取って OK（電流 < 2 mA）。GND は外部 5V のグランドと共通バーで 1 点接地。
