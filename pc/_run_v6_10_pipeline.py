"""v6-v10 全自動パイプライン: v7..v10 採取完了 → 学習 (新ハード時代だけ) → 変換。

v123456 系列との違い:
  - v1-v5 を捨てる (ブレッドボード→ユニバーサル基板で特徴量が変わったため)
  - v6, v7, v8, v9, v10 = 5 ソースで cross-baseline
  - その他 (max aug + spike-fix + Neutron XL) は同じ

採取は別タスク (各 v に対して collector_client.py --plan plans/full_32_train_vN.yaml)。
このスクリプトは captures/full_32_train_v{6..10} が全部 1500+ frame 揃ってから実行する想定。
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

RUN_OUT = Path("runs/neutron_v6_10_XL")

CAPTURES = [
    "captures/full_32_train_v6",
    "captures/full_32_train_v7",
    "captures/full_32_train_v8",
    "captures/full_32_train_v9",
    "captures/full_32_train_v10",
]

# baseline-jitter は全 5 個を cross
BASELINES = CAPTURES[:]


def run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


# pre-check
for cap in CAPTURES:
    d = Path(cap)
    if not d.exists():
        sys.exit(f"not found: {d.resolve()}")
    n = sum(1 for _ in d.rglob("frame_*.wav"))
    if n < 1500:
        sys.exit(f"{cap} incomplete: {n} frames, expected 1600")
    print(f"{cap}: {n} frames OK")

env_train = os.environ.copy()
env_train["PYTHONIOENCODING"] = "utf-8"

# ---- 1. 学習 ----
if (RUN_OUT / "best.pt").exists():
    print("=" * 60); print("[1] TRAINING — best.pt 既に存在、スキップ"); print("=" * 60)
else:
    print("=" * 60); print("[1] TRAINING v6_10 (cross baseline + max aug)"); print("=" * 60)
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
run(["uv", "run", "python", "-c",
     f"import numpy as np; d = np.load('{RUN_OUT}/deploy4d/calib.npy'); "
     f"d_nhwc = np.transpose(d, (0, 2, 3, 1)).astype(np.float32); "
     f"np.save('{RUN_OUT}/deploy4d/calib_nhwc.npy', d_nhwc); "
     f"print('NHWC:', d_nhwc.shape)".replace("\\", "/")])
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

print("=" * 60)
print("[DONE] 学習 + 2 種類の Neutron tflite 完了:")
print(f"  NXP path : {RUN_OUT}/deploy4d/model_int8_nxp_neutron.tflite")
print(f"  PINTO+win: {RUN_OUT}/deploy4d/pinto_neutron_winconv.tflite")
print("=" * 60)
