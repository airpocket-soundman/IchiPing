<!--
ProtoPedia 投稿用原稿。

ProtoPedia の作品登録フォームは下記の入力欄から構成される（公式ヘルプ
https://protopedia.gitbook.io/helpcenter/registration / .../markdown）。

  必須:
    - 作品ステータス  (アイデア / 開発中 / 完成 / 供養作品)
    - 作品タイトル    (テキスト 1 行)
    - 概要           (Markdown 可、短文)
  任意:
    - 作品 URL
    - ライセンス
    - 画像（最大 5 枚、jpg/png）
    - 動画（YouTube URL を 1 本）
    - システム構成（画像 1 枚 + 説明文、Markdown 可）
    - 開発素材（API / SDK / デバイスを選択）
    - タグ（複数）
    - ストーリー   (Markdown 可、長文。記事の本体)
    - メンバー登録
    - 関連リンク

Markdown は見出し ## ～ ##### が使える（# は不使用が慣例）。表 (| --- |)、
リスト、リンク、画像、コードブロック (4 スペース or タブ)、引用、強調が
そのまま使える。HTML 併用可で、画像サイズ指定だけは <img height=...> を使う。

埋め込みは URL を 1 行で書くと自動展開:
  YouTube / Twitter / Flickr / SpeakerDeck / MakeCode / CARTO

以下、フォームの入力欄ごとに ===== で区切って原稿を並べた。
そのまま該当欄にコピペすれば投稿できる。
-->


===== 作品ステータス =====

開発中


===== 作品タイトル =====

IchiPing — 1 個のセンサで家中の窓と扉を「聴く」エッジ AI


===== 概要（Markdown 可・短文） =====

**「雨降ってきた、窓大丈夫？」** 外出先での不安を解消するエッジ AI デモ。スピーカから **1 発の物理 Ping**（能動掃引音）を撃ち、たった <span style="font-size:1.8em;font-weight:900;vertical-align:-0.05em;">1</span> 個のマイクで返ってくる室内インパルス応答を NPU 上の CNN で解析、**家中の窓・扉 32 通りの組合せ状態を同時推定**します。降雨センサ + M5Stamp Pico (ESP32) を追加して、雨検出 → 推論 → スマホ通知まで完結。さらに **サーボで自動的に窓を閉める** ところまでデモします。NXP **FRDM-MCXN947**（Neutron NPU + PowerQuad DSP 内蔵）上で 1.89 ms 推論、32 状態とも MCU 実機で 100% 正解を達成しました。


===== 作品 URL =====

https://github.com/airpocket-soundman/IchiPing


===== タグ =====

エッジAI, 音響AI, 1D-CNN, MEMSマイク, アクティブセンシング, インパルス応答, MCXN947, FRDM, NXP, INMP441, MAX98357A, PCA9685, SG90, ILI9341, LVGL, PyTorch, ONNX, eIQ, DigiKey, ROHM, ヒーローズリーグ


===== 開発素材（選択肢から該当を選ぶ。以下は手動で書く場合の参考） =====

- ハードウェア
  - NXP FRDM-MCXN947（Cortex-M33 + NPU + PowerQuad）
  - InvenSense INMP441（I²S MEMS マイク, 24-bit）
  - Maxim MAX98357A（I²S Class-D アンプ 3.2 W）
  - NXP PCA9685（I²C 16ch PWM ドライバ）
  - Tower Pro SG90 ×5（マイクロサーボ）
  - ILITEK ILI9341 2.4" TFT 240×320（SPI, RGB565）
  - パネルマウント トグルスイッチ ×5（窓 a/b/c + 扉 AB/BC の真値入力）
  - タクトスイッチ ×1（EXEC ボタン）
- ソフトウェア / SDK
  - MCUXpresso SDK 2.x
  - NXP eIQ Toolkit（ONNX → MCXN947 NPU デプロイ）
  - LVGL 9（TFT GUI）
  - PyTorch（学習）
  - Python（収集・検証クライアント、`pc/collector_client.py`）


===== システム構成（画像 1 枚 + Markdown 説明） =====

![システム構成図](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/system_overview.svg)

