"""v6-v12 + 強化 augmentation + per-frame 正規化 (noise_diff_norm) 全自動パイプライン。

v6_11 strong-aug norm に v12 (vol=5 採取) を追加し、cross-baseline 7 種で学習。
v11 (高ノイズ環境) + v12 (高音量) を含むことで、推論時の音量・ノイズ多様性に
対するさらなる頑健化を狙う。

データ: v6-v12 7 セッション、cross-baseline 7 種、strong aug、noise_diff_norm。
firmware 側 ichp_features_normalize_frame() を有効化した build とペア必須。
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

RUN_OUT = Path("runs/neutron_v6_12_strongaug_norm_XL")

CAPTURES = [
    "captures/full_32_train_v6",
    "captures/full_32_train_v7",
    "captures/full_32_train_v8",
    "captures/full_32_train_v9",
    "captures/full_32_train_v10",
    "captures/full_32_train_v11",
    "captures/full_32_train_v12",
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
    # メイン 32 state のみカウント (_*/ 隠しバックアップは除外)
    n = sum(1 for sd in d.iterdir()
              if sd.is_dir() and sd.name.startswith("s") and len(sd.name) == 6
              for _ in sd.glob("frame_*.wav"))
    print(f"{cap}: {n} frames")
    if n < 1500: sys.exit(f"{cap} incomplete")

env_train = os.environ.copy()
env_train["PYTHONIOENCODING"] = "utf-8"

# ---- 1. 学習 ----
if (RUN_OUT / "best.pt").exists():
    print("=" * 60); print("[1] TRAINING — best.pt 既に存在、スキップ"); print("=" * 60)
else:
    print("=" * 60); print("[1] TRAINING v6-12 STRONG AUG + NORM"); print("=" * 60)
    run(["uv", "run", "python", "-m", "training.train_32cls",
         "--captures", *CAPTURES,
         "--baseline-jitter-dirs", *BASELINES,
         "--ambient-dirs", "captures/full_32_passive_v1",
         "--feature-mode", "noise_diff_norm",
         "--feature-aug",
         "--spike-fix",
         "--aug-strong",
         "--arch", "neutron", "--size", "XL", "--seed", "0",
         "--out", str(RUN_OUT),
         "--epochs", "80", "--batch", "32", "--lr", "1e-3"],
        env=env_train)

# ---- 2. ONNX export ----
print("=" * 60); print("[2] EXPORT ONNX (4D) + NXP path tflite"); print("=" * 60)
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

# ---- 4. WSL SDK_26_03 Neutron converter ----
print("=" * 60); print("[4] WSL SDK_26_03 Neutron converter"); print("=" * 60)
in_wsl  = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_final" / "model_fp32_4d_full_integer_quant.tflite").replace("\\", "/")
out_wsl = "/mnt/d/GitHub/IchiPing/pc/" + str(RUN_OUT / "deploy4d" / "pinto_neutron_sdk26_03.tflite").replace("\\", "/")
py_tmpfile = RUN_OUT / "deploy4d" / "_wsl_convert.py"
py_tmpfile.write_text(
    f'import neutron_converter_SDK_26_03.neutron_converter as nc\n'
    f'from pathlib import Path\n'
    f'in_p  = Path("{in_wsl}")\n'
    f'out_p = Path("{out_wsl}")\n'
    f'b = nc.convertModel(list(in_p.read_bytes()), "mcxn94x")\n'
    f'out_p.write_bytes(bytes(b))\n'
    f'print("out", out_p.stat().st_size, "B")\n',
    encoding="utf-8")
py_tmp_wsl = "/mnt/d/GitHub/IchiPing/pc/" + str(py_tmpfile).replace("\\", "/")
run(["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--",
     "bash", "-c",
     "source /opt/nc_venv/bin/activate && "
     "LD_LIBRARY_PATH=/opt/nc_venv/lib/python3.12/site-packages/ortools.libs:/opt/nc_venv/lib/python3.12/site-packages/ortools/.libs:$LD_LIBRARY_PATH "
     f"python {py_tmp_wsl}"])

print("=" * 60)
print("[DONE] v6-12 強化 aug + 正規化学習 + WSL Neutron 変換完了")
print(f"       deployable: {RUN_OUT}/deploy4d/pinto_neutron_sdk26_03.tflite")
print("=" * 60)
