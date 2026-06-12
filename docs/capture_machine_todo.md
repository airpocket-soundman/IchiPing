# 測定マシン側 TODO — docs レポートの欠落画像を復帰する

作成日: 2026-06-12 / 起票元: メイン開発マシン（音響データ非保有）

> **✅ 2026-06-12 消化済み（測定マシンにて）**
>
> | タスク | 結果 |
> |---|---|
> | analysis PNG 復帰（手順 1〜4） | 3 dataset とも `analysis/` が手元に存在（再生成不要）。207 枚をコミット `eb82524` |
> | v21+ 代表 wav の追加タスク | train_v21〜v25 + eval_v1 の 32 state × 6 = 192 枚をコミット `fa286ce` |
> | 判断ポイント（≤v20 画像を残すか） | メイン側が `.gitignore` を追跡対象に変更済み（`a8c8a52`）= 「残す」決定とみなし復帰を実施 |
> | figures_spec.md の「測定マシン待ち」図 | 図1並置・図2・図3 実データ版を生成（詳細は [figures_spec.md](figures_spec.md) 生成状況） |
>
> 残: push 後に GitHub 上で 3 レポート（full32_initial/noise/passive_test.html）の画像表示を確認（手順 5）。

以下は起票時の原文（手順の記録として残す）。

## 背景

`docs/` 配下の3つのテストレポートが参照する FFT/スペクトログラム画像（計 **207枚**）が、
どのクローンでも・GitHub 上でも表示できない状態だった。

原因は `.gitignore` が `captures/` をディレクトリごと除外していたため、レポートが指す
`captures/<dataset>/analysis/**/*.png` が **git 履歴に一度も入っていなかった**こと
（全106コミットを横断確認済み。「最近消えた」のではなく最初から未コミット）。

## メイン側で対応済み（pull すれば入る）

- **`.gitignore` 修正**: `captures/` 配下でも `analysis/**/*.png` だけは追跡対象に変更。
  wav・csv・npy 等の生データは引き続き除外。
  （`**/captures/**` → `!**/captures/**/` → `!**/captures/**/analysis/**/*.png` の3行構成）
- **`docs/nn_methods_compare.html`** の画像パス誤記を1件修正（`eval_xrun_noise_v1` → `eval_noise_v1`）。

→ 測定マシンでは **まず `git pull`** して上記を取り込むこと。

## 測定マシンでやること（画像実体はこのマシンにしか無い）

対象3データセットと欠落枚数:

| レポート | データセット | 欠落 |
|---|---|---|
| `docs/full32_initial_test.html` | `pc/captures/full_32_v2` | 69 |
| `docs/full32_noise_test.html` | `pc/captures/full_32_noise_v1` | 69 |
| `docs/full32_passive_test.html` | `pc/captures/full_32_passive_v1` | 69 |

各データセットにつき `analysis/overview/`（5枚）と 32状態 × `{fft.png, spectrogram.png}`（64枚）。

### 手順

1. `git pull`（修正済み `.gitignore` と nn_methods_compare を取り込む）

2. 各データセットの `analysis/` 画像が手元に在るか確認:
   ```bash
   ls pc/captures/full_32_v2/analysis/overview/
   ls pc/captures/full_32_noise_v1/analysis/overview/
   ls pc/captures/full_32_passive_v1/analysis/overview/
   ```

3. **無ければ raw wav から再生成**（wav が残っている場合のみ可）:
   ```bash
   cd pc
   uv run python analyze_full32.py --root captures/full_32_v2
   uv run python analyze_full32.py --root captures/full_32_noise_v1
   uv run python analyze_full32.py --root captures/full_32_passive_v1
   ```
   ※ 出力先は既定で `<root>/analysis/`。raw wav も既に消している場合は復元不能
   （これら3つは ≤v20 = 旧ハード era のデータ。下記「判断ポイント」参照）。

4. 画像を追跡に追加してコミット・push:
   ```bash
   git add pc/captures/full_32_v2/analysis \
           pc/captures/full_32_noise_v1/analysis \
           pc/captures/full_32_passive_v1/analysis
   git status   # *.png のみ stage されていること（wav/csv は除外されるはず）を確認
   git commit -m "data: full_32 v2/noise_v1/passive_v1 の analysis PNG を追跡追加（docs レポート画像復帰）"
   git push
   ```

5. push 後、GitHub もしくは別クローンで3レポートの画像が表示されることを確認。

## 追加タスク — v21+ 各測定の代表 wav をコミット（解析用サンプル）

メイン側で `.gitignore` を追加修正済み: **v21 以降の各測定 × 各条件(state) につき
`frame_000000.wav` 1枚だけ**を追跡対象にした（残り frame・生データは引き続き除外）。

- 対象: `full_32_train_v21`〜`v25`（＋将来 `v26`以降）、`full_32_eval_v1`
- 対象外: `passive_v1`、≤v20 の旧データ

測定マシンで `git pull` 後、代表 wav を追加コミット:

```bash
# *.wav のうち frame_000000.wav だけが stage 対象になる（gitignore 制御済み）
git add pc/captures/full_32_train_v2[1-5] pc/captures/full_32_eval_v1
git status   # frame_000000.wav と analysis/*.png のみであることを確認
git commit -m "data: v21+ 各測定の代表 wav (frame_000000) を解析用にコミット追加"
git push
```

代表が `frame_000000` で不都合な state があれば、その state だけ別 frame を `git add -f` で追加してよい。

## 判断ポイント

これら3データセットは **v2 / noise_v1 / passive_v1 = ≤v20（旧ハード）era** のもの。
「v20 以前は不要」という現方針に照らすと、画像を復帰させずに

- **レポート自体を obsolete 扱い**にして `index.html` のリンクに「(旧ハード・参考)」注記を付ける、
  あるいはリンクをアーカイブ節へ移す

という選択もある。**画像を残す価値があるかを先に決めてから** 上記手順を実行するか判断すること。
（メイン側で「残す」と決まればこの TODO を消化、「不要」ならレポートのリンク整理タスクに切替。）
