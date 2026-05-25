"""SDK 同梱の libNeutronDriver/Firmware と eIQ neutron-converter のバージョン照合。"""
import re, subprocess
paths = [
    "d:/GitHub/mcuxsdk/mcuxsdk/middleware/eiq/neutron/mcxn/libNeutronDriver.a",
    "d:/GitHub/mcuxsdk/mcuxsdk/middleware/eiq/neutron/mcxn/libNeutronFirmware.a",
]
for path in paths:
    print(f"=== {path} ===")
    data = open(path, "rb").read()
    strs = re.findall(rb"[\x20-\x7e]{6,60}", data)
    keys = (b"version", b"microcode", b"neutron", b"3.80", b"3.1", b"3.2",
            b"3.3", b"3.4", b"3.5", b"converter", b"0X", b"0x")
    matches = sorted({s.decode("ascii") for s in strs
                      if any(k in s.lower() for k in keys)})
    for s in matches[:40]:
        print(f"  {s}")
print()
print("=== converter version ===")
r = subprocess.run(["d:/workspace/eIQ/bin/neutron-converter.exe", "--version"],
                   capture_output=True, text=True)
print(r.stdout.strip())
