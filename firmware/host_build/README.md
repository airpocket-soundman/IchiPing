# firmware/host_build — C 側 frame packer のホストビルド

`firmware/shared/source/ichiping_frame.c` を MCUXpresso SDK なしで（普通の gcc で）共有ライブラリ化するための足場。

## 目的

[../../pc/ichp_frame.py](../../pc/ichp_frame.py) の `pack_frame` と
[../source/ichiping_frame.c](../source/ichiping_frame.c) の `ichp_pack_frame` が、
**同じ入力に対してバイト単位で同一**の出力を返すことを Python の `ctypes` 越しに確認するための足場。

C 側と Python 側でフレーム形式がドリフトすると CRC が通らず実機通信が全滅するため、
これが最も強い回帰検証になる（ヘッダ層を 100% カバー）。

## ビルド要件

| プラットフォーム | コンパイラ |
|---|---|
| Linux | `gcc`（apt/dnf でデフォルト） |
| macOS | `gcc` or `clang`（Command Line Tools） |
| Windows | **MinGW-w64**（MSYS2 推奨。`pacman -S mingw-w64-x86_64-gcc`） |

`ichiping_frame.c` の依存は `<stdint.h>`, `<stddef.h>`, `<string.h>` のみ。MCUXpresso SDK は不要。

## ビルド

```bash
cd firmware/host_build
make
```

生成物:
- Linux: `libichp.so`
- macOS: `libichp.dylib`
- Windows: `ichp.dll`

## 確認方法

このディレクトリはファームではないので実機・シリアル不要。**PC でユニットテストを 1 本走らせるだけ**。

### A. ライブラリのリンクと ctypes 突合テスト（本筋）

1. 上記 `make` で `.so/.dylib/.dll` が生成されることを確認
2. テストを実行:
   ```bash
   cd ../../pc
   conda activate ichiping
   python -m unittest test_ctypes_packer -v
   ```
3. 期待出力:
   ```
   test_pack_frame_matches_python ... ok
   test_crc16_matches_python ... ok
   ----------------------------------------------------------------------
   Ran 2 tests in 0.018s

   OK
   ```

2 件のテスト:
- `test_pack_frame_matches_python` — 同じ入力に対して C 側 `ichp_pack_frame` と Python 側 `pack_frame` がバイト単位で同一フレームを出すか
- `test_crc16_matches_python` — CRC-16/CCITT-FALSE の C 側と Python 側の実装一致（既知ベクタ + ランダム入力）

### B. gcc が無い環境

ライブラリが見つからない場合 `test_ctypes_packer` は **自動 skip**（fail ではない）:

```
test_pack_frame_matches_python ... skipped 'libichp not built'
```

このときも `test_frame_format` と `test_loopback` は通るので最低限のフレーム整合性は保証される。**実機を繋ぐ前に host_build まで通しておくのを推奨**。

## クリーン

```bash
make clean
```
