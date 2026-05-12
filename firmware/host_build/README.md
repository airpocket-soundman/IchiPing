# firmware/host_build — C 側 frame packer のホストビルド

`firmware/source/ichiping_frame.c` を MCUXpresso SDK なしで（普通の gcc で）共有ライブラリ化するための足場。

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

## テスト

```bash
cd pc
python -m unittest test_ctypes_packer -v
```

ライブラリが見つからない場合（gcc が無い環境）はテストが skip される。`test_frame_format` 側のテストは引き続き動くので、ヘッダ層の最低限の整合性は確認できる。

## クリーン

```bash
make clean
```
