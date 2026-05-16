# 09_collector — PC 制御ラベル付きデータ採取

v0.5 NN 訓練用のデータ採取ステーション。08 の SAI 全二重 + 02 の PCA9685 サーボ + 03 の ILI9341 TFT を統合し、OpenSDA UART 1 本で **PC ↔ MCU 双方向プロトコル**を回す。サーボ座標系・校正運用の正式仕様は [docs/servo_coords.md](../../../docs/servo_coords.md) を参照。

## アーキテクチャ

```
[PC: collector_client.py]                       [MCU: 09_collector]
  PING / GET / SET / SERVO / RUN / STOP   →  ichp_cmd_lbuf_feed
                                                       │
                                                       ▼
  OK ... / ERR ... / INFO ... 1 行         ←  uart_write_line
                                                       │
  ICHP audio frame ×repeats（servo_deg[5] = 実角）←  send_frame
```

ASCII 行と ICHP バイナリは同一 UART に多重化。PC は `ICHP` magic で境界判定。

## コマンド一覧（[shared/include/ichp_cmd.h](../../shared/include/ichp_cmd.h) 参照）

| 種別 | コマンド | 例 | 効果 |
|---|---|---|---|
| 診断 | `PING` | `PING` | `OK PONG <build>` 応答 |
| 取得 | `GET CONFIG` / `GET HOME` / `GET OPEN` / `GET PINS` | `GET HOME` | 現状を OK 行で返す |
| 設定 | `SET VOLUME <0..1>` | `SET VOLUME 0.05` | TX ソフト音量 |
| 設定 | `SET EXCITATION <name>` | `SET EXCITATION multiband` | `chirp` / `multiband` / `silence` |
| 設定 | `SET REPEATS <N>` | `SET REPEATS 30` | RUN 時の試行回数 |
| 設定 | `SET PIN <servo> <deg>` | `SET PIN door_AB 0` | 当該扉/窓を RUN 時に固定 |
| 設定 | `CLEAR PIN <servo>` / `CLEAR PINS` | `CLEAR PIN door_AB` | pin 解除 |
| 校正 | `SET HOME <servo> <deg>` | `SET HOME window_a 12` | home（閉位置, mechanical）を RAM 更新 |
| 校正 | `SET OPEN <servo> <deg>` | `SET OPEN window_a 87` | open（全開位置, mechanical）を RAM 更新 |
| 校正 | `SAVE HOME` | `SAVE HOME` | 永続化（**現状 NOT_IMPL — 後述**） |
| マニュアル | `SERVO <servo> <deg>` | `SERVO window_a 45` | 1 ch を即動かす（RUN 外専用） |
| マニュアル | `SERVO ALL OFF` | `SERVO ALL OFF` | 全 PWM 停止 |
| 実行 | `RUN` | `RUN` | repeats 回データ採取 |
| 中断 | `STOP` | `STOP` | 次フレーム境界で中断 |

servo 名: `window_a` / `window_b` / `window_c` / `door_AB` / `door_BC`（大文字小文字無視）。角度引数はすべて **mechanical_deg**（PCA9685 への生 PWM 角）。`SERVO` / `SET HOME` / `SET OPEN` / `SET PIN` 全部 mechanical 系。表示用の logical 系 (閉=0, 開=+, max 75/90) は [docs/servo_coords.md](../../../docs/servo_coords.md) を参照。

## 動作シーケンス

```
boot
 └ servo_config_init → servos to home_deg → READY
loop:
 └ poll UART RX
    ├ line complete → parse → dispatch → respond
    │   └ on RUN:
    │       for i in 0..repeats-1:
    │         build trial pattern (pinned values + random fill)
    │         drive servos, settle 400 ms
    │         render excitation (cached if unchanged)
    │         play_and_capture (full-duplex SAI1, 2 s window)
    │         send ICHP frame (servo_deg[] = actual angles applied)
    │         poll for STOP between trials
    └ idle → __WFI
```

## PC 側ツールの立ち上げ — [`pc/collector_client.py`](../../../pc/collector_client.py)

### 1. 環境セットアップ（一度だけ）

uv 推奨（pyserial 1 つだけで動く軽量ツールなので extras なし `uv sync` で十分）:

```powershell
# uv 初導入 (1 回だけ)
irm https://astral.sh/uv/install.ps1 | iex

cd pc
uv sync                    # pyserial だけ取得、~3 秒
```

conda 派の場合は `conda env create -f environment.yml` でも OK。詳細は [pc/README.md](../../../pc/README.md) 参照。

