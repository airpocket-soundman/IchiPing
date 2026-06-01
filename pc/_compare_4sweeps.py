"""4 sweep ログを横並びにして state x モデル/音量 の比較表を作る."""
import re
from pathlib import Path

TASKS = Path('C:/Users/yamas/AppData/Local/Temp/claude/d--GitHub-IchiPing/008e4e19-d8ab-46d6-92fb-cdaece78f3ef/tasks')
RUNS = [
    ('A: v678910 strong / vol30', 'bl73r2jdn.output'),
    ('B: v678910 strong / vol50', 'bn5pfla4c.output'),
    ('C: v6_11 strong+norm / vol30', 'b88slblaf.output'),
    ('D: v6_11 strong+norm / vol50', 'buzd5ifci.output'),
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

header = '{:>2} {:6}'.format('st', 'truth')
for label, _ in RUNS:
    header += ' | {:^28}'.format(label[:28])
print(header)
print('-' * len(header))

for sid in range(32):
    truth = 's' + ''.join(str((sid >> k) & 1) for k in range(5))
    row = '{:>2} {:6}'.format(sid, truth)
    for label, _ in RUNS:
        if sid not in results[label]:
            row += ' | {:28}'.format('')
            continue
        p_idx, ok32, c14, ok14, mg = results[label][sid]
        p_state = 's' + ''.join(str((p_idx >> k) & 1) for k in range(5))
        mark = 'O' if ok32 == 'OK' else 'X'
        cell = '{}({}) {:3} m={:5.1f}'.format(p_state, mark, c14, mg)
        row += ' | {:^28}'.format(cell)
    print(row)

print('-' * len(header))
for label, _ in RUNS:
    ok32 = sum(1 for v in results[label].values() if v[1] == 'OK')
    ok14 = sum(1 for v in results[label].values() if v[3] == 'OK')
    mg_avg = sum(v[4] for v in results[label].values()) / len(results[label])
    print('{:32s}: 32cls={}/32 ({:.1f}%)  14cls={}/32 ({:.1f}%)  margin avg={:.1f}'.format(
        label[:32], ok32, ok32/32*100, ok14, ok14/32*100, mg_avg))
