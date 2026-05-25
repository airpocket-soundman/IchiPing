"""採取完了 → 学習 → 変換 → flash → MCU sweep → PC eval → report 全自動。

採取自体は別タスク。これは採取完了 (=v5_part2 dir に 1280 frame 揃った) を確認してから
順に実行する想定。
"""
import csv, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

WSL_PY = "/tmp/nc_venv/bin/python"
PYOCD = r"C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts/pyocd.exe"
NEUTRON_RUNNER = Path("d:/workspace/eIQ/bin/neutron-runner.exe")
ELF_10  = Path("d:/GitHub/IchiPing/firmware/projects/10_inference/debug/ichiping_10_inference_cm33_core0.elf")
ELF_09  = Path("d:/GitHub/IchiPing/firmware/projects/09_collector/debug/ichiping_09_collector_cm33_core0.elf")
SWEEPS  = Path("runs/v12345_compare_report/sweeps")

def run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)

# ---- 1. 学習 (v1+v2+v3+v4+v5+v5_part2 + ambient) ----
env_train = os.environ.copy()
env_train["PYTHONIOENCODING"] = "utf-8"
if Path("runs/neutron_v12345_50f_32cls_ambient_XL/best.pt").exists():
    print("=" * 60); print("[1] TRAINING — best.pt 既に存在、スキップ"); print("=" * 60)
else:
    print("=" * 60); print("[1] TRAINING v12345_50f"); print("=" * 60)
    run(["uv", "run", "python", "-m", "training.train_32cls",
         "--captures", "captures/full_32_train_v1", "captures/full_32_train_v2",
                       "captures/full_32_train_v3", "captures/full_32_train_v4",
                       "captures/full_32_train_v5", "captures/full_32_train_v5_part2",
         "--ambient-dirs", "captures/full_32_passive_v1",
         "--feature-mode", "noise_diff", "--feature-aug",
         "--arch", "neutron", "--size", "XL", "--seed", "0",
         "--out", "runs/neutron_v12345_50f_32cls_ambient_XL",
         "--epochs", "60", "--batch", "32", "--lr", "1e-3"],
        env=env_train)

# ---- 2. ONNX export + NXP calib npy 生成 ----
print("=" * 60); print("[2] EXPORT ONNX (4D)"); print("=" * 60)
run(["uv", "run", "python", "export_neutron_4d.py",
     "--ckpt", "runs/neutron_v12345_50f_32cls_ambient_XL/best.pt",
     "--out",  "runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d",
     "--captures", "captures/full_32_train_v1", "captures/full_32_train_v2",
                   "captures/full_32_train_v3", "captures/full_32_train_v4",
                   "captures/full_32_train_v5", "captures/full_32_train_v5_part2",
     "--size", "XL", "--backend", "nxp"],
    env=env_train, check=False)

# ---- 3. NHWC calib + PINTO onnx2tf INT8 ----
print("=" * 60); print("[3] PINTO INT8 TFLite"); print("=" * 60)
run(["uv", "run", "python", "-c", """
import numpy as np
d = np.load('runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/calib.npy')
d_nhwc = np.transpose(d, (0, 2, 3, 1)).astype(np.float32)
np.save('runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/calib_nhwc.npy', d_nhwc)
print('NHWC:', d_nhwc.shape)
"""])
run(["uv", "run", "onnx2tf",
     "-i", "runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/model_fp32_4d.onnx",
     "-o", "runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_final",
     "-oiqt", "-qt", "per-channel",
     "-cind", "spectrum_4d",
     "runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/calib_nhwc.npy",
     "[0.0]", "[1.0]"], env=env_train)

# ---- 4. SDK_26_03 neutron-converter (WSL) ----
print("=" * 60); print("[4] NEUTRON CONVERTER (SDK_26_03, WSL)"); print("=" * 60)
wsl_script = """
import neutron_converter_SDK_26_03.neutron_converter as nc
from pathlib import Path
in_p  = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_final/model_fp32_4d_full_integer_quant.tflite")
out_p = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_neutron_sdk26_03.tflite")
b = nc.convertModel(list(in_p.read_bytes()), "mcxn94x")
out_p.write_bytes(bytes(b))
print(f"out {out_p.stat().st_size} B")
"""
run(["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--",
     "bash", "-c",
     f"LD_LIBRARY_PATH=/tmp/nc_venv/lib/python3.12/site-packages/ortools.libs:/tmp/nc_venv/lib/python3.12/site-packages/ortools/.libs:$LD_LIBRARY_PATH "
     f"{WSL_PY} -c '{wsl_script}'"])

