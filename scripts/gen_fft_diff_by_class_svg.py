"""Generate docs/img/fft_diff_by_class.svg from full_32_v1 captures.

Reads each state's FFT CSV, computes diff from s00000 baseline, groups by
14 equivalence classes, and renders class-mean diff curves as SVG.
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


def state_str(idx):
    bits = [(idx >> k) & 1 for k in range(5)]
    return 's' + ''.join(str(b) for b in bits)


def class_of(idx):
    """Return (class_name, color) for 14 equivalence classes."""
    bits = [(idx >> k) & 1 for k in range(5)]
    a, b, c, AB, BC = bits
    if AB == 0:
        return ('A2', '#c2185b') if a == 1 else ('A1', '#1f3a5f')
    elif BC == 0:
        idx_in_class = a + b * 2
        return (
            f'B{idx_in_class + 1}',
            ['#0a8754', '#ff9f1c', '#6ea8fe', '#c678dd'][idx_in_class]
        )
    else:
        idx_in_class = a + b * 2 + c * 4
        return (
            f'C{idx_in_class + 1}',
            ['#2ec4b6', '#ffb454', '#ff7ab6', '#6c5ce7',
             '#7be0a4', '#e89a3d', '#9b59b6', '#3498db'][idx_in_class]
        )


def main():
    root = Path('.')
    baseline_path = root / 'pc/captures/full_32_v1/analysis/s00000/fft.csv'
    freqs, baseline = load_fft(baseline_path)

    # Collect diffs per class
    class_diffs = {}
    class_colors = {}
    for idx in range(32):
        state = state_str(idx)
        if state == 's00000':
            continue
        csv_path = root / f'pc/captures/full_32_v1/analysis/{state}/fft.csv'
        if not csv_path.exists():
            continue
        _, mags = load_fft(csv_path)
        diff = [m - b for m, b in zip(mags, baseline)]
        cname, color = class_of(idx)
        class_diffs.setdefault(cname, []).append(diff)
        class_colors[cname] = color

    # Mean diff per class
    class_mean_diff = {}
    for cname, diffs in class_diffs.items():
        n = len(diffs)
        mean_d = [sum(diffs[i][j] for i in range(n)) / n for j in range(len(diffs[0]))]
        class_mean_diff[cname] = mean_d

    class_order = ['A1', 'A2', 'B1', 'B2', 'B3', 'B4',
                   'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8']
    class_order = [c for c in class_order if c in class_mean_diff]

    # Plot setup
    fmin, fmax = 100.0, 6000.0
    istart = bisect.bisect_left(freqs, fmin)
    iend = bisect.bisect_left(freqs, fmax)
    n_target = 500
    step = max(1, (iend - istart) // n_target)
    freqs_ds = freqs[istart:iend:step]

    W, H = 1100, 740
    margin_l, margin_r, margin_t, margin_b = 90, 200, 70, 80
    plot_w = W - margin_l - margin_r
    plot_h = H - margin_t - margin_b
    top_y = margin_t

    diff_min, diff_max = -30, 35

    log_fmin, log_fmax = math.log10(fmin), math.log10(fmax)

    def fx(f):
        return margin_l + (math.log10(f) - log_fmin) / (log_fmax - log_fmin) * plot_w

    def fy(v):
        return top_y + plot_h - (v - diff_min) / (diff_max - diff_min) * plot_h

    def polyline(values, freq_list):
        pts = []
        for f, v in zip(freq_list, values):
            pts.append(f'{fx(f):.1f},{fy(v):.1f}')
        return 'M' + ' L'.join(pts)

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
    svg.append('  .axis-title { font-size: 12px; fill: #1a1d23; font-weight: 700; }')
    svg.append('  .zero-line  { stroke: #1a1d23; stroke-width: 0.8; stroke-dasharray: 2 2; }')
    svg.append('  .legend-text{ font-size: 11px; fill: #1a1d23; }')
    svg.append('  .legend-title { font-size: 12px; font-weight: 700; fill: #1a1d23; }')
    svg.append('</style>')

    svg.append(f'<text x="{W/2}" y="28" text-anchor="middle" class="title">'
               'FFT diff (state - s00000) クラス別平均</text>')
    svg.append(f'<text x="{W/2}" y="48" text-anchor="middle" class="sub">'
               '14 観測等価クラスごとに FFT diff の形状が違う → NN がこの形を学習して状態を識別</text>')

    svg.append(f'<rect class="plot-bg" x="{margin_l}" y="{top_y}" width="{plot_w}" height="{plot_h}"/>')

    for gv in [-30, -20, -10, 0, 10, 20, 30]:
        y = fy(gv)
        svg.append(f'<line class="grid" x1="{margin_l}" y1="{y:.1f}" x2="{margin_l+plot_w}" y2="{y:.1f}"/>')
        svg.append(f'<text class="axis-label" x="{margin_l-8}" y="{y+3:.1f}" text-anchor="end">{gv:+d} dB</text>')

    freq_grid = [100, 200, 500, 1000, 2000, 5000]
    for f in freq_grid:
        x = fx(f)
        svg.append(f'<line class="grid" x1="{x:.1f}" y1="{top_y}" x2="{x:.1f}" y2="{top_y+plot_h}"/>')
        svg.append(f'<text class="axis-label" x="{x:.1f}" y="{top_y+plot_h+18}" text-anchor="middle">{f} Hz</text>')

    svg.append(f'<text class="axis-title" x="{W/2}" y="{top_y+plot_h+40}" text-anchor="middle">周波数 (Hz, log scale)</text>')
    svg.append(f'<text class="axis-title" x="{margin_l-50}" y="{top_y+plot_h/2}" text-anchor="middle" '
               f'transform="rotate(-90, {margin_l-50}, {top_y+plot_h/2})">FFT diff (dB)</text>')

    zy = fy(0)
    svg.append(f'<line class="zero-line" x1="{margin_l}" y1="{zy:.1f}" x2="{margin_l+plot_w}" y2="{zy:.1f}"/>')

    for cname in class_order:
        diff = class_mean_diff[cname]
        diff_ds = diff[istart:iend:step]
        path = polyline(diff_ds, freqs_ds)
        color = class_colors[cname]
        sw = '2.0' if cname in ['A1', 'A2'] else '1.4'
        svg.append(f'<path d="{path}" stroke="{color}" stroke-width="{sw}" fill="none" opacity="0.85"/>')

    legend_x = W - margin_r + 20
    legend_y_top = top_y + 10
    svg.append(f'<text class="legend-title" x="{legend_x}" y="{legend_y_top}">等価クラス</text>')

    groups = [('A (扉 AB 閉)', ['A1', 'A2']),
              ('B (AB 開, BC 閉)', ['B1', 'B2', 'B3', 'B4']),
              ('C (AB 開, BC 開)', ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8'])]

    ly = legend_y_top + 22
    for gtitle, gclasses in groups:
        svg.append(f'<text class="legend-title" x="{legend_x}" y="{ly}" font-size="11">{gtitle}</text>')
        ly += 18
        for cname in gclasses:
            if cname not in class_mean_diff:
                continue
            color = class_colors[cname]
            svg.append(f'<line x1="{legend_x}" y1="{ly-3}" x2="{legend_x+24}" y2="{ly-3}" '
                       f'stroke="{color}" stroke-width="2.4"/>')
            n_members = len(class_diffs[cname])
            svg.append(f'<text class="legend-text" x="{legend_x+30}" y="{ly}">{cname} (n={n_members})</text>')
            ly += 18
        ly += 6

    exp_y = H - 32
    svg.append(f'<text x="{margin_l}" y="{exp_y}" font-size="11" fill="#4b5563">'
               'A1 (全閉) は plus minus 0 dB 近傍に張り付く / A2 (窓 a 開) は低域 200-400 Hz で大きな +diff</text>')
    svg.append(f'<text x="{margin_l}" y="{exp_y+16}" font-size="11" fill="#4b5563">'
               '扉 AB / BC を開くと中高域 (1-3 kHz) で隣室モードが結合し独自パターンが現れる → NN がこの形状差から状態を判定</text>')

    svg.append('</svg>')

    out = Path('docs/img/fft_diff_by_class.svg')
    out.write_text('\n'.join(svg), encoding='utf-8')
    print(f'wrote {out} ({len(svg)} lines)')
    print(f'classes plotted: {class_order}')


if __name__ == '__main__':
    main()
