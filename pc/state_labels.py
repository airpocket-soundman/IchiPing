"""状態ラベルの表記変換 — s 表記 (サーボ論理順) ↔ h 表記 (間取り順)。

| 表記 | ビット順 (左→右) | 用途 |
|---|---|---|
| sABCDE | a, b, c, AB, BC | データ採取・学習・captures ディレクトリ名 (正本) |
| hABCDE | c, BC, b, AB, a | 資料図・プレゼン (模型を端から見た開口部の空間順) |

a/b/c = 部屋 A/B/C の窓、AB/BC = 部屋間の扉。1 = 開, 0 = 閉。

注意: s→h と h→s の並べ替えは互いに逆置換だが**同一ではない**
(2 回適用しても元に戻らない)。必ずこのモジュールの関数を使うこと。

例: s10010 (a=開, AB=開) → h00011
"""
from __future__ import annotations

S_ORDER = ("a", "b", "c", "AB", "BC")
H_ORDER = ("c", "BC", "b", "AB", "a")

# h[i] = s[_S2H[i]] / s[i] = h[_H2S[i]]
_S2H = tuple(S_ORDER.index(name) for name in H_ORDER)   # (2, 4, 1, 3, 0)
_H2S = tuple(H_ORDER.index(name) for name in S_ORDER)   # (4, 2, 0, 3, 1)


def _convert(label: str, src_prefix: str, dst_prefix: str,
             perm: tuple[int, ...]) -> str:
    if len(label) != 6 or label[0] != src_prefix or any(c not in "01" for c in label[1:]):
        raise ValueError(f"invalid {src_prefix}-label: {label!r}")
    bits = label[1:]
    return dst_prefix + "".join(bits[i] for i in perm)


def s_to_h(label: str) -> str:
    """s 表記 (a b c AB BC) → h 表記 (c BC b AB a)。例: s10010 → h00011"""
    return _convert(label, "s", "h", _S2H)


def h_to_s(label: str) -> str:
    """h 表記 (c BC b AB a) → s 表記 (a b c AB BC)。例: h00011 → s10010"""
    return _convert(label, "h", "s", _H2S)
