# 06_mic_test ブリングアップ作業ノート（2026-05-15 時点）

INMP441 を SAI1 RX で取り込もうとして詰まったので、原因切り分けの過程と現時点での残課題をここに残す。動き出したら全部消すか、`README.md` 側に成果だけ吸収する。

## 結論サマリ

SAI ファームウェア層は **設定が全て正しいと確認済み**。BCLK/FS は配線元（J1.1 / J1.11）まで出ている。残るのは「INMP441 が SD ラインに何も出力しない」という純粋なハード側問題で、原因はまだ未確定（モジュール故障 / 配線ミス / L/R タイミングのどれか）。

> **2026-05-16 追補**: 同日 [07_speaker_test](../07_speaker_test/README.md) で **MAX98357A から実音が出ることを確認** (commit `385b619`)。同じ `sai_speaker_init` / `sai_mic_init` が呼ぶ SAI フレーマ設定が物理的に有効な BCLK/FS/TXD を出している証拠なので、06 の「データ全 0」問題は SAI 側ではなく INMP441 モジュールまたは配線の方に切り分け可能。`SAI_WriteBlocking` のスタック過剰読み出しバグも同セッションで発見・回避済 (`sai_speaker.c` 側で per-sample `SAI_WriteData` に変更)。

> **2026-05-16 追補 2 (`SAI_ReadBlocking` の対称バグを発見)**: 07 で見つけた TX 側の SDK バグの **ミラー版が RX 側にも存在**することを確認 — `SAI_ReadBlocking()` の内部実装は `bytesPerWord = RCR1 * (bitWidth/8U)` バイトを 1 回の FRF アサート時に一括読み出しする。SDK デフォルトの RX ウォーターマーク (RCR1=4) と 32-bit ワード幅では **1 回あたり 16 バイト読む**が、`sai_mic_record_blocking` が渡していた `&left` は 4 バイトのローカル変数。結果として **スタック上の隣接 12 バイトを毎サンプル踏みつぶす**。サンプル値が常に 0 に見えていた現象の最有力候補。修正は per-sample `SAI_ReadData(base, ch)` + `kSAI_FIFORequestFlag` 待ち + **RX ウォーターマークを 1 に下げる** (FRF が 1 サンプル単位で立つようにしてオーバーラン回避)。詳細は本 NOTES 末尾の「2026-05-16 追加修正」セクション。

## これまでに直したもの（ファーム側）

| 症状 | 原因 | 修正 | コミット |
|---|---|---|---|
| `SAI_ReadBlocking` で永久ハング、RFR0=0 のまま | `SAI_Init(base)` を呼んでいなかった | `sai_mic.c` 冒頭で `SAI_Init`, `SAI_TxReset`, `SAI_RxReset` 追加 | `1e94345` |
| `BCLK` が動かない・`FEF` underflow で TX 停止 | TCR4.FCONT=0 で FIFO underflow 時に framer 停止 | `base->TCR4 \|= (1u << 28)` で FCONT を立てる | `1e94345` |
| BCLK divider が動かない（WSF 立たず, RFR0=0） | `MCR.MOE=0` だと MCLK ピンが**入力モード**になり、TCR2.MSEL=01（MCLK option 1）の BCLK source に外部 MCLK 信号を要求してしまう。MCLK ピンを物理的に出していないので永遠に待ち続ける | `SAI_SetMasterClockConfig(mclkOutputEnable=true)` で MCR.MOE=1 にして内部 MCLK divider 出力に切替 | `1e94345` |
| 1 MHz BCLK が J1 ヘッダで edge 崩れ (~1.1 Vpp サインに見える) | SAI ピンのドライブ強度がデフォルト `kPORT_LowDriveStrength` だったため、ジャンパー配線 + デバイス入力負荷で edge が CMOS VIH を割る | `pin_mux.c` の SAI ピン全てを `kPORT_HighDriveStrength` に変更、加えて PIO1_0 (J5.6/J2.17) を BCLK の SJ-無し直結ルートとして並列にマックス | `d4155a9` |
| データ全 0（`peak/rms` が常に 0） | SDK の **`SAI_ReadBlocking` がスタックを過剰書き込み**するバグ。`bytesPerWord = RCR1 * 4` バイト一括読み出す実装で、デフォルト RCR1=4 だと 1 サンプル要求につき 16 バイトを `&left`（4 バイト）に書き込み、隣接 12 バイトのスタックを破壊。コンパイラの最適化次第で `left` が常に 0 に見える / 戻りアドレスが壊れて未定義動作 | per-sample `SAI_ReadData(base, ch)` + `kSAI_FIFORequestFlag` 待ち + **RX ウォーターマーク 1** で書き直し | (本セッション) |

