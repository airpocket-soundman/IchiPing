# 11_smart_window — 本実装ファーム (雨センサ連動 + ESP32 UART)

[10_inference](../10_inference/README.md) を**運用想定の本実装**に拡張したファーム。10 をそのまま流用するのではなく**新規プロジェクト**として分離し、10 は技術検証 (NPU 推論 + baseline 戦略の評価) 用にそのまま保存する位置づけ。

10 からの追加は **2 つだけ**で、それ以外 (推論パイプライン、`ichp_cmd` verb 一式、サーボ、TFT 上半分、baseline 戦略) は 10 と完全同一。

| 追加要素 | 内容 |
|---|---|
| **ESP32 (M5Stamp Pico) 用 LPUART5** | OpenSDA debug UART (LPUART4 / 921600) と並行に LPUART5 (115200) でも `ichp_cmd` を受け付ける双方向チャネル。応答は受信元 UART に返し、自発 event は両方に broadcast |
| **YL-83 雨センサ (P3_4) ポーリング** | 乾→湿エッジが debounce 通過したら自動で `INFER` を 1 回回し、結果を両 UART に broadcast + TFT 表示 |

通知ロジック・自動 OPEN/CLOSE・ホームオートメーション制御はこのファームでは**やらない**。雨検知時の挙動は「INFER して結果を吐く」までで、判断と通知は ESP 側で組む想定。

## ESP32 UART (LPUART5)

| 項目 | 値 |
|---|---|
| FlexComm | FC5 (`kFRO12M_to_FLEXCOMM5`) |
| ピン | P1_16 (TXD → ESP RX) / P1_17 (RXD ← ESP TX) — **要 datasheet 確認**、配線見直し時は [`pin_mux.c`](frdmmcxn947_cm33_core0/pins/pin_mux.c) と一緒に更新 |
| ボーレート | 115200 (M5Stamp Pico の REPL/IDE デフォルト) |

**なぜ FC5 で LPUART2 ではないか**: 配線 spec の LPUART2 は LPI2C2 と FlexComm 2 を共有する。サーボ I²C (PCA9685) が FC2 を既に使っているため、ESP UART はぶつからない FC5 に移した。

### 応答ルール

- コマンド受信した UART にだけ応答する (debug 経由なら debug に、ESP 経由なら ESP に)
- 自発 event (`EVENT RAIN_DETECTED`, それに続く `RESULT ...`) は両 UART に broadcast
- 起動完了時に ESP 側にも `INFO IchiPing 11_smart_window ready (esp uart)` を投げて疎通の最初の手がかりを残す
- `STOP` は両 UART から受け付け、`INFER STREAM` / `BL CALIBRATE` の途中で中断できる

ESP 側 PoC コードは未実装。当面は USB-TTL アダプタを P1_16/17 に直結して `screen /dev/ttyUSB0 115200` のような単純な端末から `PING\r\n` `INFER\r\n` などを叩いて疎通を見るところから始める。

## 雨センサ (YL-83)

| 項目 | 値 |
|---|---|
| ピン | Arduino D9 = P3_4 |
| プルアップ | 内蔵 (pin_mux.c) |
| 信号レベル | 乾燥 = HIGH (1) / 湿潤 = LOW (0) |
| poll 周期 | 100 ms |
| debounce | 3 連続一致 (≒ 300 ms) |
| クールダウン | 5 秒 (連続トリガ抑制) |

### 動作

1. 100 ms 周期で `GPIO_PinRead(P3_4)` をサンプル
2. 同じ値が 3 連続したらその state を確定
3. **乾→湿エッジ**確定 (`wet=false → wet=true`) で:
   - `EVENT RAIN_DETECTED` を両 UART に broadcast
   - `do_infer_once(BCAST)` を回す → `RESULT ...` 行を両 UART に broadcast
   - TFT 下部 status strip を更新
4. 直近 5 秒以内に発火していたらスキップ
5. 湿→乾はトリガにしない (state だけ静かに更新)

初期 state (起動時に既に湿っているケース) は event として扱わない。あくまで「乾燥状態から雨が降り始めた瞬間」だけを能動的に検知する設計。

短絡テスト: P3_4 と GND を抵抗 (10kΩ程度) または直接で短絡すると WET になる。離せば内蔵プルアップで HIGH に戻り、次回の短絡時にまたトリガ。

## TFT 表示レイアウト (240×320 縦)

10 の主表示 (y=0..232) に**下部 status strip (y=232..320)** を追加。