### 2. COM ポート確認

Windows: デバイスマネージャ → 「ポート (COM と LPT)」 → FRDM-MCXN947 OpenSDA の COM 番号を控える（例 `COM7`）。複数 USB シリアル機器がある場合は、ボードを抜き差しして消える / 出てくるポートが目印。

### 3. 起動 — REPL モード

```powershell
cd pc
uv run python collector_client.py --port COM7 --out ../captures
```

起動成功時:

```
connected COM7 @ 921600 bps, output -> ../captures
  < INFO IchiPing 09_collector ready
  < INFO build May 16 2026 18:42:11
  < INFO send PING to test, GET CONFIG for state, RUN to collect

Commands forwarded to the MCU (case-insensitive verb):
  PING
  GET CONFIG / GET HOME / GET OPEN / GET PINS
  ...
>
```

`>` プロンプトでコマンドを打つと **そのまま 09_collector ファームに送信**され、MCU からの応答（`OK ...` / `ERR ...` / `INFO ...`）が `  < ...` で表示される。バイナリ ICHP フレームが流れてくれば自動でデマルチプレクスして `captures/<label>/frame_NNNNNN.wav` に保存。

### 4. 基本コマンド例

**疎通確認**:

```
> PING
  < OK PONG May 16 2026 18:42:11
> GET CONFIG
  < OK CONFIG rate=16000 window=32000 excitation=multiband volume=0.050 repeats=30
> GET HOME
  < OK HOME window_a=0.0 window_b=0.0 window_c=0.0 door_AB=0.0 door_BC=0.0
```

**サーボ校正（ホーン取付調整時）**:

```
> SERVO window_a 0           # マニュアル角度指定
  < OK SERVO window_a 0.0
> SERVO window_a 12
  < OK SERVO window_a 12.0    # 「閉」になる角度を目視で探す
> SET HOME window_a 12       # その値を home (閉) として焼く
  < OK HOME window_a 12.0
> SERVO window_a 87          # 「開」になる角度を探す
> SET OPEN window_a 87
  < OK OPEN window_a 87.0
> GET HOME                    # 5 ch 分の home 一覧
  < OK HOME window_a=12.0 window_b=0.0 ...
```

詳細手順は [docs/servo_coords.md §3](../../../docs/servo_coords.md)。

**ラベルを切替えてデータ採取**:

```
> :label door_closed         # PC 側ローカルコマンド (MCU に届かない)
  label set to 'door_closed' (next frames -> <out>/door_closed/)
> SET PIN door_AB 0           # door_AB を「閉」固定
  < OK PIN door_AB 0.0
> SET PIN door_BC 0
  < OK PIN door_BC 0.0
> SET REPEATS 30
  < OK REPEATS 30
> RUN
  < OK RUN started repeats=30 excitation=multiband
  > saved frame_000000.wav (seq=1)
  > saved frame_000001.wav (seq=2)
  ...
  < OK RUN done frames=30
```

30 試行ぶん `captures/door_closed/frame_000000.wav` ... `frame_000029.wav` と `labels.csv` が保存される。

### 5. 起動 — プラン実行モード（推奨, 学習データ採取の本番）

JSON プランを書いて 1 コマンドで複数条件を順次採取:

```json
[
  {"label": "door_closed", "pins": {"door_AB": 0,  "door_BC": 0},  "repeats": 30},
  {"label": "door_half",   "pins": {"door_AB": 45, "door_BC": 45}, "repeats": 30},
  {"label": "door_open",   "pins": {"door_AB": 90, "door_BC": 90}, "repeats": 30},
  {"label": "amb_silence", "pins": {}, "excitation": "silence", "repeats": 10}
]
```

```powershell
uv run python collector_client.py --port COM7 --plan plan.json --out ../captures
```

各 step ごとに `CLEAR PINS` → `SET PIN ...` → `SET REPEATS N` → `RUN` を自動発行。所要時間は約 `repeats × 3 秒 + 設定オーバーヘッド数秒` × step 数。上記 4 step / 計 100 試行で約 6 分。

### 6. ローカルコマンド（`:` で始まる、MCU には届かない）

| コマンド | 効果 |
|---|---|
| `:label <name>` | 以降のフレーム保存先を `captures/<name>/` に切替 |
| `:help` | コマンド一覧表示 |
| `:quit` / `:exit` | 切断して終了 |

### 7. トラブルシュート

