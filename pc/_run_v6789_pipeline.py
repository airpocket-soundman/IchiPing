"""v6+v7+v8+v9 全自動パイプライン: 学習 → ONNX → PINTO INT8 → WSL SDK_26_03 Neutron → model_data.h (記録のみ、flash しない)。

新ハード時代 4 セッション分 (v6, v7, v8, v9) で学習。
- cross-baseline 4 種 (各 captures 自身)
- max augmentation (waveform + feature-aug + ambient)
- spike-fix (XL dropout 0.3 + LR warmup+cosine)
- 80 epochs, Neutron XL

採取済前提: pc/captures/full_32_train_v{6,7,8,9} (各 1600 frame)。

最終ステップで WSL SDK_26_03 経由の Neutron 変換 (7/7 = 100% NPU) と
model_data.h 候補を生成するが flash は行わない (ユーザ判断で別途)。
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

RUN_OUT = Path("runs/neutron_v6789_XL")

CAPTURES = [
    "captures/full_32_train_v6",
    "captures/full_32_train_v7",
    "captures/full_32_train_v8",
    "captures/full_32_train_v9",
]
BASELINES = CAPTURES[:]


def run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


for cap in CAPTURES:
    d = Path(cap)
    if not d.exists():
        sys.exit(f"not found: {d.resolve()}")
    n = sum(1 for _ in d.rglob("frame_*.wav"))
    print(f"{cap}: {n} frames")
    if n < 1500: sys.exit(f"{cap} incomplete")

env_train = os.environ.copy()
env_train["PYTHONIOENCODING"] = "utf-8"

# ---- 1. 学習 ----
if (RUN_OUT / "best.pt").exists():
    print("=" * 60); print("[1] TRAINING — best.pt 既に存在、スキップ"); print("=" * 60)
else:
    print("=" * 60); print("[1] TRAINING v6789"); print("=" * 60)
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

# ---- 2. ONNX export + NXP path tflite (副産物) ----
print("=" * 60); print("[2] EXPORT ONNX (4D) + NXP path tflite (副産物)"); print("=" * 60)
run(["uv", "run", "python", "export_neutron_4d.py",
     "--ckpt", str(RUN_OUT / "best.pt"),
     "--out",  str(RUN_OUT / "deploy4d"),
     "--captures", *CAPTURES,
     "--size", "XL", "--backend", "nxp"],
    env=env_train, check=False)

# ---- 3. PINTO onnx2tf INT8 ----
print("=" * 60); print("[3] PINTO INT8 TFLite"); print("=" * 60)
import numpy as np
d = np.load(RUN_OUT / "deploy4d" / "calib.npy")
d_nhwc = np.transpose(d, (0, 2, 3, 1)).astype(np.float32)
np.save(RUN_OUT / "deploy4d" / "calib_nhwc.npy", d_nhwc)
print(f"NHWC calib saved: {d_nhwc.shape}")

run(["uv", "run", "onnx2tf",
     "-i", str(RUN_OUT / "deploy4d" / "model_fp32_4d.onnx"),
     "-o", str(RUN_OUT / "deploy4d" / "pinto_final"),
     "-oiqt", "-qt", "per-channel",
     "-cind", "spectrum_4d",
     str(RUN_OUT / "deploy4d" / "calib_nhwc.npy"),
     "[0.0]", "[1.0]"], env=env_train)

# ---- 4. WSL SDK_26_03 Neutron converter (7/7 = 100% NPU, firmware 互換版) ----
print("=" * 60); print("[4] WSL SDK_26_03 Neutron converter"); print("=" * 60)
in_wsl  = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_final" / "model_fp32_4d_full_integer_quant.tflite").replace("\\", "/")
out_wsl = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_neutron_sdk26_03.tflite").replace("\\", "/")
wsl_py_script = f"""
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
     f"source /opt/nc_venv/bin/activate && "
     f"LD_LIBRARY_PATH=/opt/nc_venv/lib/python3.12/site-packages/ortools.libs:/opt/nc_venv/lib/python3.12/site-packages/ortools/.libs:$LD_LIBRARY_PATH "
     f"python -c '{wsl_py_script}'"])

print("=" * 60)
print("[DONE] 学習 + WSL Neutron 変換完了")
print(f"       deployable: {RUN_OUT}/deploy4d/pinto_neutron_sdk26_03.tflite")
print(f"       firmware に焼くにはユーザ判断で別途 model_data.h 生成 + build + flash")
print("=" * 60)
