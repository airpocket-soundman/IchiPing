import serial, time
s = serial.Serial()
s.port='COM3'; s.baudrate=921600; s.timeout=1.0
s.dtr=False; s.rts=False
print('opening...'); 
s.open()
print('opened. drain...')
time.sleep(0.2); s.reset_input_buffer()
print('write PING...')
s.write(b'PING\r\n'); s.flush()
print('read...')
for i in range(10):
    line = s.readline()
    if not line: print(f'[{i}] (empty)'); break
    print(f'[{i}]', line)
s.close()
print('done')
