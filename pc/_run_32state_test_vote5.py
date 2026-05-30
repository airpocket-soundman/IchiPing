"""現環境で BL CALIBRATE → BL LIVE → 32 状態を各 5 回推論して多数決。

各 state ごとに INFER STREAM 5 を投げて、得られた 5 個の cls32_idx を集計、最頻値を採用。
タイブレーク: 同点の場合は平均 margin が大きい方。
"""
import re, serial, time
from collections import Counter

NAMES = ['a', 'b', 'c', 'AB', 'BC']

# RESULT line parser (inference_client.py と同じ)
RESULT_RE = re.compile(
    r"RESULT\s+seq=(?P<seq>\d+)\s+"
    r"cls32_idx=(?P<sidx>\d+)\s+cls32_state=(?P<state>s\d{5})\s+"
    r"cls14=(?P<cls14>\S+)\s+"
    r"second32_idx=\d+\s+second32_state=s\d{5}\s+baseline=\S+\s+"
    r"argmax_q=(?P<aq>-?\d+)\s+second_q=(?P<sq>-?\d+)\s+margin=(?P<mg>-?\d+)"
)


def cmd(ser, verb, wait=2.0):
    ser.reset_input_buffer()
    ser.write((verb + '\r\n').encode())
    end = time.time() + wait
    r = bytearray()
    while time.time() < end:
        chunk = ser.read(1024)
        if chunk: r.extend(chunk)
    return r.decode('utf-8', errors='replace')


def cmd_until(ser, verb, end_pred, max_wait=15.0):
    """end_pred(line)->True で打ち切る. 取れた全 line を返す."""
    ser.reset_input_buffer()
    ser.write((verb + '\r\n').encode())
    deadline = time.time() + max_wait
    buf = bytearray()
    lines = []
    while time.time() < deadline:
        chunk = ser.read(1024)
        if chunk:
            buf.extend(chunk)
            while b'\n' in buf:
                idx = buf.index(b'\n')
                line = buf[:idx].decode('utf-8', errors='replace').rstrip()
                buf = buf[idx+1:]
                lines.append(line)
                if end_pred(line):
                    return lines
    return lines


def vote(samples):
    """samples = [(cls32_idx, margin), ...] 5 個. 多数決+margin tiebreak."""
    if not samples:
        return None, []
    counts = Counter(s[0] for s in samples)
    top = counts.most_common()
    max_n = top[0][1]
    tied = [idx for idx, n in top if n == max_n]
    if len(tied) == 1:
        return tied[0], samples
    # tie-break by avg margin
    best_idx, best_m = tied[0], -1e9
    for idx in tied:
        avg_m = sum(s[1] for s in samples if s[0] == idx) / max(sum(1 for s in samples if s[0] == idx), 1)
        if avg_m > best_m:
            best_m, best_idx = avg_m, idx
    return best_idx, samples


