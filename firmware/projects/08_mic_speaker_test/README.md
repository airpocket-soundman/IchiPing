# 08_mic_speaker_test — マイク × スピーカ閉ループ

スピーカ (MAX98357A) で 200→6 kHz chirp を再生しながらマイク (INMP441 / MSM261) で 2.5 秒キャプチャ。**部屋のインパルス応答**を 1 ICHP フレームに詰めて 921600 bps の UART で PC に送る。

これは IchiPing の本命データ取得経路の最小版で、ここで CRC が通って WAV に保存できるようになると、あとは:
- 連続版 → [09_audio_stream](../09_audio_stream/)
- ラベル付き条件指定版 → [10_collector](../10_collector/)
にスムーズに繋がる。

## 動作概要

| 項目 | 値 |
|---|---|
| サンプルレート | 16 kHz |
| chirp | 200 → 6000 Hz 線形スイープ, 2.0 s, 両端 5 ms raised-cosine フェード |
| 録音窓 | 2.5 s (= chirp 2.0 s + late reflections 500 ms) |
| サイクル | 5 s 間隔（捕捉 2.5 s + UART 送信 ~0.9 s + 残りスリープ） |
| ソフト音量 | 0.15 (約 -16 dB; 07_speaker_test と同じ。MAX98357A GAIN ピン GND で +3 dB ハード) |
| 起動状態 | **PAUSED** — SW3 押下で RUNNING/PAUSED トグル |
| chirp ↔ 録音同期 | 同一 SAI1 ループでインターリーブ（TX FIFO 深さ分 ~8 サンプル / 500 µs のオフセット） |
| マイク取込精度 | f32 で取って int16 ペイロードに変換（full dynamic range 保持） |

起動時に SW3 を押すまで音は鳴らず、シリアルには:

```
IchiPing 08_mic_speaker_test  --  SAI1 TX chirp + SAI1 RX capture
  Fs=16000 Hz, chirp=32000 samp (200..6000 Hz), window=40000 samp, cycle=5000 ms
Press SW3 to start the cycle; press again to pause.
[SAI MIC] sai_clk=... TCSR=0x9017xxxx ...
[SAI] sai_clk=... TCSR=0x9017xxxx ...
```

の後、SW3 押下後にサイクルが回る:

```
[SW3] cycle RUNNING
[   1] firing chirp + capturing 40000 samples...
  [ICHP] seq=1 bytes=80038 crc=0x37a2 peak=18432 rms=2104
```

PC 側 receiver.py からは:

```
[     1] t=  5123ms sr=16000 N=40000 servos=[  0.0, ...] CRC=OK (0.20 fps)
captures/08/frame_000001.wav  ← 約 80 kB の 16-bit mono WAV
```

判定: WAV を Audacity 等で開いて
- chirp の波形が見える（マイク経路 OK）
- chirp 終了後 ~500 ms に **late reflections**（残響）が残っている（部屋応答 OK）
- 高調波歪みやクリッピングが無い（peak < 30000、rms 数百〜数千が目安）

## PC 側受信

```
cd pc
python receiver.py --port COM7 --baud 921600 --out ../captures/08
```

`captures/08/labels.csv` が 5 秒に 1 行ずつ追記される。

## 06/07 からの差分まとめ

| 項目 | 06_mic_test | 07_speaker_test | 08（このプロジェクト） |
|---|---|---|---|
| SAI1 方向 | RX のみ（sync, TX は clock-only master） | TX のみ | **TX + RX 全二重** |
| 取込形式 | f32 (`record_blocking_f32`) | — | **f32 取込 → int16 ペイロード変換** |
| 再生信号 | — | 200/1k/5k 純音 + 200→6k chirp + 無音 | **200→6k chirp 単発** |
| chirp 終端 | — | 6 kHz (Nyquist 回避) | 6 kHz (07 の知見を引き継ぎ) |
| フェード | — | 5 ms raised-cosine | 5 ms raised-cosine |
| ソフト音量 | — | 0.15 | 0.15 |
| SW3 | なし | あり (toggle) | **あり (toggle)** |
| PC 出力 | シリアル統計のみ | なし | **ICHP フレーム → WAV** |

