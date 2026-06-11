import serial, time
s = serial.Serial()
s.port='COM3'; s.baudrate=921600; s.timeout=2.0
s.dtr=False; s.rts=False
s.open()
time.sleep(0.5); s.reset_input_buffer()
s.write(b'PING\r\n'); s.flush()
print('PING:', s.readline())
print('CLOSE ALL...')
s.write(b'CLOSE ALL\r\n'); s.flush()
import time as t
t0 = t.time()
while t.time() - t0 < 20:
    line = s.readline()
    if not line:
        print('[timeout]')
        break
    print(f'[{t.time()-t0:6.2f}s]', line)
    if b'OK CLOSE all' in line: break
s.close()
