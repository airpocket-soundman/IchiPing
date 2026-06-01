"""v67 モデルの量子化前後で PC 上の精度を比較し、MCU sweep (15.6%) のギャップ原因を切り分ける。

評価対象データ: captures/full_32_train_v8 (v67 にとって完全 held-out)
baseline: v8 自身の s00000 (MCU の BL CALIBRATE → BL LIVE と同等の運用)

各 tflite と PyTorch best.pt を同条件で eval。
精度が PyTorch ≒ ONNX ≒ INT8 tflite ≒ Neutron tflite → 量子化は無罪、MCU sweep のギャップは
データ分布シフト or live calibration 由来。
"""
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "training"))

from training.dataset import IchiPingDataset, class_of
from training.model_32cls_neutron import (
    IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig, idx_to_bits,
)
import torch

V67   = ROOT / "runs" / "neutron_v67_XL"
EVAL  = ROOT / "captures" / "full_32_train_v8"

# baseline = v8 自身の s00000 を使う (MCU の live CALIBRATE と同条件)
ds = IchiPingDataset(
    captures_dirs=[EVAL],
    feature_mode="noise_diff",
    baseline_override_dir=EVAL,
)
print(f"dataset: {len(ds)} examples from {EVAL.name}")


def eval_predictor(predict_fn, label):
    ok32 = 0; ok14 = 0
    for i in range(len(ds)):
        item = ds[i]
        x = item["x"].numpy()   # (1, 1024)
        true_idx = int(item["state_idx"].item())
        pred_idx = int(predict_fn(x))
        if pred_idx == true_idx: ok32 += 1
        bt = np.asarray(idx_to_bits(true_idx))
        bp = np.asarray(idx_to_bits(pred_idx))
        if class_of(bt) == class_of(bp): ok14 += 1
    n = len(ds)
    print(f"  {label:<35s} 32cls={ok32}/{n} ({ok32/n*100:.1f}%)   14cls={ok14}/{n} ({ok14/n*100:.1f}%)")


# ---- 1. PyTorch ----
print("\n=== PyTorch best.pt ===")
cfg = IchiPingV1_32clsNeutronConfig(size="XL")
model = IchiPingV1_32clsNeutron(cfg)
state = torch.load(V67 / "best.pt", map_location="cpu", weights_only=False)
model.load_state_dict(state["state_dict"] if "state_dict" in state else state)
model.eval()


@torch.no_grad()
def pred_pytorch(x):
    x_t = torch.from_numpy(x).unsqueeze(0).unsqueeze(0)  # (1, 1, 1, 1024)
    out = model(x_t)
    return int(out.argmax(dim=-1).item())


eval_predictor(pred_pytorch, "PyTorch FP32 best.pt")

# ---- 2. ONNX FP32 (4D) ----
print("\n=== ONNX FP32 4D ===")
try:
    import onnxruntime as ort
    sess = ort.InferenceSession(
        str(V67 / "deploy4d" / "model_fp32_4d.onnx"),
        providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    def pred_onnx_fp32(x):
        x_t = x.reshape(1, 1, 1024, 1).astype(np.float32)
        out = sess.run(None, {in_name: x_t})[0]
        return int(np.argmax(out.flatten()))

    eval_predictor(pred_onnx_fp32, "ONNX FP32 (4D)")
except Exception as e:
    print(f"  ONNX FP32 skip: {e}")

# ---- 3. PINTO INT8 tflite (full_integer_quant) ----
print("\n=== PINTO INT8 tflite ===")
try:
    import tensorflow as tf
    int8_p = V67 / "deploy4d" / "pinto_final" / "model_fp32_4d_full_integer_quant.tflite"
    interpreter = tf.lite.Interpreter(model_path=str(int8_p))
    interpreter.allocate_tensors()
    in_det  = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    def pred_pinto_int8(x):
        # NHWC INT8: scale, zp で量子化
        scale, zp = in_det["quantization"]
        x_nhwc = x.reshape(1, 1, 1024, 1)
        x_q = np.clip(np.round(x_nhwc / scale + zp), -128, 127).astype(np.int8)
        interpreter.set_tensor(in_det["index"], x_q)
        interpreter.invoke()
        out = interpreter.get_tensor(out_det["index"])
        return int(np.argmax(out.flatten()))

    eval_predictor(pred_pinto_int8, "PINTO INT8 tflite")
except Exception as e:
    print(f"  PINTO INT8 skip: {e}")

# ---- 4. NXP INT8 + Windows Neutron tflite (8/9 = 89% NPU) ----
print("\n=== NXP path Neutron tflite ===")
try:
    nxp_p = V67 / "deploy4d" / "model_int8_nxp_neutron.tflite"
    interpreter = tf.lite.Interpreter(model_path=str(nxp_p))
    interpreter.allocate_tensors()
    in_det  = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    def pred_nxp_neutron(x):
        scale, zp = in_det["quantization"]
        x_nhwc = x.reshape(1, 1, 1024, 1)
        x_q = np.clip(np.round(x_nhwc / scale + zp), -128, 127).astype(np.int8)
        interpreter.set_tensor(in_det["index"], x_q)
        interpreter.invoke()
        out = interpreter.get_tensor(out_det["index"])
        return int(np.argmax(out.flatten()))

    eval_predictor(pred_nxp_neutron, "NXP onnx2quant + win Neutron")
except Exception as e:
    print(f"  NXP Neutron skip: {e}")

# ---- 5. WSL SDK_26_03 Neutron tflite (7/7 = 100% NPU, deployed 版) ----
print("\n=== WSL SDK_26_03 Neutron tflite (firmware と同じ) ===")
try:
    wsl_p = V67 / "deploy4d" / "pinto_neutron_sdk26_03.tflite"
    interpreter = tf.lite.Interpreter(model_path=str(wsl_p))
    interpreter.allocate_tensors()
    in_det  = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    def pred_wsl_neutron(x):
        scale, zp = in_det["quantization"]
        x_nhwc = x.reshape(1, 1, 1024, 1)
        x_q = np.clip(np.round(x_nhwc / scale + zp), -128, 127).astype(np.int8)
        interpreter.set_tensor(in_det["index"], x_q)
        interpreter.invoke()
        out = interpreter.get_tensor(out_det["index"])
        return int(np.argmax(out.flatten()))

    eval_predictor(pred_wsl_neutron, "WSL SDK_26_03 Neutron (deployed)")
except Exception as e:
    print(f"  WSL Neutron skip: {e}")

print()
print("=" * 60)
print("解釈:")
print("  - PyTorch ≒ ONNX = ベースライン精度")
print("  - PINTO INT8, NXP Neutron, WSL Neutron が PyTorch から大きく離れる → 量子化問題")
print("  - 全部似た精度 → 量子化無罪、MCU sweep のギャップは run-time 環境")
print("=" * 60)