def main():
    ser = serial.Serial('COM3', 921600, timeout=0.3)
    ser.reset_input_buffer()

    print('WAIT_BOOT (up to 10s)...', flush=True)
    end = time.time() + 10.0
    boot_buf = bytearray()
    saw_ready = False
    while time.time() < end:
        chunk = ser.read(512)
        if chunk:
            boot_buf.extend(chunk)
            if b'IchiPing 10_inference ready' in boot_buf:
                saw_ready = True; break
    if not saw_ready:
        print('NO_BOOT_BANNER - probing PING', flush=True)
        cmd(ser, 'PING', wait=2.0)
    else:
        print('BOOT_READY', flush=True); time.sleep(0.3); ser.reset_input_buffer()

    cmd(ser, 'PAT CLEAR')
    cmd(ser, 'PAT NOISE w_2000 2000 30 0')
    cmd(ser, 'PAT SELECT 0')
    info = cmd(ser, 'PAT INFO')
    if 'count=1' not in info:
        print(f'PAT_PUSH_FAIL: {info!r}'); ser.close(); return

    print('SETUP CLOSE ALL', flush=True)
    cmd(ser, 'CLOSE ALL', wait=8.0)

    print('CALIBRATE: 10 frames at all-closed (~25s)', flush=True)
    out = cmd(ser, 'BL CALIBRATE 10', wait=30.0)
    for line in out.splitlines():
        if line.startswith('OK BL calibrated') or line.startswith('ERR BL'):
            print(f'CALIB_RESULT {line}', flush=True)

    out = cmd(ser, 'BL LIVE', wait=2.0)
    for line in out.splitlines():
        if line.startswith('OK BL') or line.startswith('ERR'):
            print(f'BL_LIVE {line}', flush=True)

    print('[BEGIN 32-state vote5 sweep]', flush=True)
    current = [0, 0, 0, 0, 0]
    correct32 = 0; correct14 = 0
    rows = []
    for state_idx in range(32):
        bits = [(state_idx >> k) & 1 for k in range(5)]
        state_str = 's' + ''.join(str(b) for b in bits)
        # 14cls computation for truth (PC 側)
        a, b, c, ab, bc = bits
        if ab == 0:
            truth14 = "A1" if a == 0 else "A2"
        elif bc == 0:
            truth14 = {(0,0):"B1",(1,0):"B2",(0,1):"B3",(1,1):"B4"}[(a,b)]
        else:
            truth14 = "C" + str(1 + a + 2*b + 4*c)

        for i in range(5):
            if current[i] != bits[i]:
                verb = 'OPEN' if bits[i] == 1 else 'CLOSE'
                cmd(ser, f'{verb} {NAMES[i]}', wait=2.0)
        current = bits[:]

        # INFER STREAM 5 → 5 個の RESULT を集めて多数決
        lines = cmd_until(
            ser, 'INFER STREAM 5',
            end_pred=lambda L: L.startswith('OK INFER done') or L.startswith('OK INFER aborted'),
            max_wait=25.0,
        )
        samples = []  # [(cls32_idx, margin, cls14), ...]
        for line in lines:
            m = RESULT_RE.match(line)
            if m:
                samples.append((int(m.group('sidx')), int(m.group('mg')), m.group('cls14')))

        if len(samples) < 3:
            print(f'STATE {state_idx:2d} truth={state_str} ONLY_{len(samples)}_SAMPLES', flush=True)
            continue

        # cls32 多数決
        counts32 = Counter(s[0] for s in samples)
        top32 = counts32.most_common()
        max_n = top32[0][1]
        tied = [idx for idx, n in top32 if n == max_n]
        if len(tied) == 1:
            pred32 = tied[0]
        else:
            best_idx, best_m = tied[0], -1e9
            for idx in tied:
                avg_m = sum(s[1] for s in samples if s[0] == idx) / max(sum(1 for s in samples if s[0] == idx), 1)
                if avg_m > best_m:
                    best_m, best_idx = avg_m, idx
            pred32 = best_idx

        # cls14 多数決
        counts14 = Counter(s[2] for s in samples)
        pred14 = counts14.most_common(1)[0][0]

        ok32 = "OK" if pred32 == state_idx else "ng"
        ok14 = "OK" if pred14 == truth14 else "ng"
        if pred32 == state_idx: correct32 += 1
        if pred14 == truth14: correct14 += 1

        # show count distribution
        c32_str = ", ".join(f"{idx}:{n}" for idx, n in top32)
        avg_m = sum(s[1] for s in samples) / len(samples)
        print(f'STATE {state_idx:2d} truth={state_str} -> '
              f'cls32={pred32}({ok32}) cls14={pred14}({ok14}) '
              f'dist=[{c32_str}] avg_margin={avg_m:.1f}', flush=True)

        rows.append((state_idx, state_str, truth14, pred32, pred14, ok32, ok14))

    print(f'\n[SUMMARY] 32cls exact: {correct32}/32 ({correct32/32*100:.1f}%)', flush=True)
    print(f'[SUMMARY] 14cls exact: {correct14}/32 ({correct14/32*100:.1f}%)', flush=True)
    print('[DONE]', flush=True)
    ser.close()


if __name__ == '__main__':
    main()
