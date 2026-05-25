"""現環境 captures を PyTorch FP32 + PINTO INT8 TFLite で eval。MCU 結果と比較。"""
import json, sys
from pathlib import Path
import numpy as np
import torch

sys.path.insert(0, "training")
from dataset import IchiPingDataset, CLASS_ORDER_14, class_of
from model_32cls_neutron import (
    IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig, idx_to_bits,
)

CAPTURES = Path("captures/live_room_check_2026-05-24")
CKPT     = Path("runs/neutron_v1234_32cls_ambient_XL/best.pt")
PINTO    = Path("runs/neutron_v1234_32cls_ambient_XL/deploy4d/pinto_final/model_fp32_4d_full_integer_quant.tflite")
OUT      = Path("runs/eval_live_room")
OUT.mkdir(parents=True, exist_ok=True)

# PyTorch FP32 model
cfg = IchiPingV1_32clsNeutronConfig(size="XL")
model = IchiPingV1_32clsNeutron(cfg)
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
model.load_state_dict(ck["state_dict"] if "state_dict" in ck else ck)
model.eval()

# PINTO INT8 TFLite (Neutron 化前)
import tensorflow as tf
tfl = tf.lite.Interpreter(model_path=str(PINTO))
tfl.allocate_tensors()
in_d = tfl.get_input_details()[0]
out_d = tfl.get_output_details()[0]
in_scale, in_zp = in_d["quantization"]

# baseline = 自己 s00000 平均 (MCU LIVE と同条件)
ds = IchiPingDataset(captures_dirs=[CAPTURES],
                     feature_mode="noise_diff",
                     baseline_override_dir=CAPTURES)
print(f"loaded {len(ds)} samples, baseline=self s00000")

correct_pt_32 = 0; correct_pt_14 = 0
correct_tf_32 = 0; correct_tf_14 = 0
rows = []
with torch.no_grad():
    for i in range(len(ds)):
        item = ds[i]
        x = item["x"].numpy()           # (1, 1024)
        true32 = int(item["state_idx"].item())
        true_state = "s" + "".join(str(b) for b in idx_to_bits(true32))
        true_cls14 = class_of(np.asarray(idx_to_bits(true32)))

        # PyTorch FP32
        x_t = torch.from_numpy(x).unsqueeze(0).unsqueeze(0)   # (1,1,1,1024)
        logits = model(x_t).squeeze().numpy()
        pt32 = int(np.argmax(logits))
        pt_cls14 = class_of(np.asarray(idx_to_bits(pt32)))
        if pt32 == true32:        correct_pt_32 += 1
        if pt_cls14 == true_cls14: correct_pt_14 += 1

        # PINTO INT8 TFLite (NHWC int8 NHWC (1,1,1024,1))
        x_nhwc = x[None, None, :, :].transpose(0, 2, 3, 1).astype(np.float32)
        q = np.round(x_nhwc / in_scale).astype(np.int32) + int(in_zp)
        q = np.clip(q, -128, 127).astype(in_d["dtype"])
        tfl.set_tensor(in_d["index"], q)
        tfl.invoke()
        out8 = tfl.get_tensor(out_d["index"]).reshape(-1)
        tf32 = int(np.argmax(out8))
        tf_cls14 = class_of(np.asarray(idx_to_bits(tf32)))
        if tf32 == true32:        correct_tf_32 += 1
        if tf_cls14 == true_cls14: correct_tf_14 += 1

        rows.append({
            "i": i, "true_state": true_state, "true_idx": true32, "true_cls14": true_cls14,
            "pt_state": "s" + "".join(str(b) for b in idx_to_bits(pt32)),
            "pt_idx": pt32, "pt_cls14": pt_cls14,
            "tf_state": "s" + "".join(str(b) for b in idx_to_bits(tf32)),
            "tf_idx": tf32, "tf_cls14": tf_cls14,
        })

n = len(ds)
summary = {
    "n": n,
    "pytorch_fp32": {"acc_32cls": correct_pt_32/n, "acc_14cls": correct_pt_14/n},
    "pinto_int8":   {"acc_32cls": correct_tf_32/n, "acc_14cls": correct_tf_14/n},
}
print("\n== PC eval on current-environment 96 frames ==")
print(json.dumps(summary, indent=2))
(OUT / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows},
                                              indent=2, ensure_ascii=False),
                                  encoding="utf-8")
print(f"\nsaved {OUT/'summary.json'}")