このセットを `sai_mic.c` と `sai_speaker.c` の両方に入れている（08 で同時駆動する想定）。

## 現在のレジスタダンプ（正常動作期待値）

書き込み直後のシリアル出力で以下が出ていれば SAI 側はクリアン:

```
TCSR=0x90170000   (TE=1, BCE=1, WSF=1, FEF=1, FWF=1, FRF=1)
RCSR=0x90140000   (RE=1, BCE=1, WSF=1, FEF=1)
TCR4=0x10011F3B   ← bit 28 (FCONT)=1
MCR =0x40000000   ← bit 30 (MOE)=1
TMR =0x2          (MonoLeft: 右をマスク = 左ch のみ通す)
RMR =0x2          同上
TCR2.MSEL=1, TCR2.DIV=22, BCLK=48MHz/(2*23)≈1.043MHz
```

`RFR0` の下位16bit（write pointer）が動いて FIFO に流入しているが、サンプル値が全部 0 — つまり SD ラインがずっと 0V 固定ということ。

## 現状の残課題（ハード側切り分け）

書き込み直後、`Captured 8000 samples, exiting.` が出た後の状態で、**BCLK と FS はずっと出続けている**（TX framer を disable していない上に FCONT=1）。テスタを **AC mV レンジ**（V～ または mV～）にして順番に当てる。

| # | 測定プローブ位置 | レンジ | 期待値 | NG 時の意味 |
|---|---|---|---|---|
| 1 | 基板 **J1.1**（BCLK 出口） ↔ GND | AC mV | 数百 mV〜1 V | SAI 設定がウソ。要再追求 |
| 2 | 基板 **J1.11**（FS 出口） ↔ GND | AC mV | 数百 mV〜1 V | 同上 |
| 3 | INMP441 ボードの **SCK パッド** ↔ GND | AC mV | 数百 mV〜1 V | BCLK ジャンパ線断線 / 誤挿入 |
| 4 | INMP441 ボードの **WS パッド** ↔ GND | AC mV | 数百 mV〜1 V | FS ジャンパ線断線 / 誤挿入 |
| 5 | INMP441 ボードの **VDD パッド** ↔ GND | DC | 3.3 V | 電源未接続 |
| 6 | INMP441 ボードの **L/R パッド** ↔ GND | DC | 0 V | L/R floating |
| 7 | INMP441 ボードの **SD パッド** ↔ GND | AC mV | 数十〜数百 mV | INMP441 起動失敗 or モジュール故障 |
| 8 | 基板 **J1.15**（SD 取込口） ↔ GND | AC mV | 7 と同じ | SD ジャンパ線断線 |

切り分け早見:

- 1〜2 OK / 3〜4 NG → ジャンパ線問題（基板側 OK、線か誤挿入）
- 3〜6 OK / 7 NG → **INMP441 モジュール自体が動いていない**（要モジュール交換 or 別 I²S Master で疎通確認）
- 7 OK / 8 NG → SD 線断線

## 追加で疑うべきこと

- **L/R のラッチタイミング**: INMP441 は電源 ON 時の L/R レベルでチャネル選択をラッチする可能性。L/R を後から GND につないでも認識されないなら、USB ケーブル抜き挿しで電源リセット → 再測定。
- **L/R 極性反転品**: 中華系互換ブレイクアウトの一部は L/R 極性が逆（GND=右ch）。実験として `sai_mic.c` の `kSAI_MonoLeft` を `kSAI_MonoRight` に変えてビルド → 数値が出るなら確定。試した結果 → **このモジュールは反転品ではない**（MonoRight でも全 0 だった）。
- **モジュール故障**: ESD・電源逆挿し等で SD 出力が殺されている可能性。予備があれば差し替え推奨。

## 配線確定（FRDM-MCXN947, [pin_plan.md](../../../hardware/pin_plan.md) より）

| INMP441 端子 | 接続先 | MCU pin |
|---|---|---|
| VDD | 基板 3V3 | — |
| GND | 基板 GND | — |
| L/R | 基板 GND（左ch 選択） | — |
| SCK ← | **J1.1**（SAI1_TX_BCLK） | P3_16 Alt10 |
| WS ← | **J1.11**（SAI1_TX_FS） | P3_17 Alt10 |
| SD → | **J1.15**（SAI1_RXD0） | P3_21 Alt10 |

## 動き出した後にやること

1. 診断用 main.c を通常版に戻す（`s_window` 配列と `print_stats` を復元、`sai_mic_record_blocking` を使う構成へ）
2. 07_speaker_test を同じ SAI ドライバで動作確認
3. 08_mic_speaker_test で全二重動作確認
4. このファイルを削除 or `README.md` に成果だけ畳む
5. コミットメッセージで MCR.MOE=1 と FCONT 修正のメモを残す