## 既知の制約

- chirp 再生と録音は **同一 CPU ループでインターリーブ実行**しているため、TX FIFO 深さ (8 サンプル ≈ 500 µs at 16 kHz) 分の同期オフセットが入る。500 ms の reflections 窓内に十分収まるため IR 解析には支障なし。完全アライメントが必要なら EDMA リング化（[shared/source/sai_mic.c](../../shared/source/sai_mic.c) の `sai_mic_start_streaming` プレースホルダ）を v0.2 で実装。
- INMP441 と MAX98357A は **物理的に離す**こと（直接振動結合で `room IR` ではなく機構的な振動を計測してしまう）。10 cm 以上、できれば別の基板に分離。

## 配線（実機準拠 — SAI1 全二重, J1 ヘッダ）

[06_mic_test](../06_mic_test/README.md) + [07_speaker_test](../07_speaker_test/README.md) の **完全な和集合**。BCLK / LRC は **両デバイスで物理的に同じ J1 ピンを共有**（クロック源は SAI1 の TX フレーマ 1 個、サンプル間でドリフトしない）。出典: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf) Table 17。

| 信号 | INMP441 | MAX98357A | J1 ピン | MCU pin | Alt | SDK 信号 | ソルダージャンパ |
|---|---|---|---|---|---|---|---|
| BCLK | SCK | BCLK | **J1.1** | P3_16 | Alt10 | `SAI1_TX_BCLK` | SJ11: 1-2 (デフォルト) |
| LRC / WS | WS | LRC | **J1.11** | P3_17 | Alt10 | `SAI1_TX_FS` | — (SJ なし、直結) |
| 録音データ | SD | — | **J1.15** | P3_21 | Alt10 | `SAI1_RXD0` | — |
| 再生データ | — | DIN | **J1.5** | P3_20 | Alt10 | `SAI1_TXD0` | — |
| VDD / VIN | 3V3 | **外部 5V レール** | — | — | — | — | — |
| GND | GND | GND | — | — | — | — | — |
| L/R | GND（Left 選択） | — | — | — | — | — | — |
| GAIN | — | **GND 直結 (3 dB)** | — | — | — | — | デモ用 0.25 W スピーカ保護。v1+ で 1〜3 W に上げるならフローティング (9 dB) に戻す |
| SD | — | VIN 直結（常時 ON） | — | — | — | — | — |

> **クロック構成**: TX フレーマが master（BCLK/FS を P3_16/P3_17 に出す）、RX フレーマは `kSAI_ModeSync` で TX のクロックを内部共有。`shared/source/sai_mic.c` + `sai_speaker.c` で同じ SAI1 ベースを共有しているので、`sai_mic_init` と `sai_speaker_init` を続けて呼ぶと同じ TX 設定が確立される（idempotent）。
>
> **重要 (注意したハマり)**: `sai_speaker_play_blocking` は完了時に **TX を disable しない**実装に変更済。disable してしまうと続けて呼ぶ `sai_mic_record_blocking` で RX が無音になる（sync mode は TX クロックに依存しているため）。明示停止は `sai_speaker_stop` で。08 はこれらを単独で呼ばず、main.c 内の `chirp_and_capture()` で TX/RX 両 FIFO を 1 ループに統合してインターリーブ実行している。
>
> **SJ 設定**: 出荷時デフォルトのままで OK。SJ11 はデフォルト 1-2 で J1.1 = `SAI1_TX_BCLK`、SJ10 はデフォルト 1-2 のまま (J1.3 = `MC_ENC_I` 未使用)。`SAI1_TX_FS` は J1.3 ではなく **J1.11** から取ることでジャンパ操作を完全に不要にしている。BUM Table 17 を参照。
>
> **電源**: 外部 5V レールに **1000 µF 電解**を入れて MAX98357A の inrush を吸収。INMP441 の VDD（3V3）は MCU 3V3 から取って OK（電流 < 2 mA）。GND は外部 5V のグランドと共通バーで 1 点接地。