機材は **コントローラ筐体**（MCU / 表示 / トグル / アンプ / サーボドライバ / Wi-Fi モジュール）と **House 模型**（マイク / スピーカ / サーボ ×5 / 降雨センサ）の 2 箱に分かれ、ケーブルで結ぶ構成。模型側のトグルスイッチ ×5 は窓・扉の真値ラベルとして学習データに付与される。クラウドとスマホは筐体外の外部システム。

**筐体ごとの中身**

| 場所 | 入っているもの |
|---|---|
| コントローラ筐体 | FRDM-MCXN947 / ILI9341 TFT / トグルスイッチ ×5 + EXEC ボタン / MAX98357A アンプ / PCA9685 サーボドライバ / **M5Stamp Pico (ESP32)** |
| House 模型 | INMP441 マイク / 8 Ω スピーカ / SG90 サーボ ×5（窓 a/b/c + 扉 AB/BC） / **降雨センサ（屋外設置）** |
| 外部システム | スマートホームクラウド (Home Assistant 等の MQTT broker) / ユーザのスマホ |

**筐体間ケーブル**

- I²S mic 3 線（BCLK / WS / SD）+ 電源: コントローラ → 模型内 INMP441
- スピーカ 2 線: コントローラ内 MAX98357A → 模型内 スピーカ
- PWM ×5: コントローラ内 PCA9685 → 模型内 SG90 ×5
- **GPIO 1 線: コントローラ内 MCU → 模型内 降雨センサ（雨検出デジタル入力）**

**コントローラ筐体内の局所配線**

- **UART (LPUART): MCU ↔ M5Stamp Pico**（推論結果送信 + 「閉めて」コマンド受信）

**筐体外 (Wi-Fi)**

- **M5Stamp Pico ↔ スマートホームクラウド ↔ スマホ** (MQTT / HTTP)

**信号の流れ（v1 構成）**

| 段 | 担当 | 内容 |
|---|---|---|
| ① 励振 | MCU → MAX98357A → スピーカ | 200 Hz – 6 kHz の ESS（指数掃引）または可変周波数 chirp を I²S DAC から放射 |
| ② 観測 | INMP441（I²S MEMS マイク, 24-bit） | 同期して 16 kHz で取り込み、SAI1 DMA で MCU に転送 |
| ③ 整合 | PowerQuad FFT + 整合フィルタ | 1024 bin の log-magnitude スペクトルに圧縮 |
| ④ 推論 | MCXN947 内蔵 NPU で 1D CNN | 窓 a/b/c + 扉 AB/BC の開閉 5 ビット（真状態 32 通り / 実効区別 14 通り、後述）を同時出力 |
| ⑤ 表示 | ILI9341 TFT（LVGL）| 推定結果をフロアプランに重ねて可視化 |
| ⑥ 収集 | PC（`pc/collector_client.py`）| OpenSDA UART 921600 bps で WAV + ラベル CSV を吸い上げ、PyTorch で学習 → ONNX → eIQ Toolkit で MCU へ |

**3 部屋アクリル模型（Phase 4 デモ）**

3 部屋を模した 30 cm スケールのアクリル筐体に、PCA9685 経由で SG90 ×5 が窓と扉を物理的に開閉する。模型側のトグルスイッチ ×5 を「真値」として PC に送り、教師ありデータを半自動で量産できる仕組み。

**主要部品（v1 BOM、約 $254）**

| 役割 | 部品 | 配置 |
|---|---|---|
| MCU | NXP **FRDM-MCXN947** | コントローラ筐体 |
| マイク | InvenSense **INMP441** I²S MEMS | House 模型 |
| アンプ | **MAX98357A** I²S Class-D | コントローラ筐体 |
| サーボ駆動 | **PCA9685** + SG90 ×5 (オチ 2「窓自動閉」担当) | PCA9685 = コントローラ / SG90 = 模型 |
| 表示 | **ILI9341** 2.4" TFT 240×320（RGB565, LVGL） | コントローラ筐体 |
| 操作入力 | パネルトグル ×5（窓 a/b/c + 扉 AB/BC 真値） + EXEC タクトスイッチ ×1 | コントローラ筐体 |
| **Wi-Fi モジュール** | **M5Stamp Pico**（ESP32-PICO-D4） | **コントローラ筐体内に統合**、MCU と UART (LPUART) で接続 |
| **降雨センサ** | YL-83 等の安価モジュール | **House 模型に統合（屋外設置）**、MCU の GPIO に直接入力 |
| スマートホーム連携 | クラウド (Home Assistant 等の MQTT broker) | 筐体外 (Wi-Fi 経由) |
| PC 連携 | OpenSDA UART 921600 bps（学習データ収集用）／ USB CDC | コントローラ筐体 |

