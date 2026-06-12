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

**「雨降ってきた、窓大丈夫？」** 外出先での不安を解消するエッジ AI デモ。スピーカから **1 発の物理 Ping** を撃ち、たった <span style="font-size:1.8em;font-weight:900;vertical-align:-0.05em;">1</span> 個のマイクで三つの部屋の窓と扉の状態を調べます。


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

**システム処理フローチャート**

![信号フロー](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/signal_flow.svg)

雨検出 → 1 Ping 励振 → マイク観測 → 信号処理 → NPU 推論 → ローカル表示 + スマホ通知 → ユーザのアクションでサーボ自動閉まで、runtime の処理経路を 1 枚で示しています。学習データ収集モードでは ③〜④ の間で WAV を PC に送出し、PyTorch 学習 → ONNX → Neutron 変換 → MCU 戻しのサイクルを回します。

**3 House 模型**

3 部屋を模した 30 cm スケールのアクリル筐体に、PCA9685 経由で SG90 ×5 が窓と扉を物理的に開閉する。模型側のトグルスイッチ ×5 を「真値」として PC に送り、教師ありデータを半自動で量産できる仕組み。

**主要部品（v1 BOM、約 $210、DigiKey 価格ベース）**

| 役割 | 部品 | 単価 | Vendor / DigiKey リンク | 配置 |
|---|---|---|---|---|
| MCU | NXP **FRDM-MCXN947** | $49 | [DigiKey: FRDM-MCXN947](https://www.digikey.com/en/products/detail/nxp-usa-inc/FRDM-MCXN947/22036137) | コントローラ筐体 |
| マイク | InvenSense **INMP441** I²S MEMS breakout | $2-5 | 汎用 (AliExpress / Amazon) ／ bare IC は [DigiKey: INMP441ACEZ-R7](https://www.digikey.com/en/products/detail/tdk-invensense/INMP441ACEZ-R7/2606606) | House 模型 |
| アンプ | **MAX98357A** I²S Class-D | $5.95 | [DigiKey: Adafruit 3006](https://www.digikey.com/en/products/detail/adafruit-industries-llc/3006/6058477) | コントローラ筐体 |
| サーボドライバ | **PCA9685** 16ch PWM | $14.95 | [DigiKey: Adafruit 815](https://www.digikey.com/en/products/detail/adafruit-industries-llc/815/4990757) | コントローラ筐体 |
| サーボ | **SG90** 9g micro servo ×5 (オチ「窓自動閉」担当) | $5.95×5 | [DigiKey: Adafruit 169](https://www.digikey.com/en/products/detail/adafruit-industries-llc/169/5154651) | House 模型 |
| 表示 | **ILI9341** 2.4" TFT 240×320（RGB565, LVGL, タッチパネル付き） | $29.95 | [DigiKey: Adafruit 2478](https://www.digikey.com/en/products/detail/adafruit-industries-llc/2478/5761253) | コントローラ筐体 |
| 操作入力 | パネルトグル SPST ×5（窓 a/b/c + 扉 AB/BC 真値） | $3.50×5 | [DigiKey: E-Switch ST161D00](https://www.digikey.com/en/products/detail/e-switch/ST161D00/EG4815-ND/2116294) | コントローラ筐体 |
| 操作入力 | EXEC タクトスイッチ 6×6 mm | $0.20 | [DigiKey: 6mm タクト検索](https://www.digikey.com/en/products/filter/tactile-switches/197) | コントローラ筐体 |
| **Wi-Fi モジュール** | **M5Stamp Pico**（ESP32-PICO-D4） | $6.50 | [DigiKey: M5Stack K051](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/K051/14672117) | **コントローラ筐体内に統合**、MCU と UART (LPUART) で接続 |
| **降雨センサ** | YL-83 抵抗式モジュール | $1.50 | 汎用 (AliExpress / Amazon、DigiKey 取扱なし) | **House 模型に統合（屋外設置）**、MCU の GPIO に直接入力 |
| スマートホーム連携 | クラウド (Home Assistant 等の MQTT broker) | — | (ソフトウェア) | 筐体外 (Wi-Fi 経由) |
| PC 連携 | OpenSDA UART 921600 bps（学習データ収集用）／ USB CDC | — | (オンボード) | コントローラ筐体 |
| 1000 µF 16 V 電解 | サーボ V+ 突入電流吸収 | $0.55 | [DigiKey: Chemi-Con EKYB160ELL102MJ16S](https://www.digikey.com/en/products/detail/chemi-con/EKYB160ELL102MJ16S/4843676) | コントローラ筐体 |
| LED 緑 5mm | PWR 表示 | $0.25 | [DigiKey: Dialight 5500205F](https://www.digikey.com/en/products/detail/dialight/5500205F/350-1598-ND/808996) | コントローラ筐体 |

詳細な配線ピンマップは [hardware/wiring.html](https://github.com/airpocket-soundman/IchiPing/blob/main/hardware/wiring.html)、完全な BOM (抵抗・コンデンサ・ジャンパ線・USB ケーブル等含む) は [hardware/bom.html](https://github.com/airpocket-soundman/IchiPing/blob/main/hardware/bom.html) を参照。

**注**: 旧 BOM の「Adafruit 3421」は INMP441 ではなく SPH0645LM4H（別チップ）です。本ファームウェアは INMP441 を期待するため、Adafruit 3421 では動きません。汎用 INMP441 ブレイクアウト（AliExpress / Amazon）または bare IC + 自作 PCB を推奨。

M5Stamp Pico (ESP32) は親指サイズの Wi-Fi モジュールでコントローラ筐体内に収まり、降雨センサは安価な抵抗式モジュールで House 模型の屋外面に貼り付けるだけ。**デモ装置一式でクラウド連携まで完結**します。


===== ストーリー（Markdown 可・長文。記事の本体） =====

## シーン — 「雨降ってきた、窓大丈夫？」

外出中に空が暗くなり、雨がポツポツと降り出した。スマホを取り出して — **「あれ、窓閉めてきたっけ…？」**

家まで戻る時間も余裕もない。誰もが一度は経験する、地味だけど落ち着かない不安です。

## IchiPing の解決 — 1 マイク 1 Ping で 32 状態を当てる

IchiPing は、**「<span style="font-size:1.8em;font-weight:900;">1</span> 個のマイクと <span style="font-size:1.8em;font-weight:900;">1</span> 発の Ping だけで、家中の窓と扉の開閉 32 通りを当てる」** ことを実現したエッジ AI デバイスです。窓 3 個 + 扉 2 個 = 5 bit → **32 通りの組合せ状態**を 1 ショット推論で特定します。

これに **降雨センサ + M5Stamp Pico (ESP32)** をデモ用周辺機器として追加することで、次のフローが成立します:

1. 屋外の降雨センサが雨を検出 (GPIO 入力)
2. M5Stamp Pico → UART で IchiPing コントローラに推論トリガを送る
3. IchiPing が 1 Ping → MCU 上の Neutron NPU で **1.89 ms 推論** → 窓・扉 32 状態のうちどれかを特定
4. M5Stamp Pico が Wi-Fi 経由でスマートホームクラウドに結果を送信
5. ユーザのスマホに通知「窓 a が開いてます！」

> **今回は PoC ですが、外部ネットワークへの接続用に ESP32 (M5Stamp Pico) を搭載しており、スマートホームシステムに統合する基本機能を実装しています。**

### 理論的観測限界 — 14 クラスのはずだった

設計開始時には **「32 状態のうち 14 状態しか区別できないはず」** と予想していました。マイクと SPK は Room A の中央にあり、扉 AB が閉まれば Room B / C は音響的に遮断され、向こう側の窓状態は観測不能になる — これが「観測等価性」の物理的予言です。

![観測可能性: 扉開閉によるフロアプラン上の有効エリア](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/observability.svg)

3 場面で観測可能な変数 (窓) と区別できる状態数:

| 扉条件 | 真状態数 | 観測可能なクラス |
|---|---|---|
| 扉 AB 閉 (BC は問わず) | 16 配置 | 2 クラス (窓 a の開閉のみ) |
| 扉 AB 開, BC 閉 | 8 配置 | 4 クラス (窓 a, b の組合せ) |
| 扉 AB 開, BC 開 | 8 配置 | 8 クラス (窓 a, b, c の組合せ) |
| **合計** | **32 配置** | **2 + 4 + 8 = 14 クラス (理論)** |

### 実測 — 32 クラス全て分類成功

しかし、v12345 検証 (MCU 実機 8 モデル × 32 状態 sweep) で **当初予言は経験的に否定** されました。実機計測の結果、**32 真状態すべてが 100% 識別可能** だったのです。

| モデル | 32 cls 正解率 | 14 cls 正解率 |
|---|---|---|
| **v12345_BLJIT_live** | **32 / 32 = 100%** | 32 / 32 = 100% |
| v12345_BLJIT_factory (校正不要) | 28 / 32 = 88% | 32 / 32 = 100% |
| v12345_50f_live_noiselow (騒音下) | **32 / 32 = 100%** | 32 / 32 = 100% |

理由は **実扉が完全遮音ではなく -20〜-30 dB の漏れがあった** こと。同じ等価クラスに属する状態のサブ状態にも、扉を透過した微弱な音響シグナルが残っていました。これを学習側で拾うために **Baseline Jittering Augmentation** という手法を開発: 各録音を 5 種類の baseline で diff して 5 サンプル分に増殖させ、「baseline 環境に依存しないラベル決定境界」を獲得させたのです。

結果、**閉鎖扉を透過した微弱な信号も NN が捉え、観測等価性の理論限界を突破して 32 クラス全分類に成功** しました。詳細: [v12345 検証レポート](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/v12345_report.html) / [nn_methods_compare §1](https://github.com/airpocket-soundman/IchiPing/blob/main/docs/nn_methods_compare.html)。

## 技術の原理 — 潜水艦ソナー・蝙蝠・鯨類エコーロケーションと同じ仕組み

IchiPing の **「Ping を撃って返ってきた音から環境を推定する」** という基本原理は、まったく新しい発明ではありません。自然界と軍事技術にすでに数億年〜数十年の蓄積があります。

| 主体 | 原理 | 用途 |
|---|---|---|
| 🦇 **蝙蝠** | 20-200 kHz の超音波 chirp を口/鼻から発射 → 反射音を耳で聞き分けて飛翔中の昆虫や障害物の位置・大きさ・動きを把握 | 暗闇飛翔 / 狩り |
| 🐬 **鯨類 (イルカ・ハクジラ類)** | クリック音や FM スイープを噴気孔下のメロン器官から発射 → 下顎の脂肪体で受波して魚群や海底地形を 3D 再構成 | 索餌 / 仲間との通信 |
| 🚢 **潜水艦のアクティブソナー** | 低周波 ping を水中に放射 → 反射エコーから対象艦の位置・距離・速度を計算 | 索敵 / 海図作成 |
| 🔊 **IchiPing** | スピーカから 1 Ping (200-6000 Hz 掃引) → マイクで室内インパルス応答を録音 → NPU が窓・扉の開閉状態を推定 | 家中の窓状態モニタリング |

**共通する 4 ステップ**: ① 既知の探査音を発射 → ② 反射・伝搬の応答を観測 → ③ 環境固有の特徴 (反射時間 / 周波数応答 / モード) を抽出 → ④ 環境状態を推定。

IchiPing はこの **アクティブ音響センシング** の原理を、$50 の MCU + $8 のマイク・スピーカで、家庭環境というスケールに落とし込んだ実装です。**蝙蝠が暗闇の昆虫を「聴いて」捕まえるのと同じことを、開いたままの窓に対してやっている** と言えます。

## 検出の仕組み — FFT diff による特徴抽出

NN に渡す入力は **生の FFT スペクトルではなく、「baseline (h00000 全閉) からの diff」** です。これが SNR を大きく改善し、窓開閉の特徴を顕在化させる鍵になっています。

![FFT diff による特徴抽出 — h00000 (全閉) vs h00001 (窓 a 開)](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/fft_diff_explanation.svg)

上から:
1. **h00000 (全閉) の FFT マグニチュード** — SPK の周波数特性、室内モード、定常雑音、室温・湿度依存などが全部混在
2. **h00001 (窓 a のみ開) の FFT マグニチュード** — 上とほぼ同じに見える。窓 a を開けた音響的変化は数 dB 程度で、目視ではほぼ判別不能
3. **Diff = h00001 − h00000** — 両者の共通成分（雑音床や SPK 特性）が打ち消され、**窓 a を開けたことで起きた変化だけ**が残る。309 Hz で +30 dB のピーク、1212 Hz / 2798 Hz で −15〜−20 dB のディップ、と明瞭な特徴が出る

**SNR 改善は +20〜+30 dB**。NN は「環境全部の絶対値」ではなく「**何が変わったか**」に集中して学習できるため、少サンプルでも高精度を実現できます。

### クラスごとの FFT diff 形状

![FFT diff from baseline h00000 (dB) — sorted by equivalence class](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/fft_diff_heatmap_by_class.png)

32 状態を等価クラス順 (A1 → A2 → B1..B4 → C1..C8) に縦に並べ、横軸を周波数 (50-5000 Hz log) として diff を **色 (赤 = +dB / 青 = -dB)** で示したヒートマップ。クラスごとに独自のパターンが目視で確認できます:

- **A1 (全閉, 一番下のブロック)** はほぼ無色 (±0 dB) — baseline と同じなので diff = 0
- **A2 (窓 a のみ開)** は 200-400 Hz と 600-800 Hz に強い赤帯 — 部屋 A の壁面振動が窓開で軽くなる
- **B 系列 (扉 AB 開, BC 閉)** は中域 400-1000 Hz に独特の赤/青パターン — 部屋 A+B の結合モード
- **C 系列 (両扉開, 上部 8 行)** は全帯域に広がる複雑な diff — 3 部屋カップリングで多数の共鳴ピーク

クラス境界ではっきり色パターンが切り替わるのが見え、これが **14 等価クラスへのマッピングが NN にとって学習可能** な根拠です。さらに同じクラス内 (例: A1 の 16 サンプル) でも微妙な色差があり、これが **32 真状態すべて識別可能** だった理由 (扉漏れによるサブクラス情報の残存)。

NN はこの「**形状の違い**」を Conv1D / Conv2D で学習し、状態を識別します。生 FFT 直接学習では「窓 a 開」と「室温が下がった」を区別できませんが、**diff にすれば環境変化はキャンセル**されて窓状態だけが残る、というのが本手法の本質です。

## MCU で動く CNN モデルの構造

PC で学習させたモデルを **そのまま MCU に持っていくと NPU 比率は 30% 程度** に留まり、CPU フォールバックで推論時間と消費電力が悪化します。これを解決するために、IchiPing の本番モデルは **MCXN947 Neutron NPU が 100% 処理できる構造に op を書き換えた** 専用アーキを採用しました。

![MCU 本番モデル — Neutron 互換 32-class Conv2D](https://raw.githubusercontent.com/airpocket-soundman/IchiPing/main/docs/img/nn_arch_neutron_actual.svg)

**構造の特徴**:

| 層 | op | 出力 shape | params | NPU |
|---|---|---|---|---|
| 入力 | log-mag spectrum | (B, 1, 1024) | — | — |
| Conv2D 32ch | k=(1, 16), s=(1, 4) | (B, 32, 1, 253) | 544 | ✓ |
| Conv2D 64ch | k=(1, 8), s=(1, 4) | (B, 64, 1, 62) | 16,448 | ✓ |
| Conv2D 128ch | k=(1, 4), s=(1, 4) | (B, 128, 1, 15) | 32,896 | ✓ |
| Conv2D 128ch | k=(1, 3), s=(1, 2) | (B, 128, 1, 7) | 49,280 | ✓ |
| AvgPool2D | k=(1, 7) | (B, 128, 1, 1) | 0 | ✓ |
| 1×1 Conv2D | classifier head | (B, 32, 1, 1) | 4,128 | ✓ |
| 出力 | 32 logits → argmax → state | (B, 32) | — | — |

合計 **~104K params / 108 KB INT8 / 7 op すべて NPU 化 = 100% NPU 比率 / 推論時間 1.89 ms**。

### NPU 互換のために変えた点

| 変更前 (素直な実装) | 変更後 (Neutron 互換) | 理由 |
|---|---|---|
| Conv1D | **Conv2D kernel=(1, K)** | Conv1D は TFLite で Reshape を強制 → fusion が崩れる |
| GlobalAveragePool (Mean op) | **AvgPool2D k=(1, 7)** | Mean op は NPU 非対応。stride 4,4,4,2 で空間 1024→7 に揃え kernel ≤ 7 制限に収める |
| Flatten + Linear (FC) | **1×1 Conv2D** | Linear は NPU 上で非効率。1×1 Conv2D で等価実装し NPU に載せる |
| BatchNorm（推論時も残す） | **export 前に Conv に fold** | `fuse_conv_bn_eval` で Conv に吸収し推論グラフから消す |

### 出力 — 32 logits → 14 cls 同時取得

NN 本体の出力は **32 logits（5-bit 真状態の softmax）のみ**。14 観測等価クラスは firmware 側で `STATE_TO_EQUIV[argmax32]` テーブル参照だけで導出するため、**追加の NN 推論コストはゼロ**。1 推論で `cls32` / `cls14` / `second32 候補` の 3 値を併記出力できます。

## なぜ「1 マイク 1 Ping」が成立するのか

家中にセンサを散らす方式は配線・電池交換・通信の地獄を抱えるのが常ですが、室内の音響インパルス応答（RIR: Room Impulse Response）は **窓 1 枚が開くだけでも全体のモードと残響が変わる** という性質を持ちます。**部屋を丸ごと共振器とみなして 1 点で全部聴く** ほうが筋がいい — それが IchiPing の出発点です。

## なぜ「アクティブセンシング」なのか

パッシブにマイクで生活音を聞くだけでは、家が静まりかえった時刻に観測できなくなります。IchiPing は **物理 Ping（200 Hz から 6 kHz への能動的指数掃引）を撃ち**、その応答を分析するアクティブ計測スタイルを取ります。

- 部屋の状態 (窓/扉の組合せ 32 通り) が変わると、各モードの周波数とダンピングが変わる
- 扉が開くと隣接室と結合し、単一ピークが対称・反対称ペアに分裂する
- 窓が開くと放射損失で Q が落ち、ピーク幅が広がる

このような物理的に裏付けのある変化を **1D CNN** に学習させ、組合せ状態を一発で当てに行きます。CNN backbone は ~14K パラメータの軽量設計で、MCXN947 内蔵の **NPU + PowerQuad DSP** で INT8 推論まで完結します。

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
