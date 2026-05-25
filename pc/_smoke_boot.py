"""Boot バナー + 全コマンド一括 smoke test。

reset 前にシリアルを開いておくのが boot 取りこぼし防止のコツ。
"""
import serial, time, subprocess

# 1. シリアル先開け
ser = serial.Serial("COM3", 921600, timeout=0.3)
ser.reset_input_buffer()

# 2. pyocd reset で MCU 再起動
print("=== resetting MCU ===")
subprocess.run([
    r"C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts/pyocd.exe",
    "reset", "--target", "mcxn947vdf"
], check=True, capture_output=True)
print("(reset issued)")

# 3. 8 秒 boot を収集
end = time.time() + 8.0
buf = bytearray()
while time.time() < end:
    chunk = ser.read(2048)
    if chunk:
        buf.extend(chunk)
print("=== boot output ===")
print(buf.decode("utf-8", errors="replace"))

# 4. PING / GET CONFIG / BL STATUS を順に試す
def cmd(verb, wait=1.5):
    print(f"\n>>> {verb}")
    ser.reset_input_buffer()
    ser.write((verb + "\r\n").encode())
    end = time.time() + wait
    resp = bytearray()
    while time.time() < end:
        chunk = ser.read(512)
        if chunk:
            resp.extend(chunk)
    print(resp.decode("utf-8", errors="replace"))

cmd("PING")
cmd("GET CONFIG", wait=2.0)
cmd("BL STATUS")
cmd("PAT INFO")

ser.close()
