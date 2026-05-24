"""v12345_50f を factory baseline で 32 state sweep (校正無し)。"""
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

    # boot 待ち
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
        print('BOOT_READY', flush=True)
        time.sleep(0.3); ser.reset_input_buffer()

    cmd(ser, 'PAT CLEAR')
    cmd(ser, 'PAT NOISE w_2000 2000 30 0')
    cmd(ser, 'PAT SELECT 0')

    # factory mode 明示 (起動直後の default だが念のため)
    out = cmd(ser, 'BL FACTORY')
    print(f'BL: {out.strip()}', flush=True)
    out = cmd(ser, 'BL STATUS')
    print(f'STATUS: {out.strip()}', flush=True)

    # CLOSE ALL (初期化)
    print('SETUP CLOSE ALL', flush=True)
    cmd(ser, 'CLOSE ALL', wait=8.0)
    current = [0, 0, 0, 0, 0]

    print('[BEGIN 32-state sweep with FACTORY baseline]', flush=True)
    for state_idx in range(32):
        bits = [(state_idx >> k) & 1 for k in range(5)]
        state_str = 's' + ''.join(str(b) for b in bits)
        for i in range(5):
            if current[i] != bits[i]:
                verb = 'OPEN' if bits[i] == 1 else 'CLOSE'
                cmd(ser, f'{verb} {NAMES[i]}', wait=2.0)
        current = bits[:]
        out = cmd(ser, 'INFER', wait=5.0)
        result = ''
        for line in out.splitlines():
            if line.startswith('RESULT'):
                result = line; break
        if not result:
            print(f'STATE {state_idx:2d} truth={state_str} NO_RESULT', flush=True)
        else:
            print(f'STATE {state_idx:2d} truth={state_str} -> {result}', flush=True)
    print('[DONE]', flush=True)
    ser.close()

if __name__ == '__main__':
    main()
