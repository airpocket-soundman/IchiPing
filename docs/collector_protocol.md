# Collector 通信仕様 (09_collector ↔ PC)

09_collector ファームと PC ツール間の通信を **OpenSDA UART 単線**で行う際の正式仕様。
ASCII コマンド/応答と ICHP バイナリフレームを同じワイヤに多重化する。

C 側の正本: [firmware/shared/include/ichp_cmd.h](../firmware/shared/include/ichp_cmd.h) と
[firmware/shared/include/ichiping_frame.h](../firmware/shared/include/ichiping_frame.h)。
本ドキュメントは PC 実装者向けの参照書で、変更があれば C ヘッダを真として更新する。

---

## 1. 物理層

| 項目 | 値 |
|---|---|
| インターフェース | OpenSDA UART (LPUART4) |
| ボーレート | 921 600 bps |
| データビット | 8N1 |
| フロー制御 | なし |
| エンコーディング | バイナリ / ASCII 多重 |

PC 側は `pyserial`、ボード側は `LPUART_WriteBlocking` / 割込 RX 駆動。

---

## 2. フレーミング規約

UART には 2 種類のメッセージが混在する:

| 種別 | 形式 | 検出方法 |
|---|---|---|
| ASCII コマンド/応答 | 1 行 (CR-LF または LF 終端) | バイト走査で `\n` 検出 |
| ICHP バイナリフレーム | 36 B ヘッダ + N×2 B PCM + 2 B CRC | バイト走査で `ICHP` magic 検出 |

ASCII 行内に文字列 `ICHP` が出現しないよう、ファーム側は応答メッセージで該当 4 バイト連続を避ける（コマンド名・パラメータ識別子の選定で確認済）。

```
PC 受信側の擬似コード:
  loop:
    bytes = serial.read(N)
    for b in bytes:
      if scanning_ascii and b == '\n':
        process_line(buffer); buffer.clear()
      else if magic_match(b):
        switch to binary mode, read 36 + N×2 + 2 bytes
      else:
        buffer.append(b)
```

---

## 3. ASCII コマンド

verb は **大文字小文字無視**（パーサが内部で大文字化）、空白/タブ区切り、CR-LF または LF で終端。
最大行長 **128 バイト**（超過は `ERR LINE_TOO_LONG`）。

### 3.1 全 verb 一覧

完全な動的仕様は [`ichp_cmd.h`](../firmware/shared/include/ichp_cmd.h) のヘッダコメント参照。
PC 実装者がよく使う verb の要約:

| 区分 | verb | 引数 | 応答例 |
|---|---|---|---|
| 診断 | `PING` | なし | `OK PONG <build_time>` |
| 設定取得 | `GET CONFIG` / `GET HOME` / `GET OPEN` / `GET PINS` | なし | `OK CONFIG ...` |
| 設定 | `SET VOLUME <pct>` | 0..100 | `OK VOLUME <pct>` |
| 設定 | `SET REPEATS <n>` | 整数 | `OK REPEATS <n>` |
| 設定 | `SET PIN <servo> <deg>` | servo=a/b/c/AB/BC、deg=0..180 | `OK PIN <servo> <deg>` |
| サーボ | `SERVO <servo> <deg>` | 単発移動 | `OK SERVO <servo> deg=<n>` |
| サーボ | `OPEN <servo>` / `CLOSE <servo>` | name で開閉 | `OK OPEN <servo> deg=<n>` |
| パターン管理 | `PAT CLEAR` / `PAT INFO` / `PAT SELECT <idx>` | — | `OK PAT ...` |
| パターン登録 | `PAT PULSE BEGIN <name>` ... `PAT TONE <hz> <on_ms> <off_ms>` ... `PAT PULSE END [repeat]` | 多段 | 各行 `OK PAT ...` |
| パターン登録 | `PAT SWEEP <name> <start_hz> <end_hz> <sweep_ms> <silence_ms>` | atomic | `OK PAT sweep ...` |
| パターン登録 | `PAT NOISE <name> <dur_ms> [vol_pct] [shape]` | atomic、shape=0(PRBS, default) / 1(uniform) | `OK PAT noise ...` |
| 試聴 | `EMIT <idx>` | 録音なし放射 | `OK EMIT idx=<n> name=<name> samples=<n>` |
| 採取 | `RUN` | 現 select の `repeats` 回採取 | `OK RUN started ...` ＋ N ×ICHP フレーム ＋ `OK RUN done frames=<n>` |
| 中断 | `STOP` | 採取中のみ有効 | `OK STOP requested` ＋ `OK RUN aborted frames=<n>` |
| EQ | `EQ ENABLE` / `EQ DISABLE` / `EQ RESET` / `EQ GET` / `EQ STATE` | — | 各種 `OK EQ ...` |
| EQ 係数 | `EQ SET <stage> <b0> <b1> <b2> <a1> <a2>` | stage=0..7、係数 float | `OK EQ set stage=<n>` |

### 3.2 応答メッセージ規約

すべての応答は 1 行で、先頭トークンが `OK` / `ERR` / `INFO` のいずれか。

| 先頭 | 意味 |
|---|---|
| `OK ...` | コマンド成功。後続トークンは verb 別 |
| `ERR <code> [detail]` | 失敗。code 例: `BAD_VERB` / `BAD_ARGS` / `BAD_SERVO` / `OUT_OF_RANGE` / `BUSY` / `NOT_IMPL` / `LINE_TOO_LONG` |
| `INFO ...` | 非同期通知（ブート banner、I²C スキャン結果、警告等）。コマンドへの応答ではない |

