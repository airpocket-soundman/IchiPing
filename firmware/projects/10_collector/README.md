# 10_collector — PC 制御ラベル付きデータ採取

PC 側から **窓サイズ / サンプルレート / 励起信号 / 反復回数 / ラベル** を指定して学習データを採取する **双方向**プロジェクト。OpenSDA UART 1 本に ASCII コマンドと ICHP バイナリフレームを多重化する。

## アーキテクチャ

```
[PC: collector_client.py]                     [MCU: 10_collector]
    │  SET / GET / START / STOP / PING (ASCII)
    │ ─────────────────────────────────────────►  ichp_cmd_feed_byte()
    │                                                   ↓
    │  ← "OK ..." / "ERR ..." / "INFO ..." 1 行         状態更新
    │ ◄─────────────────────────────────────────
    │
    │  ← ICHP frame N 個（バイナリ, 36B header + N×2B + CRC）
    │ ◄─────────────────────────────────────────  run_one() ×repeats
```

PC 側は `ICHP` magic を見つけたらバイナリ取り出し、それ以外は ASCII 行として扱う。両者の境界は magic 一致で確定する（ASCII 中に `ICHP` の 4 連続バイトが出る確率は OK / INFO 行では非常に低い）。

## サポートコマンド（[shared/include/ichp_cmd.h](../../shared/include/ichp_cmd.h) 参照）

| コマンド | 例 | 効果 |
|---|---|---|
| `SET window <N>` | `SET window 32000` | 1 回の録音サンプル数 |
| `SET rate <Hz>` | `SET rate 16000` | サンプリング周波数 |
| `SET tone <kind>` | `SET tone chirp` | 励起信号: `chirp` / `tone200` / `tone1k` / `tone5k` / `silence` |
| `SET repeats <N>` | `SET repeats 30` | START 時の繰り返し回数 |
| `SET label <str>` | `SET label door_open` | フレームの label メタ (≤ 19 chars) |
| `GET` | `GET` | 現在の設定を OK 行で返す |
| `START` | `START` | 設定で N 回録音し ICHP フレーム連送 |
| `STOP` | `STOP` | 実行中の run を中断 |
| `PING` | `PING` | `OK PONG <build_time>` 応答 |

## 使い方

### インタラクティブ

```
cd pc
python collector_client.py --port COM7 --out ../captures/10
> SET label door_closed
> SET tone chirp
> SET window 32000
> SET repeats 20
> START
```

各録音終了ごとに `captures/10/door_closed_<epoch>_NNNNNN.wav` と `labels.csv` の 1 行が追加される。

### スクリプト（条件×繰返しを一発で）

```json
[
  {"label": "door_closed", "tone": "chirp",   "repeats": 30},
  {"label": "door_half",   "tone": "chirp",   "repeats": 30},
  {"label": "door_open",   "tone": "chirp",   "repeats": 30},
  {"label": "amb_silence", "tone": "silence", "repeats": 10}
]
```

```
python collector_client.py --port COM7 --plan plan.json --out ../captures/10
```

## ICHP フレーム内 servo_deg[5] の意味

このプロジェクトでは servo_deg[] を **メタ情報スロット**として再利用する:

| 添字 | 意味 |
|---|---|
| `servo_deg[0]` | window_samples |
| `servo_deg[1]` | rate_hz |
| `servo_deg[2]` | tone enum (0=chirp, 1=200, 2=1k, 3=5k, 4=silence) |
| `servo_deg[3]` | repeat index (0..repeats-1) |
| `servo_deg[4]` | repeats total |

ラベル文字列はフレーム外、直前の `INFO label=...` ASCII 行で送る（受信側 PC で対応付け）。

## 既知の制約 / TODO

- 励起信号 → 録音は **直列**（speaker_blocking → mic_blocking）。本来は EDMA で同時実行。
- ASCII の `INFO label=...` 行とフレームの対応付けが PC 側で **シーケンシャル前提**（START の直後に流れる前提）。複数 START を並走させたい場合はラベル ID を frame 内に埋めるべき。
- LPUART RX FIFO が浅いので、長文コマンド（label に空白を含む 30 字超）は流す前に MCU 側ループを通すマージンを取ること。

## 配線

[08_mic_speaker_test](../08_mic_speaker_test/README.md) と完全に同じ（**SAI1 全二重**, J1 ヘッダ）。**J1.1 BCLK / J1.11 LRC / J1.5 DIN / J1.15 SD**、すべて FRDM-MCXN947 出荷時のジャンパ位置 (SJ11=1-2, SJ10=1-2) のまま動く。