M5Stamp Pico (ESP32) は親指サイズの Wi-Fi モジュールでコントローラ筐体内に収まり、降雨センサは安価な抵抗式モジュールで House 模型の屋外面に貼り付けるだけ。**デモ装置一式でクラウド連携まで完結**します。


===== ストーリー（Markdown 可・長文。記事の本体） =====

## シーン — 「雨降ってきた、窓大丈夫？」

外出中に空が暗くなり、雨がポツポツと降り出した。スマホを取り出して — **「あれ、窓閉めてきたっけ…？」**

家まで戻る時間も余裕もない。誰もが一度は経験する、地味だけど落ち着かない不安です。

![デモシナリオ全体図](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/demo_use_case.svg)

## IchiPing の解決 — 1 マイク 1 Ping で 32 状態を当てる

IchiPing は、**「<span style="font-size:1.8em;font-weight:900;">1</span> 個のマイクと <span style="font-size:1.8em;font-weight:900;">1</span> 発の Ping だけで、家中の窓と扉の開閉 32 通りを当てる」** ことを実現したエッジ AI デバイスです。これに **降雨センサ + M5Stamp Pico (ESP32)** をデモ用周辺機器として追加することで、次のフローが成立します:

1. 屋外の降雨センサが雨を検出 (GPIO 入力)
2. M5Stamp Pico → UART で IchiPing コントローラに推論トリガを送る
3. IchiPing が 1 Ping → MCU 上の Neutron NPU で **1.89 ms 推論** → 窓・扉 32 状態のうちどれかを特定
4. M5Stamp Pico が Wi-Fi 経由でスマートホームクラウドに結果を送信
5. ユーザのスマホに通知「窓 a が開いてます！」

## 2 段オチ

**オチ 1** — でも、通知が来ても外出中だと…

- 「閉まってる」なら → 安心 ✓
- 「開いてる」なら → 知らされても何もできない 😞

通知だけのソリューションには「情報不足」とは別の **「情報があっても手が届かない悲しさ」** がある。

**オチ 2** — **でも安心してください。** このデモ装置は窓を **サーボで自動開閉できます**。

スマホから「閉めて」をタップ → クラウド → M5Stamp Pico → UART → IchiPing → PCA9685 → SG90 ×5 → **物理的に窓を閉める ✓**

「聴く（推論）→ 通知 → **閉める（アクション）**」の往復が、1 個のセンサと 1 発の Ping から始まる小さな AI デバイスで全部完結します。

## なぜ「1 マイク 1 Ping」が成立するのか

家中にセンサを散らす方式は配線・電池交換・通信の地獄を抱えるのが常ですが、室内の音響インパルス応答（RIR: Room Impulse Response）は **窓 1 枚が開くだけでも全体のモードと残響が変わる** という性質を持ちます。**部屋を丸ごと共振器とみなして 1 点で全部聴く** ほうが筋がいい — それが IchiPing の出発点です。

## なぜ「アクティブセンシング」なのか

パッシブにマイクで生活音を聞くだけでは、家が静まりかえった時刻に観測できなくなります。IchiPing は **物理 Ping（200 Hz から 6 kHz への能動的指数掃引）を撃ち**、その応答を分析するアクティブ計測スタイルを取ります。

- 部屋の状態 (窓/扉の組合せ 32 通り) が変わると、各モードの周波数とダンピングが変わる
- 扉が開くと隣接室と結合し、単一ピークが対称・反対称ペアに分裂する
- 窓が開くと放射損失で Q が落ち、ピーク幅が広がる

このような物理的に裏付けのある変化を **1D CNN** に学習させ、組合せ状態を一発で当てに行きます。CNN backbone は ~14K パラメータの軽量設計で、MCXN947 内蔵の **NPU + PowerQuad DSP** で INT8 推論まで完結します。