<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 360" width="280" height="360" font-family="sans-serif" font-size="11">
  <rect x="20" y="20" width="240" height="320" fill="#000" stroke="#555"/>
  <rect x="20" y="20" width="240" height="28" fill="#000080"/>
  <text x="26" y="38" fill="#fff">IchiPing smart_win</text>
  <text x="26" y="68"  fill="#888">seq    42</text>
  <text x="26" y="100" fill="#ffa500" font-size="22" font-weight="bold">s10010</text>
  <text x="26" y="130" fill="#888">idx=9/32</text>
  <text x="26" y="155" fill="#0f0">cls14=B2</text>
  <text x="26" y="185" fill="#0ff">factory baseline</text>
  <text x="26" y="210" fill="#888">2100 us</text>
  <line x1="20" y1="252" x2="260" y2="252" stroke="#444"/>
  <text x="26" y="270" fill="#888">RAIN</text>
  <text x="26" y="290" fill="#0ff">WET  3s ago  (#7)</text>
  <text x="26" y="312" fill="#888">ESP UART</text>
  <text x="26" y="332" fill="#fff">rx=1284  tx=4096</text>
</svg>

下部 strip は **1 秒に 1 回** 自動で再描画される (推論中は `do_infer_once` 側で更新するので二重描画にならない)。

| 行 | 内容 |
|---|---|
| `RAIN` ラベル + state | `WET` (cyan) / `DRY` (green) + 直近 event からの経過秒 + event 回数 `#N` |
| `ESP UART` ラベル + counters | `rx=XXXX tx=XXXX` (受信/送信バイト数の累積) |

## コマンド体系

10 と完全同一。`ichp_cmd` で受け付ける verb 一覧は [10_inference README](../10_inference/README.md#コマンド体系) 参照。

`GET CONFIG` の応答に **ESP UART カウンタと雨センサ状態を追加**してある:

```
OK CONFIG rate=16000 window=32000 pattern=... volume=5 baseline=factory
  in_scale_x1e6=185412 in_zp=29 n_invokes=42 last_us=2104
  esp_rx=1284 esp_tx=4096 rain=dry rain_events=7
```

## ビルド + フラッシュ

10 と完全に同じ手順。MCUXpresso for VS Code で:

1. Import project → `firmware/projects/11_smart_window`
2. Build → `Debug/ichiping_11_smart_window.bin`
3. Flash via OpenSDA

依存 (TFLite Micro + Neutron, CMSIS-DSP, prj.conf) は 10 をそのままコピーしてあるので新規セットアップは不要。

## 動作確認シーケンス (推奨)

```bash
# 1. OpenSDA UART (debug) で起動ログを見る
cd pc
uv run python inference_client.py --port COM7

# 2. patterns push + baseline 校正 + 推論一発 (10 と同じ)
:reload
BL CALIBRATE 10
BL LIVE
INFER

# 3. ESP UART 疎通: 別 USB-TTL アダプタを P1_16/17 に挿して
screen /dev/ttyUSB0 115200
> PING
< OK PONG ...
> INFER
< RESULT seq=... cls32_state=s...

# 4. 雨検知トリガ: P3_4 を GND に短絡
# debug + ESP の両方の UART に下記がほぼ同時に出る
EVENT RAIN_DETECTED
RESULT seq=... cls32_state=s... baseline=live ...
```

## 設計判断メモ

- **ESP UART を broadcast 専用にしなかった理由**: ホームオートメーションのコマンド (OPEN/CLOSE/INFER on demand) を ESP 側から自然に投げられるようにしたかった。MCU を完全な daemon にしておけば ESP 側のロジック (notification, scheduling, scene control) を MCU の再フラッシュなしで反復開発できる。
- **雨検知後に自動 CLOSE しない理由**: 「雨 → 窓閉めるべき」判定は人間の意思決定 (在宅か外出か、洗濯物干してたか) を含むので MCU 側ではやらない。MCU は「いま雨が降り始めた + 窓は s10010 (a だけ開いてる)」事実を吐くだけ。閉めるかどうか・誰に通知するかは ESP/クラウド側で決める。
- **クールダウン 5 秒の根拠**: INFER 一回 (capture 2 s + Welch ~50 ms + invoke ~2 ms ≈ 2.1 s) が走り切る時間 + 雨センサのチャタリングが収まる時間。連続発火させても CPU を取り合うだけで価値が無いので抑制。

## 既知の制約 / TODO

- **P1_16/17 の pin alt は要 datasheet 確認**。`kPORT_MuxAlt2` 仮置きで通している。動かなかったら FRDM-MCXN947 datasheet で FC5 の物理ピン Alt を確認 → [`pin_mux.c::LPUART5_InitPins`](frdmmcxn947_cm33_core0/pins/pin_mux.c) と当文書の表を同時更新
- ESP 側ファーム (M5Stamp Pico 上の MicroPython or Arduino スケッチ) は未実装。少なくとも「MCU からの `RESULT` を Slack / IFTTT に転送する」最小スクリプトは別途用意する
- 雨センサのキャリブレーションは GPIO レベル判定のみ (アナログ閾値は使っていない)。誤検知が多ければ YL-83 基板側のポテンショ調整で済むはず
- `s_infer_busy` フラグでガードしているが、INFER 中の rain edge は単に捨てている。本来は queue に入れて INFER 終了後にもう一回回すべき (現状は次回エッジまで待つ)
- 起動時の自動 `CLOSE ALL` (10 から継承) はサーボへの突入電流が大きいので、PCA9685 V+ の電源が貧弱だと boot 時に MCU 側もリセットがかかる場合がある。10 で確認済みだが本ファームでも症状出たら 5V 容量を増やす方向で