## 2026-05-16 追加修正（`SAI_ReadBlocking` バグ）

### 発見の経緯

07_speaker_test のブリングアップ中に **`SAI_WriteBlocking` が `&local_uint32` を渡されると 28 バイト読み込んで FIFO に流す**バグを特定し ([07/README.md](../07_speaker_test/README.md#L?) の「踏んだ落とし穴」)、純音が ぐだぐだ → クリーン再生になった。同じ実装パターンが RX 側にあるか調べたところ、対称形のバグが [fsl_sai.c:1906-1931](D:/GitHub/mcuxsdk/mcuxsdk/drivers/sai/fsl_sai.c) で見つかった。

### バグ詳細

`SAI_ReadBlocking()` 内部:

```c
bytesPerWord = bitWidth / 8U;                                  // = 4 (32-bit ワード幅)
if (base->RCR1 && (bytesPerWord <= UINT32_MAX / base->RCR1)) {
    bytesPerWord = base->RCR1 * bytesPerWord;                  // = 4 * 4 = 16
}
while (i < size) {
    wait FWF;
    SAI_ReadNonBlocking(..., buffer, bytesPerWord);            // 16 バイト書き込む
    buffer += bytesPerWord;
    i += bytesPerWord;
}
```

`sai_mic_record_blocking` の呼び出し:

```c
uint32_t left;
SAI_ReadBlocking(base, 0u, 32u, (uint8_t *)&left, sizeof(left));   // size = 4
```

`size=4` でループに入る → 16 バイト読まれる → **`&left` の隣 12 バイトのスタック領域 (戻り値, 他のローカル, etc.) が破壊**される。
ループは 1 回で抜けるが、関数の戻り処理で破壊された return address を参照して未定義動作になる、or  `left` がレジスタ配置されていれば値だけは生き残り **最初の 4 バイトのみ正しく読まれる**。

しかし真のクリティカル パスは `SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag` の watermark = 4 デフォルト。FRF は「level >= 4」で立つので、per-sample 読み出し時には 4 サンプル分待たされてその間に新サンプルが流入し続け、ループ全体が遅延 / オーバーランで実用にならない。

### 修正

[sai_mic.c](../../shared/source/sai_mic.c) の 2 箇所:

**(1) init: RX ウォーターマークを 1 に**
```c
rxConfig.fifo.fifoWatermark = 1u;
SAI_RxSetConfig(base, &rxConfig);
```

**(2) record_blocking: per-sample `SAI_ReadData` + FRF 待ち**
```c
for (size_t i = 0; i < n_samples; i++) {
    while (!(SAI_RxGetStatusFlag(base) & kSAI_FIFORequestFlag)) { /* spin */ }
    out[i] = inmp441_word_to_int16(SAI_ReadData(base, 0u));
}
```

これで「1 サンプル要求 → 1 ワード読む」が成立し、スタック破壊もオーバーランも起きない。

### 期待される動作変化

修正前: `[   N] peak=0 rms=0 dc=0 zcr=0Hz` がループ
修正後: 静かな部屋で peak≈数十、拍手で peak が数千〜1 万に跳ね上がる

もし修正後も全 0 なら、本当に INMP441 モジュール故障 / 配線断線で SD ラインが 0V 固定ということになる（その場合は前述の「現状の残課題」セクションのテスタ チェック表に従って切り分け）。

### `[SAI MIC]` 起動時ダンプ

init 末尾で SAI レジスタを 1 行出すようにした:

```
[SAI MIC] sai_clk=48000000 Hz TCSR=0x9017xxxx RCSR=0x9014xxxx TCR2=0x07000016 TCR4=0x10011f3b MCR=0x40000000 RCR1=1
```

- TCSR bit31 (TE) + bit28 (BCE) → TX framer 動作中
- RCSR bit31 (RE) は `sai_mic_record_blocking` 起動まで 0 のまま (sync mode RX は record 時に有効化)
- RCR1=1 → ウォーターマーク修正が効いている確認

## 参考にしたファイル

- 修正済みドライバ: [sai_mic.c](../../shared/source/sai_mic.c) / [sai_speaker.c](../../shared/source/sai_speaker.c)
- 診断用 main: [main.c](main.c)
- SDK SAI ドライバ: `d:/workspace/sdks/mcuxsdk/mcuxsdk/drivers/sai/fsl_sai.{c,h}`
- MCXN947 SAI レジスタ定義: `d:/workspace/sdks/mcuxsdk/mcuxsdk/devices/MCX/MCXN/periph/PERI_I2S.h`
- ボード仕様: [docs/pdf/FRDM-MCXN947BoardUserManual.pdf](../../../docs/pdf/FRDM-MCXN947BoardUserManual.pdf)