## 当初の理論的限界 → 実測で覆された

設計開始時、私たちは「窓 3 + 扉 2 = 5 ビット → 真状態 32 通りだが、扉が閉まると向こう側は遮断され **14 等価クラスが情報量上限** になる」と予想していました。物理的に妥当に見えるこの予想は、しかし v12345 検証で **経験的に否定されました**。

実扉は完全遮音ではなく **-20〜-30 dB 減衰程度**であり、同じ等価クラスに見える状態にもサブ状態を区別できるシグナルが残っていたのです（[nn_methods_compare §1](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/nn_methods_compare.html)）。

**v12345 検証 (8 モデル × 32 状態 × MCU 実機 sweep) の結果**:

| モデル | 32 cls (5-bit 状態) | 14 cls (等価クラス) | 校正 |
|---|---|---|---|
| v12345_BLJIT_live | **100%** | **100%** | LIVE 25 秒 |
| v12345_BLJIT_factory | **88%** | **100%** | なし (即時起動) |
| v12345_50f_live_noiselow | **100%** | **100%** | LIVE (騒音下) |

**32 真状態すべてが MCU 実機で識別可能**。詳細: [v12345 検証レポート](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/v12345_report.html)。

決め手は **Baseline jittering augmentation** — 各録音を 5 種類の baseline で diff して 5 サンプル分に増殖させる学習手法で、これにより「baseline 環境に依存しないラベル決定境界」を獲得しました。

## <span style="font-size:1.8em;font-weight:900;">1</span> 個のセンサに賭ける根拠

採用したのは **InvenSense INMP441**（I²S, 24-bit）です。

- I²S 出力なので 24 bit のダイナミックレンジをそのまま MCU へ持ち込める
- アナログ MEMS に比べて DC オフセット問題がなく、chirp/RIR 用途では特に扱いやすい
- MCXN947 の **SAI（I²S）コントローラ多系統** とそのまま噛み合う

出力側は **MAX98357A**（Class-D 3.2 W）で、デモ用には 8 Ω 0.25 W の 45 mm フルレンジを 3 dB ゲイン固定で駆動。chirp は MCU 内で生成して I²S DAC ストリームに流し込みます（`firmware/projects/07_speaker_test`, `08_mic_speaker_test`, `09_collector` が該当）。

## ハードウェア構成