PC 側のパーサは:
- コマンド送出後、次の `OK ...` または `ERR ...` 行を ack として待つ
- `INFO ...` は ack ではないので待ち続ける（タイムアウト境界に注意）
- `OK RUN started ...` は ack だが、その後 ICHP フレームが N 個続き、最後に `OK RUN done frames=<n>` が来る

### 3.3 同期規約

- **ack 待ち必須**: 連続コマンド送信は前の `OK`/`ERR` 受信後にする（さもないと LPUART RX FIFO 溢れ）
- 各コマンド ack のタイムアウト目安: 通常 2 秒、`RUN` 系は 5 秒
- ブート完了待ち: PC は `INFO IchiPing 09_collector ready` または `INFO ... ready` を見るまで PAT 送信しない

---

## 4. ICHP バイナリフレーム

完全仕様は [`ichiping_frame.h`](../firmware/shared/include/ichiping_frame.h) 参照。要約:

```
[ 'I' 'C' 'H' 'P' ][32 B 残り header][PCM bytes = n_samples * 2][2 B CRC16-CCITT]
```

| Header field (uint, LE) | Bytes | 意味 |
|---|---|---|
| `magic` | 4 | "ICHP" 固定 |
| `version` | 1 | プロトコル version |
| `flags` | 1 | bit0: stop_requested 等 |
| `frame_idx` | 2 | 試行通し番号 |
| `sample_rate` | 4 | Hz (例 16000) |
| `n_samples` | 4 | PCM サンプル数（int16）|
| `bit_depth` | 1 | 16 固定 |
| `_pad` | 3 | パディング |
| `servo_deg` | 5×4 = 20 | float32 ×5、servo_a..door_BC の実角 |
| CRC | 2 (フレーム末尾) | CCITT-FALSE poly 0x1021, init 0xFFFF, magic→payload 全体 |

PCM は `int16_t` リトルエンディアン、`n_samples` 個。

---

## 5. 起動シーケンス（PC → ボード接続）

```
PC                                     ボード
─────                                   ─────
シリアル open                          (DTR トグルで MCU reset するかも)
                                       ←  INFO BOOT IchiPing 09_collector starting
                                       ←  INFO BOOT build <date> <time>
                                       ←  INFO BOOT pattern_lib ready ...
                                       ←  INFO BOOT spk_eq ready (disabled, identity defaults)
                                       ←  INFO BOOT SAI mic OK rate=16000Hz
                                       ←  INFO BOOT SAI speaker OK rate=16000Hz
                                       ←  INFO IchiPing 09_collector ready
PAT 一括送信開始                       ←  各行 OK PAT ...
PAT SELECT 0                           ←  OK PAT select ...
通常運用へ
```

PC 側は最後の `INFO ... ready` 行を見るまで PAT 送信を待つ。ボード側のコマンド処理ループは
それ以前は走っていないため、早すぎる送信は LPUART RX FIFO に滞留して LF/CR が壊れる。

---

## 6. キャリブレーション運用フロー

スピーカ・マイクのキャリブレーション ([docs/probe_sound.html](probe_sound.html) §3.A) を
本仕様で実行する流れ:

```
1. EQ DISABLE                          # 必ず生の SPK+mic 応答を測る
2. PAT NOISE cal 3000 30 0             # 3 秒の PRBS ホワイトノイズ登録
3. PAT SELECT <idx>                    # 上で登録した index に切替
4. SET REPEATS 1                       # 1 フレームでよい
5. RUN                                 # 1 個の ICHP フレームを受信
                                       # → PC で WAV 保存
6. PC で WAV 解析 → biquad 係数生成
7. EQ SET 0 <b0> <b1> <b2> <a1> <a2>   # 8 段ぶん繰り返す
   ... EQ SET 7 ...
8. EQ ENABLE
9. EQ GET                              # 確認: 全段の係数が想定通りか
10. RUN                                # 補正後の音を計測
11. PC で raw / corrected を比較
```

---

## 7. PC 側ツール

| ツール | 用途 |
|---|---|
| [pc/collector_client.py](../pc/collector_client.py) | 対話 REPL + plan 実行 + 単発コマンド (`--once`) + スクリプト (`--script`) |
| [pc/calibrator.py](../pc/calibrator.py) | キャリブレーション専用 CLI（record-noise / analyze / design-filter / upload-filter / compare） |
| [pc/patterns.py](../pc/patterns.py), [pc/patterns.yaml](../pc/patterns.yaml) | パターンライブラリ定義（PAT 送信に使う） |
| [pc/ichp_frame.py](../pc/ichp_frame.py) | ICHP フレームの pack/unpack（C ヘッダと同期） |

---

## 8. 仕様変更時の同期義務

[CLAUDE.md](../CLAUDE.md) の `firmware/shared/include/ichiping_frame.h` 同期ルールに従い、
**C ヘッダを変更したら本ドキュメントと PC 側パーサ
（`pc/ichp_frame.py`, `pc/collector_client.py`）を同じ commit で更新する**。

特に:
- `ichp_cmd_kind_t` enum 追加 → PC 側送信ヘルパに対応コマンド追加
- ICHP フレームヘッダの field 変更 → `pc/ichp_frame.py` の `HEADER_FMT` 同時更新
- `PATTERN_KIND_*` 追加 → `pc/patterns.py` の YAML パーサ拡張
