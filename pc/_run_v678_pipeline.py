"""v6+v7+v8 全自動パイプライン: 学習 → ONNX → PINTO INT8 → winconv Neutron → model_data.h。

新ハード時代 3 セッション分 (v6, v7, v8) で学習。
- cross-baseline 3 種
- max augmentation (waveform + feature-aug + ambient)
- spike-fix (XL dropout 0.3 + LR warmup+cosine)
- 80 epochs, Neutron XL

採取済前提: pc/captures/full_32_train_v{6,7,8} (各 1600 frame)。
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

RUN_OUT = Path("runs/neutron_v678_XL")

CAPTURES = ["captures/full_32_train_v6",
            "captures/full_32_train_v7",
            "captures/full_32_train_v8"]
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
    print("=" * 60); print("[1] TRAINING v678"); print("=" * 60)
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

# ---- 2. ONNX export + NXP Neutron tflite ----
print("=" * 60); print("[2] EXPORT ONNX (4D) + NXP Neutron tflite"); print("=" * 60)
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

# ---- 4. PINTO INT8 + Windows neutron-converter ----
print("=" * 60); print("[4] Windows neutron-converter on PINTO INT8"); print("=" * 60)
run(["d:/workspace/eIQ/bin/neutron-converter.exe",
     "--input",  str(RUN_OUT / "deploy4d" / "pinto_final" / "model_fp32_4d_full_integer_quant.tflite"),
     "--output", str(RUN_OUT / "deploy4d" / "pinto_neutron_winconv.tflite"),
     "--target", "mcxn94x"])

# ---- 5. model_data.h は生成しない (sweep が走ってる間は触らない) ----
print("=" * 60)
print("[DONE] 学習 + 変換完了。")
print(f"       winconv tflite: {RUN_OUT}/deploy4d/pinto_neutron_winconv.tflite")
print("       model_data.h は sweep 終了後にユーザ判断で書き換え。")
print("=" * 60)
