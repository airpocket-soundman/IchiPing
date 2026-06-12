"""state_labels.py の s↔h 変換テスト。"""
from __future__ import annotations

import unittest

from state_labels import h_to_s, s_to_h


class TestStateLabels(unittest.TestCase):
    def test_known_pairs(self):
        # (s, h) の既知ペア。s = a b c AB BC / h = c BC b AB a
        pairs = [
            ("s00000", "h00000"),
            ("s10000", "h00001"),   # a 開
            ("s01000", "h00100"),   # b 開
            ("s00100", "h10000"),   # c 開
            ("s00010", "h00010"),   # AB 開 (index 3 は両表記で不動)
            ("s00001", "h01000"),   # BC 開
            ("s10010", "h00011"),   # a + AB 開
            ("s00011", "h01010"),   # AB + BC 開
            ("s11111", "h11111"),
        ]
        for s, h in pairs:
            self.assertEqual(s_to_h(s), h, f"{s} → {h}")
            self.assertEqual(h_to_s(h), s, f"{h} → {s}")

    def test_roundtrip_all_32(self):
        for v in range(32):
            s = "s" + format(v, "05b")
            self.assertEqual(h_to_s(s_to_h(s)), s)
            h = "h" + format(v, "05b")
            self.assertEqual(s_to_h(h_to_s(h)), h)

    def test_not_involution(self):
        # s→h を 2 回適用しても元に戻らない (置換が自己逆でない) ことの確認。
        # h ラベルを誤って s_to_h に通すミスを検出できないため関数側で prefix 検証する
        s = "s10000"
        h = s_to_h(s)
        with self.assertRaises(ValueError):
            s_to_h(h)   # prefix 's' でないので拒否される

    def test_invalid(self):
        for bad in ("s1001", "x10010", "s1001a", "h0000", ""):
            with self.assertRaises(ValueError):
                (s_to_h if bad.startswith("s") else h_to_s)(bad)


if __name__ == "__main__":
    unittest.main()
