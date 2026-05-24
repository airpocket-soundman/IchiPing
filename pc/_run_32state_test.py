"""現環境で BL CALIBRATE → BL LIVE → 32 状態 sweep。

CLOSE ALL 状態 (全閉 = noise だけが鳴る環境) で baseline を取り直してから
32 状態を回す。前回 (factory baseline) との比較用。
"""
import serial, sys, time

NAMES = ['a', 'b', 'c', 'AB', 'BC']

def cmd(ser, verb, wait=2.0):
    ser.reset_input_buffer()
    ser.write((verb + '\r\n').encode())
    end = time.time() + wait
    r = bytearray()
    while time.time() < end:
        chunk = ser.read(1024)
        if chunk: r.extend(chunk)
    return r.decode('utf-8', errors='replace')

def main():
    ser = serial.Serial('COM3', 921600, timeout=0.3)
    ser.reset_input_buffer()

    # MCU の boot バナーを最大 10 秒待ってから patterns push。
    # 受信バッファに boot メッセージが溜まってる間にコマンド送ると
    # MCU は boot 中で取りこぼす → PAT NOISE が消えて INFER が no_pattern で
    # 失敗する。'INFO IchiPing 10_inference ready' を見たら main loop 起動済。
    print('WAIT_BOOT (up to 10s)...', flush=True)
    end = time.time() + 10.0
    boot_buf = bytearray()
    saw_ready = False
    while time.time() < end:
        chunk = ser.read(512)
        if chunk:
            boot_buf.extend(chunk)
            if b'IchiPing 10_inference ready' in boot_buf:
                saw_ready = True
                break
    if not saw_ready:
        print('NO_BOOT_BANNER - probably already running, probing PING', flush=True)
        cmd(ser, 'PING', wait=2.0)
    else:
        print('BOOT_READY seen, sleeping 0.3s for stragglers', flush=True)
        time.sleep(0.3)
        ser.reset_input_buffer()

    # patterns push + 確認
    cmd(ser, 'PAT CLEAR')
    cmd(ser, 'PAT NOISE w_2000 2000 30 0')
    cmd(ser, 'PAT SELECT 0')
    info = cmd(ser, 'PAT INFO')
    if 'count=1' not in info:
        print(f'PAT_PUSH_FAIL: {info!r}', flush=True)
        ser.close(); return

    # 全閉 → baseline 校正 → live 切替
    print('SETUP CLOSE ALL (initial)', flush=True)
    cmd(ser, 'CLOSE ALL', wait=8.0)

    print('CALIBRATE: capturing 10 frames at all-closed (silent ~25s)', flush=True)
    out = cmd(ser, 'BL CALIBRATE 10', wait=30.0)
    # final OK BL calibrated 行だけ抜き出して報告
    for line in out.splitlines():
        if line.startswith('OK BL calibrated') or line.startswith('ERR BL'):
            print(f'CALIB_RESULT {line}', flush=True)

    print('BL LIVE switch', flush=True)
    out = cmd(ser, 'BL LIVE', wait=2.0)
    for line in out.splitlines():
        if line.startswith('OK BL') or line.startswith('ERR'):
            print(f'BL_LIVE {line}', flush=True)

    print('[BEGIN 32-state sweep with LIVE baseline]', flush=True)
    current = [0, 0, 0, 0, 0]   # 物理状態 = CLOSE ALL 直後
    for state_idx in range(32):
        bits = [(state_idx >> k) & 1 for k in range(5)]
        state_str = 's' + ''.join(str(b) for b in bits)

        changes = []
        for i in range(5):
            if current[i] != bits[i]:
                verb = 'OPEN' if bits[i] == 1 else 'CLOSE'
                changes.append((NAMES[i], verb))

        for name, verb in changes:
            cmd(ser, f'{verb} {name}', wait=2.0)
        current = bits[:]

        out = cmd(ser, 'INFER', wait=5.0)
        result_line = ''
        for line in out.splitlines():
            if line.startswith('RESULT'):
                result_line = line
                break
        if not result_line:
            print(f'STATE {state_idx:2d} truth={state_str} NO_RESULT', flush=True)
        else:
            print(f'STATE {state_idx:2d} truth={state_str} -> {result_line}', flush=True)

    print('[DONE 32-state sweep]', flush=True)
    ser.close()

if __name__ == '__main__':
    main()
