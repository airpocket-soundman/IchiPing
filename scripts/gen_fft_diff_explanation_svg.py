"""Generate docs/img/fft_diff_explanation.svg.

3-row plot showing FFT magnitude of s00000 (baseline), s10000 (window a open),
and their diff. Used in protopedia article to explain why diff-from-baseline
improves SNR over raw FFT.
"""
from __future__ import annotations

import bisect
import csv
import math
from pathlib import Path


def load_fft(path):
    freqs, mags = [], []
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            freqs.append(float(row[0]))
            mags.append(float(row[1]))
    return freqs, mags


def main():
    root = Path('.')
    f0, m0 = load_fft(root / 'pc/captures/full_32_v1/analysis/s00000/fft.csv')
    f1, m1 = load_fft(root / 'pc/captures/full_32_v1/analysis/s10000/fft.csv')

    fmin, fmax = 100.0, 6000.0
    istart = bisect.bisect_left(f0, fmin)
    iend = bisect.bisect_left(f0, fmax)
    n_target = 500
    step = max(1, (iend - istart) // n_target)

    freqs = f0[istart:iend:step]
    m0_ds = m0[istart:iend:step]
    m1_ds = m1[istart:iend:step]
    diff_ds = [a - b for a, b in zip(m1_ds, m0_ds)]

    W, H = 1100, 900
    margin_l, margin_r, margin_t = 90, 30, 70
    sub_h = 220
    sub_gap = 30
    plot_w = W - margin_l - margin_r
    y0 = margin_t
    y1 = y0 + sub_h + sub_gap
    y2 = y1 + sub_h + sub_gap

    ym_min, ym_max = -40, 60
    diff_min, diff_max = -25, 35

    log_fmin, log_fmax = math.log10(fmin), math.log10(fmax)

    def fx(f):
        return margin_l + (math.log10(f) - log_fmin) / (log_fmax - log_fmin) * plot_w

    def my_mag(val, top_y):
        return top_y + sub_h - (val - ym_min) / (ym_max - ym_min) * sub_h

    def my_diff(val, top_y):
        return top_y + sub_h - (val - diff_min) / (diff_max - diff_min) * sub_h

    def polyline_path(values, my_fn, top_y):
        pts = []
        for f, v in zip(freqs, values):
            x = fx(f)
            y = my_fn(v, top_y)
            pts.append(f'{x:.1f},{y:.1f}')
        return 'M' + ' L'.join(pts)

    path_s00000 = polyline_path(m0_ds, my_mag, y0)
    path_s10000 = polyline_path(m1_ds, my_mag, y1)
    path_diff = polyline_path(diff_ds, my_diff, y2)

    freq_grid = [100, 200, 500, 1000, 2000, 5000]
    mag_grid = [-40, -20, 0, 20, 40, 60]
    diff_grid = [-20, -10, 0, 10, 20, 30]

    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        'font-family="Hiragino Sans, Yu Gothic UI, Segoe UI, sans-serif">'
    )
    svg.append('<style>')
    svg.append('  .title  { font-size: 18px; font-weight: 800; fill: #1a1d23; }')
    svg.append('  .sub    { font-size: 12px; fill: #6b7280; }')
    svg.append('  .plot-bg { fill: #fafafa; stroke: #cbd5e1; stroke-width: 1; }')
    svg.append('  .grid    { stroke: #e5e7eb; stroke-width: 0.8; fill: none; }')
    svg.append('  .axis-label { font-size: 11px; fill: #4b5563; }')
    svg.append('  .plot-title { font-size: 13px; font-weight: 700; }')
    svg.append('  .line-s00000 { stroke: #1f3a5f; stroke-width: 1.1; fill: none; opacity: 0.9; }')
    svg.append('  .line-s10000 { stroke: #c2185b; stroke-width: 1.1; fill: none; opacity: 0.9; }')
    svg.append('  .line-diff   { stroke: #2ec4b6; stroke-width: 1.4; fill: none; }')
    svg.append('  .zero-line   { stroke: #1a1d23; stroke-width: 0.8; stroke-dasharray: 2 2; }')
    svg.append('  .annot       { font-size: 11px; font-weight: 700; }')
    svg.append('  .peak-marker { stroke: #ff9f1c; stroke-width: 1.2; fill: #fff8e0; }')
    svg.append('</style>')

    svg.append(f'<text x="{W/2}" y="28" text-anchor="middle" class="title">'
               'FFT diff による特徴抽出 — s00000 (全閉) vs s10000 (窓 a 開)</text>')
    svg.append(f'<text x="{W/2}" y="48" text-anchor="middle" class="sub">'
               '生 FFT を直接学習せず「baseline からの diff」を使うことで雑音床を打ち消し、'
               '窓開閉の音響特徴を顕在化</text>')

    def render_plot_bg(top_y, label, color, y_grid_vals, is_diff=False):
        bottom_y = top_y + sub_h
        svg.append(f'<rect class="plot-bg" x="{margin_l}" y="{top_y}" width="{plot_w}" height="{sub_h}"/>')
        for gv in y_grid_vals:
            if is_diff:
                y = my_diff(gv, top_y)
            else:
                y = my_mag(gv, top_y)
            svg.append(f'<line class="grid" x1="{margin_l}" y1="{y:.1f}" x2="{margin_l+plot_w}" y2="{y:.1f}"/>')
            svg.append(f'<text class="axis-label" x="{margin_l-8}" y="{y+3:.1f}" text-anchor="end">{gv:+d} dB</text>')
        for f in freq_grid:
            x = fx(f)
            svg.append(f'<line class="grid" x1="{x:.1f}" y1="{top_y}" x2="{x:.1f}" y2="{bottom_y}"/>')
        svg.append(f'<text class="plot-title" x="{margin_l+10}" y="{top_y+18}" fill="{color}">{label}</text>')

    render_plot_bg(y0, 's00000 - 全窓・扉閉 (baseline)', '#1f3a5f', mag_grid)
    render_plot_bg(y1, 's10000 - 窓 a のみ開', '#c2185b', mag_grid)
    render_plot_bg(y2, 'Diff = s10000 - s00000   (窓 a 開で何が変化したか)', '#2ec4b6', diff_grid, is_diff=True)

    zy = my_diff(0, y2)
    svg.append(f'<line class="zero-line" x1="{margin_l}" y1="{zy:.1f}" x2="{margin_l+plot_w}" y2="{zy:.1f}"/>')

    for f in freq_grid:
        x = fx(f)
        svg.append(f'<text class="axis-label" x="{x:.1f}" y="{y2+sub_h+18}" text-anchor="middle">{f} Hz</text>')
    svg.append(f'<text class="axis-label" x="{W/2}" y="{y2+sub_h+38}" text-anchor="middle">周波数 (Hz, log scale)</text>')

    svg.append(f'<path class="line-s00000" d="{path_s00000}"/>')
    svg.append(f'<path class="line-s10000" d="{path_s10000}"/>')
    svg.append(f'<path class="line-diff" d="{path_diff}"/>')

    peak_freqs = [(309, +30.05, '309 Hz: +30 dB ピーク (窓 a 開で共振)'),
                  (1212, -20.50, '1212 Hz: -20 dB ディップ'),
                  (2798, -16.41, '2798 Hz: -16 dB')]
    for pf, pv, plabel in peak_freqs:
        x = fx(pf)
        y = my_diff(pv, y2)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="peak-marker"/>')
        label_y = y - 12 if pv > 0 else y + 18
        svg.append(f'<text class="annot" x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle" fill="#9a3412">{plabel}</text>')

    exp_y = y2 + sub_h + 60
    svg.append(f'<text x="{margin_l}" y="{exp_y}" font-size="12" font-weight="700" fill="#1a1d23">なぜ diff を使うのか:</text>')
    svg.append(f'<text x="{margin_l}" y="{exp_y+18}" font-size="11" fill="#4b5563">'
               '・上 2 枚は両方とも SPK + 室内残響 + 環境雑音が混在 → どちらが「窓 a 開」か目視で判別困難</text>')
    svg.append(f'<text x="{margin_l}" y="{exp_y+33}" font-size="11" fill="#4b5563">'
               '・下の diff は両者の共通成分（SPK 周波数特性・室内モード・定常雑音）が打ち消され、'
               '<tspan font-weight="700" fill="#2ec4b6">窓 a 開で発生した変化だけ</tspan>が残る</text>')
    svg.append(f'<text x="{margin_l}" y="{exp_y+48}" font-size="11" fill="#4b5563">'
               '・SNR 改善 +20〜+30 dB、NN は <tspan font-weight="700">「何が変わったか」</tspan>に集中して学習できる</text>')

    svg.append('</svg>')

    out = Path('docs/img/fft_diff_explanation.svg')
    out.write_text('\n'.join(svg), encoding='utf-8')
    print(f'wrote {out} ({len(svg)} lines)')


if __name__ == '__main__':
    main()
