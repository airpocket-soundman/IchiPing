# CLAUDE.md — IchiPing 作業ガイド

このリポは [digikey_project](../digikey_project) **§C4 の派生実装リポ**です。アイデア・仕様の **正本は digikey_project 側**にあり、本リポはそこから生まれた実装専用のサブプロジェクトです。

## 🟡 タスクを始める前に必ず実行

ユーザーから何か依頼を受けたら、**最初に必ず**以下を確認してください。これは省略しないでください。

1. **正本（digikey_project）に変更が入っていないか調べる**

   ```
   d:/GitHub/digikey_project/details/C4-IchiPing.html   ← IchiPing 仕様の正本
   d:/GitHub/digikey_project/ideas.md   §C4 セクション
   d:/GitHub/digikey_project/ideas_brief.md   C4 行
   d:/GitHub/digikey_project/digikey_plan.md   採用ボード方針
   ```

   推奨コマンド:
   ```bash
   git -C d:/GitHub/digikey_project log --oneline -20 -- details/C4-IchiPing.html ideas.md ideas_brief.md
   ```

2. **本リポの複製版と差分があるか調べる**

   ```bash
   diff d:/GitHub/digikey_project/details/C4-IchiPing.html d:/GitHub/IchiPing/docs/spec.html
   ```

   差分があった場合、それは正本側で更新が入ったまま IchiPing に伝播していない状態です。

3. **差分があれば伝播してから本来のタスクに入る**

   - **必ず先にユーザーに「正本側で X が変わっています。先に同期しますか？」と確認**。勝手に上書きしないこと。
   - 同期が承認されたら、§「同期手順」に従って関連ファイルを更新。
   - 同期後、当初のタスクに進む。

## 🔄 正本 → IchiPing 同期手順

正本側で変更があった場合の影響範囲は次の通り。**変更内容に応じて該当箇所だけ更新**してください。

| 正本の変更箇所 | IchiPing 側で更新するもの |
|---|---|
| `details/C4-IchiPing.html` 全般 | `docs/spec.html` を上書きコピー（**例外 1**: 本文中の `../ideas.html` 等、正本リポ内兄弟ファイルへの相対リンクは、IchiPing 側では絶対 URL `https://github.com/airpocket-soundman/digikey_project/blob/main/...` に置換する。**例外 2**: `<head>` 内に `<link rel="stylesheet" href="style.css">` を `</style>` の後に追記して共通サイドバー CSS を読み込ませる。**本文・図・コードブロック・`<style>` の中身は完全一致を維持**） |
| §6 ハードウェア BOM の部品変更 | `hardware/wiring.svg`、`hardware/wiring.md`、`hardware/netlist.csv`、`README.md` の BOM 表 |
| §3 信号処理パイプラインの変更 | `firmware/projects/01_dummy_emitter/main.c` の `ICHP_SAMPLE_RATE`、`ICHP_SAMPLE_COUNT`、`firmware/shared/source/dummy_audio.c`、`pc/receiver.py` |
| フレーム形式の変更（サンプル形・ラベル種類） | `firmware/shared/include/ichiping_frame.h`（正本）+ `pc/receiver.py` の `HEADER_FMT` を同時更新 |
| §7 実験模型・サーボ数の変更 | `hardware/wiring.svg`、`hardware/wiring.md` のサーボ列、`firmware` のサーボ角配列サイズ |
| プロジェクト名・コンセプトの変更 | `README.md`、`CLAUDE.md`（このファイル）、各ドキュメントのタイトル |

同期コミットのメッセージ例:
```
sync: digikey_project C4 §6 から MIC を INMP441 → ICS-43434 に変更

正本側で TDK 後継品への置き換えが反映された (digikey_project commit abc1234)。
hardware/wiring.{svg,md,csv} と firmware/README.md を更新。
```

## 🔁 逆方向（IchiPing → 正本）の更新

実装中に判明した知見が**アイデア記述レベル**の更新を要する場合、digikey_project も同期更新してください。

例:
- 部品選定が現場の都合で変わった → `digikey_project/details/C4-IchiPing.html` §6 を更新
- 検出スコープ・手法が変わった → 同 §1〜§4 を更新
- 一行説明が変わった → `digikey_project/{ideas.md, ideas.html, ideas_brief.md}` の C4 行を更新

**両リポを 1 セットの commit でまとめず**、それぞれ独立に commit してください（digikey_project は別の GitHub リポ、IchiPing も別リポ）。コミットメッセージに「対応する変更: <相手リポの commit hash>」を書いておくと追跡しやすい。

## 📁 正本との対応関係（早見表）

| 本リポ | 正本（digikey_project） |
|---|---|
| `docs/spec.html` | `details/C4-IchiPing.html`（**完全コピー** — 単純な複製管理） |
| `README.md` のロードマップ | `ideas.md` §C4 + `digikey_plan.md` |
| `hardware/wiring.svg` 配線図 | 正本にはなし（IchiPing 側で発生・維持） |
| `firmware/*` ファーム | 正本にはなし |
| `pc/receiver.py` PC スクリプト | 正本にはなし |

つまり `docs/spec.html` 以外は **IchiPing 側で独立して育てるもの**で、正本変更があっても**自動的には影響しません**。ただし正本の仕様変更が部品や信号処理を変えれば、それに合わせて配線図・ファームを更新する必要があるという形で間接的に波及します。

## 📝 プロジェクト名の経緯

- 原案: **DoorBaro**（高分解能気圧センサで家全体イベントを推定）
- 中間: **WindowGuard**（仮称）
- 現名: **IchiPing**（能動 chirp + マイク 1 個 + 1D CNN — 「イチ」哲学）

旧名で書かれた参照を見つけたら IchiPing に統一してください。詳細経緯は `docs/spec.html` §0「このページの位置づけ」。