| 症状 | 対処 |
|---|---|
| `FAIL opening COM7` | 別ターミナル（TeraTerm 等）が掴んでいる。閉じる |
| `< INFO IchiPing 09_collector ready` が来ない | ファームが起動していない / COM 番号間違い / ボーレート不一致（921600 固定）|
| `OK RUN started` 後にフレームが来ない | I²C 不通でサーボ駆動失敗 → `SERVO window_a 45` 単体で動作確認、デバッグ |
| `! frame seq=N CRC BAD` 頻発 | UART バッファ溢れ。USB ケーブル変更、PC 側 USB ハブ介在を外す |
| サーボが微動するだけ | 外部 5V レール不足 → 1000 µF 電解 + 安定 5V 給電確認 |

## 使い方（コマンドリファレンス）

### インタラクティブ

```powershell
cd pc
python collector_client.py --port COM7 --out ../captures
> PING
OK PONG May 16 2026 18:12:34
> GET HOME
OK HOME window_a=0.0 window_b=0.0 window_c=0.0 door_AB=0.0 door_BC=0.0
> SERVO window_a 45        # マニュアル動作確認（ホーン取付調整用）
> SET HOME window_a 12     # 「閉」位置を 12° に校正
> CLEAR PINS
> SET PIN door_AB 0        # door_AB だけ固定、ほかはランダム
> SET REPEATS 30
> RUN
INFO label=...
[bin frame 1] [bin frame 2] ...
OK RUN done frames=30
```

### スクリプト（条件×繰返しを一発）

```powershell
python collector_client.py --port COM7 --plan plan.json --out ../captures
```

`plan.json` 例（pin 構成で 4 種類の条件を順次採取）:

```json
[
  {"label": "door_closed", "pins": {"door_AB": 0,  "door_BC": 0},  "repeats": 30},
  {"label": "door_half",   "pins": {"door_AB": 45, "door_BC": 45}, "repeats": 30},
  {"label": "door_open",   "pins": {"door_AB": 90, "door_BC": 90}, "repeats": 30},
  {"label": "amb_silence", "pins": {}, "excitation": "silence", "repeats": 10}
]
```

各 step ごとに `CLEAR PINS` → `SET PIN ...` → `SET REPEATS N` → `INFO label=<label>` ASCII 注記 → `RUN`。フレーム受信側で「直前の `label=` 行」を取って `captures/<label>/frame_NNNNNN.wav` ＋ `labels.csv` の 1 行を追加する。

## ICHP フレーム内 `servo_deg[5]` の意味

このプロジェクトでは **当該試行で実際にサーボに送った角度（絶対）**。`home_deg` でも `open_deg` でもなく、PCA9685 に書き込んだ値そのもの。pin 指定があった ch は pin の値、無指定 ch はランダム結果。

ラベル文字列は **フレーム外**、各 RUN の前に `INFO label=...` で送る。PC 側は ASCII 行 / バイナリの逐次到着順を保つことで対応付け。

## サーボ校正（home / open 位置決定）の運用

詳細手順は [docs/servo_coords.md §3 校正手順](../../../docs/servo_coords.md) に集約。要点だけ:

1. `SERVO window_a <deg>` で 1 ch ずつ動かして「閉」位置と「全開」位置を探る
2. `SET HOME window_a 12` / `SET OPEN window_a 87` で焼き付け（窓は `open - home = 75°`、扉は `90°` が目標）
3. 5 ch ぶん繰り返し、`GET HOME` / `GET OPEN` で確認
4. **`SAVE HOME` は現状 NOT_IMPL**。代わりに [`firmware/shared/source/servo_config.c`](../../shared/source/servo_config.c) の `SERVO_CONFIG_DEFAULTS` に値を書き写してリビルド → 焼き直し
5. 以降は boot 時に同じ位置に戻る

将来 MCXN947 IAP を実装したら `SAVE HOME` がフラッシュ書込になる。手順は `servo_config_save_flash()` の TODO 参照。

## ディスプレイ（ILI9341 240×320）

09_collector は TFT を持っていれば自動で 5 サーボのリアルタイム状態パネルを描画する。パネル不在でもファームはヘッドレス動作（display 関数は no-op）。

```
┌──────────────────────────────────────────┐
│ IchiPing collector                       │
├──────────────────────────────────────────┤
│ window_a   +45/+75  [====    ]   MID     │  WINDOW (logical_max=75)
│ window_b    +0/+75  [        ]   CLOSED  │
│ window_c   +75/+75  [========]   OPEN    │
│ door_AB    +90/+90  [========]   OPEN    │  DOOR (logical_max=90)
│ door_BC    +45/+90  [====    ]   MID     │
├──────────────────────────────────────────┤
│ multiband vol 0.05                       │
│ trial   7/30                             │
└──────────────────────────────────────────┘
```