# ---- 5. model_data.h 再生成 ----
print("=" * 60); print("[5] model_data.h"); print("=" * 60)
run(["uv", "run", "python", "-c", """
from pathlib import Path
tflite = Path('runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_neutron_sdk26_03.tflite')
data = tflite.read_bytes()
hdr = Path('../firmware/projects/10_inference/source/model_data.h')
lines = ['/* Auto-generated from v12345_50f Neutron TFLite */',
         '#ifndef ICHP_MODEL_DATA_H', '#define ICHP_MODEL_DATA_H',
         '#include <stdint.h>',
         f'#define ICHP_MODEL_DATA_LEN {len(data)}',
         'static const uint8_t ichp_model_data[ICHP_MODEL_DATA_LEN] __attribute__((aligned(16))) = {']
for i in range(0, len(data), 12):
    lines.append('    ' + ', '.join(f'0x{b:02x}' for b in data[i:i+12]) + ',')
lines += ['};', '#endif', '']
hdr.write_text('\\n'.join(lines), encoding='utf-8')
print(f'wrote {hdr.stat().st_size} B')
"""])

# ---- 6. firmware build + flash 10_inference ----
print("=" * 60); print("[6] BUILD + FLASH 10_inference"); print("=" * 60)
build_env = os.environ.copy()
build_env.update({
    "SdkRootDirPath": "d:/GitHub",
    "ARMGCC_DIR": "C:/Users/yamas/.mcuxpressotools/arm-gnu-toolchain-14.2.rel1-mingw-w64-x86_64-arm-none-eabi",
    "MCUX_VENV_PATH": "C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts",
    "POSTPROCESS_UTILITY": "C:/Users/yamas/.mcuxpressotools/mcux-fixelf-14.2.2/mcux-fixelf.exe",
    "PATH": "C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts" + os.pathsep + os.environ["PATH"],
    "PYTHONIOENCODING": "utf-8",
})
run(["C:/Program Files/CMake/bin/cmake.EXE", "--build",
     "d:/GitHub/IchiPing/firmware/projects/10_inference/debug",
     "--target", "all"], env=build_env)
run([PYOCD, "flash", "--target", "mcxn947vdf", str(ELF_10)], env=build_env)
run([PYOCD, "reset", "--target", "mcxn947vdf"])

# ---- 7. PC eval (現環境 v5 + v5_part2 を新モデルで eval) ----
print("=" * 60); print("[7] PC EVAL on current-env captures"); print("=" * 60)
# 自己 baseline 路線 (= MCU LIVE 相当)
run(["uv", "run", "python", "-c", """
import sys, json
from pathlib import Path
import numpy as np
import torch
sys.path.insert(0, 'training')
from dataset import Incoming  # placeholder, real import下
""" .replace("import Incoming  # placeholder, real import下", "import IchiPingDataset, class_of") + """
from model_32cls_neutron import IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig, idx_to_bits

EVAL_DIR = Path('captures/full_32_train_v5')  # 自己 baseline 用に s00000 必要、part2 も加える
CKPT = Path('runs/neutron_v12345_50f_32cls_ambient_XL/best.pt')

cfg = IchiPingV1_32clsNeutronConfig(size='XL')
m = IchiPingV1_32clsNeutron(cfg)
ck = torch.load(CKPT, map_location='cpu', weights_only=False)
m.load_state_dict(ck['state_dict'] if 'state_dict' in ck else ck)
m.eval()
ds = IchiPingDataset(captures_dirs=[EVAL_DIR, Path('captures/full_32_train_v5_part2')],
                     feature_mode='noise_diff', baseline_override_dir=EVAL_DIR)
ok32 = ok14 = 0
with torch.no_grad():
    for i in range(len(ds)):
        it = ds[i]
        x = it['x'].numpy()
        true32 = int(it['state_idx'].item())
        true14 = class_of(np.asarray(idx_to_bits(true32)))
        x_t = torch.from_numpy(x).unsqueeze(0).unsqueeze(0)
        pred32 = int(np.argmax(m(x_t).squeeze().numpy()))
        pred14 = class_of(np.asarray(idx_to_bits(pred32)))
        if pred32 == true32: ok32 += 1
        if pred14 == true14: ok14 += 1
n = len(ds)
res = {'n': n, 'acc_32': ok32/n, 'acc_14': ok14/n}
print(json.dumps(res, indent=2))
Path('runs/neutron_v12345_50f_32cls_ambient_XL/pc_eval_self_bl.json').write_text(json.dumps(res, indent=2))
"""], env=env_train)

print("=" * 60)
print("[DONE] pipeline complete — next: run MCU 32-state sweep manually and regenerate report")
print("=" * 60)
