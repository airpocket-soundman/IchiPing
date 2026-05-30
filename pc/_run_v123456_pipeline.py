"""v123456 全自動パイプライン: v6 採取完了 → 学習 (cross-baseline + max aug) → 変換 → flash 用 model_data.h 生成。

v12345_50f 系列との違い:
  - captures に full_32_train_v6 を追加
  - --baseline-jitter-dirs で v1..v6 (v5 は v5_part2) の 6 baseline を cross
  - --spike-fix で XL dropout 0.3 + LR warmup+cosine
  - epochs 60 → 80 (データ 6x なので少し延ばす)

採取は別タスク (collector_client.py --plan plans/full_32_train_v6.yaml --run-id full_32_train_v6)。
このスクリプトは captures/full_32_train_v6 が 1600 frame 揃ってから実行する想定。

build + flash は環境依存。失敗したら手動 build + flash + 再 sweep でも OK。
"""
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

WSL_PY = "/tmp/nc_venv/bin/python"
PYOCD = r"C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts/pyocd.exe"
ELF_10  = Path("d:/GitHub/IchiPing/firmware/projects/10_inference/debug/ichiping_10_inference_cm33_core0.elf")

RUN_OUT = Path("runs/neutron_v123456_XL")

# 全 captures (学習データソース)
CAPTURES = [
    "captures/full_32_train_v1",
    "captures/full_32_train_v2",
    "captures/full_32_train_v3",
    "captures/full_32_train_v4",
    "captures/full_32_train_v5",
    "captures/full_32_train_v5_part2",
    "captures/full_32_train_v6",
]

# Cross baseline 採用 6 個 (v5 main は s00000=10 frame で統計弱いので v5_part2 を採用)
BASELINES = [
    "captures/full_32_train_v1",
    "captures/full_32_train_v2",
    "captures/full_32_train_v3",
    "captures/full_32_train_v4",
    "captures/full_32_train_v5_part2",
    "captures/full_32_train_v6",
]


def run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


# ---- pre-check: v6 採取データが揃っているか ----
v6_dir = Path(CAPTURES[-1])
if not v6_dir.exists():
    sys.exit(f"v6 captures dir not found: {v6_dir.resolve()}")
n_wav = sum(1 for _ in v6_dir.rglob("frame_*.wav"))
if n_wav < 1500:  # 1600 期待、ちょっと少なくても進める閾値
    sys.exit(f"v6 captures incomplete: {n_wav} frames found, expected 1600")
print(f"v6 ready: {n_wav} frames in {v6_dir}")

env_train = os.environ.copy()
env_train["PYTHONIOENCODING"] = "utf-8"

# ---- 1. 学習 (v1..v6 + cross baseline 6 + max aug) ----
if (RUN_OUT / "best.pt").exists():
    print("=" * 60); print("[1] TRAINING — best.pt 既に存在、スキップ"); print("=" * 60)
else:
    print("=" * 60); print("[1] TRAINING v123456 (cross baseline + max aug)"); print("=" * 60)
    run(["uv", "run", "python", "-m", "training.train_32cls",
         "--captures", *CAPTURES,
         "--baseline-jitter-dirs", *BASELINES,
         "--ambient-dirs", "captures/full_32_passive_v1",
         "--feature-mode", "noise_diff", "--feature-aug",
         "--spike-fix",
         "--arch", "neutron", "--size", "XL", "--seed", "0",
         "--out", str(RUN_OUT),
         "--epochs", "80", "--batch", "32", "--lr", "1e-3"],
        env=env_train)

# ---- 2. ONNX export (4D for Neutron) ----
print("=" * 60); print("[2] EXPORT ONNX (4D)"); print("=" * 60)
run(["uv", "run", "python", "export_neutron_4d.py",
     "--ckpt", str(RUN_OUT / "best.pt"),
     "--out",  str(RUN_OUT / "deploy4d"),
     "--captures", *CAPTURES,
     "--size", "XL", "--backend", "nxp"],
    env=env_train, check=False)

# ---- 3. PINTO onnx2tf INT8 (NHWC calib) ----
print("=" * 60); print("[3] PINTO INT8 TFLite"); print("=" * 60)
run(["uv", "run", "python", "-c", f"""
import numpy as np
d = np.load('{RUN_OUT / "deploy4d" / "calib.npy"}'.replace(chr(92), '/'))
d_nhwc = np.transpose(d, (0, 2, 3, 1)).astype(np.float32)
np.save('{RUN_OUT / "deploy4d" / "calib_nhwc.npy"}'.replace(chr(92), '/'), d_nhwc)
print('NHWC:', d_nhwc.shape)
"""])
run(["uv", "run", "onnx2tf",
     "-i", str(RUN_OUT / "deploy4d" / "model_fp32_4d.onnx"),
     "-o", str(RUN_OUT / "deploy4d" / "pinto_final"),
     "-oiqt", "-qt", "per-channel",
     "-cind", "spectrum_4d",
     str(RUN_OUT / "deploy4d" / "calib_nhwc.npy"),
     "[0.0]", "[1.0]"], env=env_train)

# ---- 4. SDK_26_03 neutron-converter (WSL) ----
print("=" * 60); print("[4] NEUTRON CONVERTER (SDK_26_03, WSL)"); print("=" * 60)
in_wsl  = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_final" / "model_fp32_4d_full_integer_quant.tflite").replace("\\", "/")
out_wsl = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_neutron_sdk26_03.tflite").replace("\\", "/")
wsl_script = f"""
import neutron_converter_SDK_26_03.neutron_converter as nc
from pathlib import Path
in_p  = Path('{in_wsl}')
out_p = Path('{out_wsl}')
b = nc.convertModel(list(in_p.read_bytes()), 'mcxn94x')
out_p.write_bytes(bytes(b))
print(f'out {{out_p.stat().st_size}} B')
"""
run(["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--",
     "bash", "-c",
     f"LD_LIBRARY_PATH=/tmp/nc_venv/lib/python3.12/site-packages/ortools.libs:/tmp/nc_venv/lib/python3.12/site-packages/ortools/.libs:$LD_LIBRARY_PATH "
     f"{WSL_PY} -c '{wsl_script}'"])

# ---- 5. model_data.h 再生成 ----
print("=" * 60); print("[5] model_data.h"); print("=" * 60)
run(["uv", "run", "python", "-c", f"""
from pathlib import Path
tflite = Path('{(RUN_OUT / "deploy4d" / "pinto_neutron_sdk26_03.tflite")}'.replace(chr(92), '/'))
data = tflite.read_bytes()
hdr = Path('../firmware/projects/10_inference/source/model_data.h')
lines = ['/* Auto-generated from v123456 Neutron TFLite */',
         '#ifndef ICHP_MODEL_DATA_H', '#define ICHP_MODEL_DATA_H',
         '#include <stdint.h>',
         f'#define ICHP_MODEL_DATA_LEN {{len(data)}}',
         'static const uint8_t ichp_model_data[ICHP_MODEL_DATA_LEN] __attribute__((aligned(16))) = {{']
for i in range(0, len(data), 12):
    lines.append('    ' + ', '.join(f'0x{{b:02x}}' for b in data[i:i+12]) + ',')
lines += ['}};', '#endif', '']
hdr.write_text(chr(10).join(lines), encoding='utf-8')
print(f'wrote {{hdr.stat().st_size}} B')
"""])

print("=" * 60)
print("[DONE] training + conversion + model_data.h 完了")
print("次: 10_inference を build + flash → 32 状態 sweep で検証")
print("=" * 60)
