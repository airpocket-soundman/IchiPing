"""Quick: compute per-state RMS_dB and max|delta_dB| distance from s00000
baseline for captures/full_32_passive_v1/. Output sorted from largest to
smallest distance — used as input to the doc page summary table."""
import csv
from pathlib import Path
import numpy as np

root = Path('captures/full_32_passive_v1/analysis')
states = sorted(d.name for d in root.iterdir() if d.is_dir() and d.name.startswith('s') and len(d.name) == 6)


def load(s):
    f = root / s / 'fft.csv'
    fr = []
    db = []
    with f.open() as fh:
        r = csv.reader(fh)
        next(r)
        for row in r:
            fr.append(float(row[0]))
            db.append(float(row[1]))
    return np.array(fr), np.array(db)


freqs, base = load('s00000')
mask = (freqs >= 300) & (freqs <= 6000)

rows = []
for s in states:
    _, db = load(s)
    diff = db[mask] - base[mask]
    rms = float(np.sqrt(np.mean(diff ** 2)))
    mx  = float(np.max(np.abs(diff)))
    rows.append((s, rms, mx))

rows.sort(key=lambda r: -r[1])

print('state    rms_db  max|d|')
for s, r, m in rows:
    print(f'{s}   {r:6.2f}  {m:6.1f}')