- 表示角度は **logical_deg**（閉 = 0, 開方向 = +）。mechanical_deg からの変換は `servo_config_to_logical()` 経由
- 色: 緑 = CLOSED（logical ≤ 3°）／橙 = OPEN（logical ≥ 95% × max）／黄 = MID
- 各 SERVO / RUN コマンドで即座に再描画

配線は [03_ili9341_test](../03_ili9341_test/README.md) と同一（LPSPI1 + A2/A3/A4/A5 GPIO）。本パネルの詳細仕様は [docs/servo_coords.md §4 ディスプレイ表示](../../../docs/servo_coords.md)。

## 配線

[08_mic_speaker_test](../08_mic_speaker_test/) の和集合 + [02_servo_test](../02_servo_test/) の I²C:

| 信号 | ピン | 備考 |
|---|---|---|
| SAI1 BCLK / FS / TXD / RXD | J1.1 / J1.11 / J1.5 / J1.15 | 08 と同じ（INMP441 + MAX98357A） |
| LPI2C2 SDA / SCL | D18 (P4_0) / D19 (P4_1) | 02 と同じ（PCA9685） |
| OpenSDA UART | LPUART4 | 921600 bps 双方向 |
| サーボ PWM | PCA9685 ch 0..4 | window_a/b/c, door_AB/BC |
| サーボ 5V | 外部 5V レール | MAX98357A と共通、1000 µF 電解必須 |
| ILI9341 TFT | LPSPI1 + A2/A3/A4/A5 GPIO | 03_ili9341_test と同じ。未接続でもファームは動作 |

## ビルド手順（MCUXpresso for VS Code）

このプロジェクトは MCUXpresso Config Tools 生成物（`frdmmcxn947_cm33_core0/`、`CMakeLists.txt`、`prj.conf` 等）を含まない。以下の手順で組み立てる:

1. **08_mic_speaker_test を雛形にコピー**
   ```
   cp -r firmware/projects/08_mic_speaker_test/frdmmcxn947_cm33_core0 firmware/projects/09_collector/
   cp firmware/projects/08_mic_speaker_test/{CMakeLists.txt,CMakePresets.json,Kconfig,example.yml,prj.conf} firmware/projects/09_collector/
   ```
2. **pin_mux に LPI2C2 を追加**: Config Tools で `D18=P4_0 Alt2 (LP_FLEXCOMM2_P0)` / `D19=P4_1 Alt2 (LP_FLEXCOMM2_P1)` を有効化
3. **TFT 用 pin_mux も追加**: 03_ili9341_test の `frdmmcxn947_cm33_core0/pins/pin_mux.c` から LPSPI1 + A2/A3/A4/A5 GPIO 部を移植、`app.h` の `BOARD_ILI_*` マクロを定義
4. **CMakeLists.txt のソース追加**:
   ```cmake
   ../../shared/source/ichiping_frame.c
   ../../shared/source/sai_mic.c
   ../../shared/source/sai_speaker.c
   ../../shared/source/pca9685.c
   ../../shared/source/ichp_cmd.c
   ../../shared/source/servo_config.c
   ../../shared/source/ili9341.c
   ../../shared/source/collector_display.c
   ```
5. **VS Code → MCUXpresso → Import Project From Folder** → `firmware/projects/09_collector/`
6. ビルド → OpenSDA で書込
7. シリアル端末で `PING` 送信 → `OK PONG ...` 確認 → `pc/collector_client.py` へ

## 既知の制約 / TODO

- **フラッシュ永続化未実装** — `SAVE HOME` は NOT_IMPL。MCXN947 IAP（`fsl_iap.h` または `fsl_flash.h`）で 1 sector を予約する実装が必要。回避策はリビルド運用（上記）
- **サーボ移動とキャプチャは直列**。1 試行 = 0.4 s 待ち + 2 s キャプチャ + 0.56 s UART 送信 = **3 秒/フレーム**。30 フレーム = 1.5 分。USB CDC（05 ベース）に乗り換えれば UART 送信 0.1 s に短縮可
- **ラベル対応付けがシーケンシャル前提**。並列 RUN 不可（並列にする場合はラベル ID をフレーム内に埋める拡張が必要）
- **ランダム化は二値選択（home / open のいずれか）**。連続角度ランダム化が必要なら `build_trial_pattern` を改修
- **STOP は試行間境界でのみ反映**。1 試行 3 秒のラグを許容する設計