主要部品の選定根拠は GitHub の [hardware/bom.html](https://github.com/airpocket-soundman/IchiPing/blob/main/hardware/bom.html) と [docs/spec.html](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/spec.html) §6 にまとまっています。

- **MCU: NXP FRDM-MCXN947** — Cortex-M33 + 専用 NPU + PowerQuad DSP。$49 でこのスペックは破格
- **TFT: ILI9341** — 240×320 RGB565。LVGL でフロアプラン UI を描画
- **サーボ駆動: PCA9685** — I²C 0x40。デモ模型の窓 a/b/c + 扉 AB/BC を物理的に開閉
- **データ経路: OpenSDA UART 921600 bps**（v0.1）→ USB CDC（v0.3〜）

## ソフトウェア構成

リポジトリは **MCU 側 C ファーム** と **PC 側 Python クライアント・学習パイプライン** の二段構成です。

- `firmware/projects/01_dummy_emitter` 〜 `10_inference` — ブリングアップを段階分割した 10 プロジェクト群（ダミーフレーム送出 → サーボ → TFT → マイク → スピーカ → 同期計測 → 推論）
- `firmware/shared/` — フレーム形式、PCA9685/LU9685 ドライバ、SAI mic/speaker、励振パターンライブラリ、表示パネル
- `pc/collector_client.py` — シリアル/TCP/file 入力から WAV + CSV ラベルを保存
- `pc/inference_client.py` — 推論結果をリアルタイム可視化
- `pc/patterns.yaml` + `pc/patterns.py` — YAML 駆動の励振パターンライブラリ
- `pc/training/` — 1D CNN マルチタスク（~14K params）の学習 → ONNX エクスポート → eIQ Toolkit で MCU へ

## 励振パターンを物理ベースで設計する

ただ広帯域 chirp を撃つだけでなく、**3 部屋模型の共鳴周波数を物理的に予測 (f₁₀₀ ≈ 572 Hz) し、判別性の高い帯域に集中投下する** 走査音設計も検討中です。これにより学習サンプル数の削減と推論の interpretable 化を狙います。詳細は [docs/probe_sound.html](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/probe_sound.html) にまとめています。

## ロードマップ

- [x] **v0.1〜v0.4** シリアル疎通 → I²S 放射/受信 → サーボ制御 → 3 部屋模型自動データ収集（達成済）
- [x] **v0.5〜v0.7** 14cls/32cls 両 head Neutron 互換モデル → INT8 量子化 → Neutron 変換 (NPU 比率 7/7 = 100%, 108 KB, 1.89 ms)（達成済）
- [x] **v1.0** MCU 実機検証 v12345 sweep (8 モデル × 32 状態、**32cls / 14cls とも 100% 達成**)（達成済）
- [ ] **v1.5** TFT (ILI9341) でフロアプラン表示 + EXEC ボタンによる手動デモモード
- [ ] **デモ拡張** 降雨センサ + M5Stamp Pico を接続して雨検出 → スマホ通知 → サーボ自動閉まで完結
- [ ] **v2.0** ROHM **ML63Q2557 + Solist-AI** への移植（ROHM EDGE HACK 2026 提出版）

## 応募先

- **DigiKey M1 デザインコンテスト**
- **ROHM EDGE HACK 2026**（v2.0 で Solist-AI 移植版を提出予定）

## プロジェクト名の由来

「**イチ**個のマイクで、**Ping**！と当てる」「<span style="font-size:1.8em;font-weight:900;">1</span> 発の **Ping** で家中を聴く」というコンセプトを縮めて **IchiPing**。当初は気圧センサで攻める案（DoorBaro / WindowGuard）でしたが、音響アクティブ計測のほうが筋が良いと判断してピボットしました。

## リポジトリ

ソース・ドキュメント・配線図・部品表すべて公開しています。

https://github.com/airpocket-soundman/IchiPing


===== メンバー登録 =====

(チーム名／メンバーは投稿者で記入)


===== 関連リンク =====

- GitHub リポジトリ: https://github.com/airpocket-soundman/IchiPing
- 母プロジェクト（アイデアカタログ・正本仕様）: https://github.com/airpocket-soundman/digikey_project
- C4 仕様書（正本）: https://github.com/airpocket-soundman/digikey_project/blob/main/details/C4-DoorBaro.html
- 走査音考察: https://github.com/airpocket-soundman/IchiPing/blob/main/docs/probe_sound.html
- NN 考察: https://github.com/airpocket-soundman/IchiPing/blob/main/docs/nn_review.html
- BOM（部品表）: https://github.com/airpocket-soundman/IchiPing/blob/main/hardware/bom.html


===== 画像（最大 5 枚、フォームでアップロード） =====

推奨アップロード順:
  1. ヒーロー画像: 実機写真 or レンダリング
  2. システム構成図: docs/img/excitation_pipeline.svg を PNG 書き出し
  3. 3 部屋模型: docs/img/collector_display_panel.svg or 模型写真
  4. ファーム動作スクリーンショット: TFT に推定結果が出ている図
  5. 配線図: hardware/wiring.svg を PNG 書き出し


===== 動画（YouTube URL を 1 本、URL を直書きすると自動埋込） =====

(撮影後に貼る。Phase 4 模型での自動データ収集デモが映え案件)


===== ライセンス =====

未定（コンテスト応募後に MIT 想定）


<!--
投稿時のチェックリスト:
  [ ] 概要を 200 字以内に削っているか
  [ ] 画像 5 枚はアップロード済か
  [ ] システム構成画像は最初の 1 枚で目を引く図にしたか
  [ ] 動画 URL は YouTube 単独 URL を 1 行で書いたか（埋込発動条件）
  [ ] タグは 10〜20 個に絞ったか（多すぎると埋もれる）
  [ ] GitHub URL は main ブランチ直リンクか（worktree や private ではないか）
  [ ] 応募イベントの「タグ」をフォームのタグ欄に追加したか
       例: #ヒーローズリーグ2026 / #DigiKeyM1 / #ROHM_EDGE_HACK_2026
-->
