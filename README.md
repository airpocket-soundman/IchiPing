# IchiPing

> **イチ発のピンで、家のすべての窓を聴く**
> 1 個のマイクと NN で、連続音響空間内の窓・扉の開閉状態を **能動 chirp + RIR + 1D CNN** で同時推定するエッジ AI デバイス。

母プロジェクト: [airpocket-soundman/digikey_project §C4](https://github.com/airpocket-soundman/digikey_project) からピボットして専用リポジトリ化。応募先は DigiKey M1 / ROHM EDGE HACK 2026。

## 作品コンセプト — 「雨降ってきた、窓大丈夫？」

### シーン

外出中に空が暗くなり、雨が降り出した。スマホを取り出して「あれ、窓閉めてきたっけ…？」と不安になる。**家まで戻る時間も余裕もない**。

### IchiPing のソリューション

**1 個のマイクと 1 発の Ping (能動音響) だけで、家中の窓・扉 32 通りの組合せ状態を一発推定する** エッジ AI デバイス。これに **降雨センサ + M5Stamp Pico (ESP32)** をデモ用周辺機器として追加することで、次のフローが成立する:

1. 屋外の降雨センサが雨を検出 (GPIO 入力)
2. M5Stamp Pico → UART で IchiPing コントローラに推論トリガを送る
3. IchiPing が 1 Ping → MCU 上の Neutron NPU で 1.89 ms 推論 → **窓・扉 32 状態のうちどれか**を特定
4. M5Stamp Pico が Wi-Fi 経由でスマートホームクラウドに結果を送信
5. ユーザのスマホに通知「窓 a が開いてます！」

> **今回は PoC ですが、外部ネットワークへの接続用に ESP32 (M5Stamp Pico) を搭載しており、スマートホームシステムに統合する基本機能を実装しています。**

### 2 段オチ

**オチ 1**: 通知が来ても外出中だと…
- 「閉まってる」なら → 安心 ✓
- 「開いてる」なら → 知らされても何もできない 😞

**オチ 2**: **でも安心してください**。このデモ装置は窓を **サーボで自動開閉できます**。
スマホから「閉めて」をタップ → クラウド → M5Stamp Pico → UART → IchiPing → PCA9685 → SG90 ×5 → **物理的に窓を閉める** ✓

### 技術検証 (これまでに達成したこと)

- 単一マイク + 単一スピーカで **32 真状態すべてが識別可能** と判明 (MCU 実機 32cls / 14cls とも 100%, XL モデル 104K params)。
- 当初想定した「閉扉の向こうは観測不能 → 14 等価クラスが理論上限」は、実扉の漏れ (-20〜-30 dB 減衰) により否定された。詳細: [v12345 検証レポート](docs/v12345_report.html)。
- 推論は **MCXN947 内蔵 Neutron NPU で 7/7 op = 100% NPU 化、1.89 ms** で完結。

## ハードウェア前提（v1）

| 役割 | 部品 |
|---|---|
| MCU | **FRDM-MCXN947**（NPU + PowerQuad + I²S 多系統） |
| マイク | **INMP441**（I²S MEMS, 24-bit） — DC オフセット問題がなく chirp/RIR に好適 |
| スピーカ駆動 | MAX98357A I²S DAC + Class-D アンプ 3.2 W |
| サーボ駆動 | PCA9685（I²C, 16ch PWM）→ SG90 ×5 |
| 表示 | ILI9341 2.4" TFT 240×320 RGB565（SPI, FC3 LPSPI）。SH1106/SSD1306 OLED から差替え（[採用根拠](hardware/display_options.html)） |
| UI | トグル ×5 + EXEC ボタン + LED ×2 |
| 通信 (PC 連携) | USB-C CDC ／ OpenSDA UART 921600 bps |

### デモ用追加機器（雨検出 → スマホ通知シナリオ）

| 役割 | 部品 | 接続 |
|---|---|---|
| 降雨センサ | YL-83 等の安価モジュール | GPIO デジタル入力、屋外設置 |
| Wi-Fi モジュール | **M5Stamp Pico**（ESP32-PICO-D4） | IchiPing と **UART (LPUART)** 接続、降雨センサを **GPIO** で読む、**Wi-Fi** でクラウド送信 |
| スマートホーム連携 | クラウド (Home Assistant / 任意の MQTT broker) | M5Stamp から push、スマホアプリで受信 |

詳細は [docs/spec.html](docs/spec.html) を参照。

## リポジトリ構成

```
IchiPing/
├── index.html / README.md       (このページ)
├── tasks.html                   タスクボード（v0.1〜v2.0）
├── CLAUDE.html / CLAUDE.md      Claude 向け作業ガイド
├── .vscode/                     VS Code ワークスペース設定
│   ├── extensions.json          推奨拡張
│   ├── settings.json            Python / テスト設定
│   └── launch.json              F5 起動構成（receiver/verify/emulator/unittest）
├── docs/
│   ├── spec.html                C4 仕様書（正本 digikey_project の完全コピー）
│   ├── nn_design.html           NN 詳細設計
│   ├── mcu_deployment.html      MCXN947 デプロイ手順（eIQ Toolkit）
│   ├── bringup.html             実機ブリングアップ手順
│   ├── vscode_setup.html        VS Code セットアップ
│   ├── style.css                共通サイドバー / レイアウト CSS
│   └── img/                     SVG 図（dataflow / frame_format / nn_arch ...）
├── hardware/
│   ├── wiring.html / wiring.md  GPIO ↔ デバイス端子マップ
│   ├── wiring.svg               視覚配線図（バス色分け）
│   ├── netlist.csv              機械可読ネットリスト
│   ├── bom.html / bom.csv       部品表（DigiKey M1 想定、必須計 ~$252）
│   └── display_options.html     ディスプレイ候補比較（SH1106 推奨）
├── firmware/                    MCUXpresso 用 C コード
│   ├── README.html / README.md
│   ├── include/
│   │   ├── ichiping_frame.h     フレーム形式（PC 側と共有する単一の真実）
│   │   └── dummy_audio.h
│   ├── source/
│   │   ├── main.c               SysTick + LPUART + フレーム送信
│   │   ├── ichiping_frame.c     ヘッダ + CRC-16/CCITT パッカー
│   │   └── dummy_audio.c        合成 chirp 200→8 kHz + xorshift32 残響
│   └── host_build/              gcc/MinGW でホストビルドする足場
│       ├── Makefile             .so/.dll を作って ctypes 突合テストに使う
│       └── README.md
└── pc/                          PC 側 Python（受信・検証・訓練）
    ├── README.html / README.md
    ├── ichp_frame.py            フレーム形式の単一情報源（Python 側）
    ├── receiver.py              シリアル/TCP/file → WAV + CSV ラベル保存
    ├── emulator.py              実機なしでフレームを生成する偽 MCU
    ├── verify.py                受信ストリームを 8 項目で検証する CLI
    ├── test_frame_format.py     ヘッダ/CRC ラウンドトリップ単体テスト（9 件）
    ├── test_loopback.py         emulator → receiver E2E テスト（6 件）
    ├── test_ctypes_packer.py    C ↔ Python パッカー突合（gcc 必要、無ければ skip）
    ├── environment.yml          conda 環境定義（PyTorch / ONNX 含む）
    ├── requirements.txt         venv 用最小依存
    └── training/                NN 訓練パイプライン（v0.5）
        ├── README.md
        ├── model.py             1D-CNN マルチタスク（~14K params）
        ├── features.py          WAV → 整合フィルタ → 1024-bin log-mag
        ├── dataset.py           captures/ PyTorch Dataset
        └── train.py             訓練 + ベスト保存 + ONNX エクスポート
```

## 現在のステータス: **v12345 完了 — MCU 実機 32cls / 14cls とも 100%**

実ハード（INMP441 / MAX98357A / PCA9685 / SG90 ×5 / ILI9341 / トグル）配線・ファームウェア統合完了、
PC で学習・量子化・Neutron 変換、MCU 上で実機推論 (1.89 ms / NPU 比率 100%) まで一気通貫で動作。
8 モデル × 32 状態の sweep で **32cls / 14cls とも 100%** 達成 ([v12345 検証レポート](docs/v12345_report.html))。
当初予想だった「14 等価クラスが情報理論的天井」は実扉の漏れ (-20〜-30 dB 減衰) により否定され、
32 真状態すべてが識別可能と判明。

![v0.1 データフロー](docs/img/dataflow_v01.svg)

## クイックスタート

### 1. ファーム側

[firmware/README.md](firmware/README.md) を参照。要点:

1. MCUXpresso IDE で `frdmmcxn947` SDK から LPUART ベースのテンプレートを作成
2. `source/main.c` を本リポの [firmware/projects/01_dummy_emitter/main.c](firmware/projects/01_dummy_emitter/main.c) で置き換え、`ichiping_frame.c` + `dummy_audio.c` を追加、Include パスに `firmware/shared/include` を追加
3. ビルド → OpenSDA で書き込み

### 2. PC 側（conda 推奨）

```powershell
cd pc
conda env create -f environment.yml
conda activate ichiping
python receiver.py --port COM7 --baud 921600 --out ../captures
```

OpenSDA の COM ポート番号はデバイスマネージャで確認。venv 派の手順は [pc/README.md](pc/README.md) 参照。VS Code でセットアップする場合は [docs/vscode_setup.html](docs/vscode_setup.html) に従えば F5 で受信・検証が走る。

### 3. 動作確認用ユニットテスト（実機なし）

```powershell
cd pc
python -m unittest test_frame_format test_loopback -v
```

15 テストでカバー:
- `test_frame_format` 9 件: ヘッダ長 36 B、各フィールドのバイトオフセット、CRC-16/CCITT-FALSE の既知ベクタ、pack→unpack ラウンドトリップ
- `test_loopback` 6 件: `emulator.py` → `receiver.py` の E2E、`random_servo_angles` の C 互換性、chirp 構造

gcc/MinGW があれば `test_ctypes_packer.py` も走り、C 側 `ichp_pack_frame` と Python 側 `pack_frame` のバイト一致を 2 件で検証（無ければ自動 skip）。フレーム形式は MCU と PC で二重定義されており、ドリフトすると CRC が通らず実機通信が全滅するため、ここを実機到着前に必ず通す。

### 4. 受信ストリームの検証（実機到着後 / loopback 両対応）

```powershell
# 実機 100 フレームを 8 項目で検証、1 件でも FAIL なら exit 1
python verify.py --port COM7 --frames 100 --strict
# loopback: emulator が書いたファイルを検証
python verify.py --in ../captures/loopback.bin --strict
```

## 通信方式

**v0.1 は OpenSDA UART 921600 bps**（最も簡単に立ち上がる構成）。1 フレーム 64 KB を約 556 ms で転送、フレーム周期 3 秒なので帯域は余裕。USB CDC への置換は v0.3 以降のロードマップ項目。

## ロードマップ

- [x] v0.1: シリアル疎通 + ダミーデータ保存
- [x] v0.2: I²S DAC（MAX98357A）からの chirp 放射、INMP441 からの実音取り込み + 走査音方式確定 (chirp + baseline diff + Welch FFT)
- [x] v0.3: CMSIS-DSP rFFT (Welch) で RIR 抽出パイプライン MCU 実装
- [x] v0.4: 32 状態 × v1+v2+v3+v4+v5 (7,360 sample) 収集 + baseline jittering ×5 = 36,800 effective
- [x] v0.5: 14cls + 32cls 両 head 共存モデル (Neutron 互換 XL, ~104K params)
- [x] v0.6: PC FP32 で MCU 等価精度確認 (32cls / 14cls とも 100%)
- [x] v0.7: INT8 量子化 + Neutron 変換 (NPU 比率 7/7 = 100%, 108 KB, 1.89 ms)
- [x] **v1.0: MCU 実機 推論検証 (v12345 sweep, 8 モデル × 32 state, 32cls / 14cls とも 100%)（**現状**）**
- [ ] v1.5: TFT (ILI9341) 表示 + EXEC ボタンによる手動デモモード完成
- [ ] v1.6: baseline jittering 拡張 (別室 baseline 投入で更なる汎化)
- [ ] v2.0: ML63Q2557 + Solist-AI への移植（ROHM EDGE HACK 提出版） — 技術課題まとめ: [docs/solist_porting.html](docs/solist_porting.html)

## ライセンス

未定（DigiKey/ROHM コンテスト応募後に MIT 想定）。