## 🔧 主要な技術的前提

これらは正本の C4 spec §6 から確定済で、変えるならユーザー確認が必要:

- **MCU**: FRDM-MCXN947
- **マイク**: INMP441（I²S MEMS, 24-bit、DC オフセット問題なしで chirp/RIR 用途に好適）
- **DAC**: MAX98357A
- **サーボ駆動**: PCA9685（I²C, 16ch PWM）→ SG90 ×5
- **データ経路 v0.1**: **OpenSDA UART 921600 bps**（実装難易度最小の選択）。後で USB CDC に置換予定
- **PC 側環境**: conda（`pc/environment.yml`）を主、venv（`pc/requirements.txt`）を副

## 📋 HTML と MD のペアは同期させる

本リポは GitHub 上で読む人（`.md`）と、ローカルで開く HTML サイトとして読む人（`.html`）の両方を想定するため、以下のペアは **内容を一致させて運用する**。片方だけ更新するのは禁止。

| HTML | Markdown | 役割 |
|---|---|---|
| `index.html` | `README.md` | トップページ |
| `CLAUDE.html` | `CLAUDE.md` | このガイド |
| `firmware/README.html` | `firmware/README.md` | firmware セットアップ |
| `pc/README.html` | `pc/README.md` | PC スクリプト |
| `hardware/wiring.html` | `hardware/wiring.md` | 配線テーブル |

**更新時の手順**

1. **片方を編集したら、必ずもう片方も同じ意味の変更を反映する**。リポ構成ツリー、BOM 表、コマンド例、ファイル名一覧、テストカバレッジ件数のような「事実データ」は片方だけ古いまま残さない。
2. 構造表現の差（HTML 側で H2 を 3 つに分けて MD 側で H2 一つ＋本文番号、等）は許容してよい。**揃えるべきは情報・指示・コマンド例・データ**であって、見出しの粒度ではない。
3. 図は SVG として `docs/img/` に置き、HTML からは `<img src="...">`、MD からは `![alt](...)` の **同じファイル**を参照する（ASCII 図禁止ルールと併用）。

**毎タスク完了前のチェック**

ペアの片方を編集した直後（あるいは PR を投げる前）に、見出し構造の差分を確認する。手っ取り早い手段:

```bash
# H1〜H3 を MD/HTML 両側から拾って並べる
python - <<'PY'
import re
from pathlib import Path
pairs = [
    ('index.html', 'README.md'), ('CLAUDE.html', 'CLAUDE.md'),
    ('firmware/README.html', 'firmware/README.md'),
    ('pc/README.html', 'pc/README.md'),
    ('hardware/wiring.html', 'hardware/wiring.md'),
]
def html_h(t):
    t = re.sub(r'<(pre|code)[^>]*>.*?</\1>', '', t, flags=re.DOTALL)
    return [re.sub(r'<[^>]+>', '', m.group(2)).strip()
            for m in re.finditer(r'<h([1-3])[^>]*>(.*?)</h\1>', t, re.DOTALL)]
def md_h(t):
    fence, out = False, []
    for ln in t.splitlines():
        if re.match(r'^\s*```', ln): fence = not fence; continue
        if fence: continue
        m = re.match(r'^(#{1,3})\s+(.+)$', ln)
        if m: out.append(m.group(2).strip())
    return out
for h, m in pairs:
    H = html_h(Path(h).read_text(encoding='utf-8'))
    M = md_h(Path(m).read_text(encoding='utf-8'))
    print(f'{h}: {len(H)} headings | {m}: {len(M)} headings')
PY
```

完全な heading 一致は不要。ただし「片方にしかないトピック」が出たら **意図的な構造差か内容欠落かを判断**して対応する。

**ペアになっていないファイル**

以下は現時点では HTML のみ／MD のみで運用しており、ペア化義務は無い。必要になったタイミングで両形式に展開すれば良い:

- HTML のみ: `tasks.html`, `docs/spec.html`, `docs/nn_design.html`, `docs/mcu_deployment.html`, `docs/bringup.html`, `docs/vscode_setup.html`, `hardware/bom.html`, `hardware/display_options.html`
- MD のみ: `firmware/host_build/README.md`, `pc/training/README.md`

## 🖼️ ドキュメント内の図は SVG で書く

**ASCII アート / 罫線図 / コードブロック内の擬似図はすべて禁止**。図示が必要な場面では必ず SVG を使う:

- 信号フロー、データフロー、状態遷移、シーケンス図 → `<svg>` で構造化
- 寸法図、配線図、配置図 → 同様
- markdown ドキュメントでも `<svg>` インラインで埋め込む（GitHub は markdown 内の SVG をレンダリングする）
- HTML ドキュメントでは外部 `.svg` 参照 or インライン `<svg>` どちらでも OK
- 既存ドキュメントを編集する際、ASCII 図を見つけたら **その編集ついでに SVG に置換**する

理由: ASCII 図は文字数で構造を表現するため画面幅／フォント幅で容易に崩れる。仕様書（正本 `details/C4-IchiPing.html`）が全面 SVG で書かれているため、本リポも同じ表現規約に揃える。

`docs/style.css` の色変数（`--accent`, `--accent-2`, `--warn`, `--pink`）を SVG 内でも使うとテーマ統一できる。

## 🚫 やらないこと

- `docs/spec.html` を IchiPing 側だけで編集する（→ 正本 `details/C4-IchiPing.html` 側で編集して同期させる）
- 正本変更の確認を省略していきなり実装に入る
- 「最近正本見たから今回はスキップ」と判断する（**毎タスク開始時に必ず確認**。前回の確認はキャッシュではない）
- **ドキュメント内に ASCII アート / 罫線図を書く**（→ SVG にする）
