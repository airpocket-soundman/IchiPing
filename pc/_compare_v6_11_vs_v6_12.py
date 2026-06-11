"""今セッション内 (同条件) v6-v11 vs v6-v12 の 4 sweep を横並びで比較."""
import re
from pathlib import Path

TASKS = Path('C:/Users/yamas/AppData/Local/Temp/claude/d--GitHub-IchiPing/008e4e19-d8ab-46d6-92fb-cdaece78f3ef/tasks')
RUNS = [
    ('v6-v11 vol30', 'b66lw79hu.output'),
    ('v6-v11 vol50', 'bjn551f94.output'),
    ('v6-v12 vol30', 'bo91saqd5.output'),
    ('v6-v12 vol50', 'bwzo3gag2.output'),
]
RE = re.compile(
    r'STATE\s+(\d+)\s+truth=(s\d{5})\s+->\s+cls32=(\d+)\(([A-Za-z]+)\)\s+'
    r'cls14=(\S+)\(([A-Za-z]+)\)\s+dist=\[(.*?)\]\s+avg_margin=([\d.-]+)'
)

results = {}
for label, fn in RUNS:
    p = TASKS / fn
    results[label] = {}
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        m = RE.match(line)
        if m:
            sid = int(m.group(1))
            results[label][sid] = (int(m.group(3)), m.group(4),
                                   m.group(5), m.group(6),
                                   float(m.group(8)))

# header
hdr = '{:>2} {:6}'.format('st', 'truth')
for label, _ in RUNS:
    hdr += ' | {:^24}'.format(label)
print(hdr)
print('-' * len(hdr))

for sid in range(32):
    truth = 's' + ''.join(str((sid >> k) & 1) for k in range(5))
    row = '{:>2} {:6}'.format(sid, truth)
    for label, _ in RUNS:
        if sid not in results[label]:
            row += ' | {:24}'.format('')
            continue
        p_idx, ok32, c14, ok14, mg = results[label][sid]
        p_state = 's' + ''.join(str((p_idx >> k) & 1) for k in range(5))
        mark = 'O' if ok32 == 'OK' else 'X'
        cell = '{}({}) {:3} m={:4.1f}'.format(p_state, mark, c14, mg)
        row += ' | {:^24}'.format(cell)
    print(row)

print('-' * len(hdr))
for label, _ in RUNS:
    ok32 = sum(1 for v in results[label].values() if v[1] == 'OK')
    ok14 = sum(1 for v in results[label].values() if v[3] == 'OK')
    mg = sum(v[4] for v in results[label].values()) / len(results[label])
    print('{:32s}: 32cls={}/32 ({:.1f}%)  14cls={}/32 ({:.1f}%)  margin avg={:.1f}'.format(
        label, ok32, ok32/32*100, ok14, ok14/32*100, mg))
