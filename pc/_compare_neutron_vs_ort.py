"""Neutron 変換劣化の純粋測定 (4D path, PC エミュレーション)。

同じ入力 WAV を 3 経路で推論し、argmax の一致率を測る:
  A) PyTorch FP32 (fold_bn 済 deploy wrapper)            学習側のリファレンス
  B) PINTO INT8 TFLite (Neutron 変換前)、tf.lite で実行   "理想 INT8" representation
  C) Neutron-converted TFLite, neutron-runner BitExact   実機と同じ NPU 数値

A→B 差 = INT8 量子化の劣化 (これまで PC eval で ~0.3% と判明)
B→C 差 = Neutron op / scheduler の数値差 ← ★ 今回測りたい本命
"""
from __future__ import annotations
import argparse, json, subprocess, sys, wave
from pathlib import Path
import numpy as np
import torch

sys.path.insert(0, "training")
from dataset import IchiPingDataset, CLASS_ORDER_14, class_of
from features import samples_to_logmag_psd
from model_32cls_neutron import (
    IchiPingV1_32clsNeutron, IchiPingV1_32clsNeutronConfig, idx_to_bits,
)

NEUTRON_RUNNER = Path("d:/workspace/eIQ/bin/neutron-runner.exe")
# 入力 quantization (model_data.h header より、infer 側でも動的に取れる値)
IN_SCALE  = 0.185412
IN_ZP     = 29

def load_wav_int16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16)

def quantize_input(logmag_diff_fp32: np.ndarray) -> np.ndarray:
    """fp32 1024 → int8 1024 (NHWC packing 前)"""
    q = np.round(logmag_diff_fp32 / IN_SCALE).astype(np.int32) + IN_ZP
    return np.clip(q, -128, 127).astype(np.int8)

def state_idx_from_bits(bits) -> int:
    return int(bits[0] + bits[1]*2 + bits[2]*4 + bits[3]*8 + bits[4]*16)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--pinto-int8-tflite", type=Path, required=True,
                    help="PINTO onnx2tf 出力の INT8 TFLite (Neutron 化前、'理想 INT8')")
    ap.add_argument("--neutron-tflite", type=Path, required=True)
    ap.add_argument("--eval-set", type=Path, required=True,
                    help="captures/eval_quiet 等。s00000/frame_*.wav を baseline、その他 s*** から各 N 取って評価")
    ap.add_argument("--n-per-state", type=int, default=3,
                    help="状態あたりの WAV 数 (32 状態 × N で総評価数)")
    ap.add_argument("--work-dir", type=Path, default=Path("runs/compare_paths"))
    args = ap.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = args.work_dir / "neutron_input_bins"
    bin_dir.mkdir(exist_ok=True)

    # 1. baseline (eval set の s00000 平均) を計算
    print("[1] computing baseline from", args.eval_set / "s00000")
    bl_wavs = sorted((args.eval_set / "s00000").glob("frame_*.wav"))[:10]
    bl_logmag = np.mean([samples_to_logmag_psd(load_wav_int16(p).astype(np.float32) / 32768.0)
                         for p in bl_wavs], axis=0)
    print(f"    baseline shape={bl_logmag.shape}, mean={bl_logmag.mean():.2f} dB")

    # 2. PyTorch model 準備
    cfg = IchiPingV1_32clsNeutronConfig(size="XL")
    model = IchiPingV1_32clsNeutron(cfg)
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["state_dict"] if "state_dict" in ck else ck)
    model.eval(); model.fold_bn_inplace()

    # 3. PINTO INT8 TFLite (Neutron 化前) を tf.lite で実行する interpreter
    import tensorflow as tf
    tfl = tf.lite.Interpreter(model_path=str(args.pinto_int8_tflite))
    tfl.allocate_tensors()
    tfl_in  = tfl.get_input_details()[0]
    tfl_out = tfl.get_output_details()[0]
    print(f"[3] TFLite input  q=(scale={tfl_in['quantization'][0]:.6f}, "
          f"zp={tfl_in['quantization'][1]}) shape={list(tfl_in['shape'])}")
    print(f"    TFLite output q=(scale={tfl_out['quantization'][0]:.6f}, "
          f"zp={tfl_out['quantization'][1]}) shape={list(tfl_out['shape'])}")

    # 4. 全 32 状態 × N WAV を集めて推論 (state_dir 命名規約: sABCDE)
    samples = []
    for sd in sorted(args.eval_set.glob("s?????")):
        wavs = sorted(sd.glob("frame_*.wav"))[:args.n_per_state]
        state_str = sd.name           # 例 "s10010"
        bits = [int(c) for c in state_str[1:]]
        true_idx = state_idx_from_bits(bits)
        for p in wavs:
            samples.append((true_idx, state_str, p))
    print(f"[2] {len(samples)} samples ({len(set(s[1] for s in samples))} states)")

    rows = []
    for i, (true_idx, state_str, wav_path) in enumerate(samples):
        x_i16 = load_wav_int16(wav_path)
        x_f32 = x_i16.astype(np.float32) / 32768.0
        logmag = samples_to_logmag_psd(x_f32)
        diff = logmag - bl_logmag
        q_int8 = quantize_input(diff)   # (1024,) int8

        # A) PyTorch FP32 — diff fp32 を入力に
        with torch.no_grad():
            x_t = torch.from_numpy(diff.astype(np.float32))[None, None, None, :]
            pt_logits = model(x_t).squeeze().numpy()
        pt_idx = int(np.argmax(pt_logits))

        # B) PINTO INT8 TFLite — INT8 NHWC (1, 1, 1024, 1) を直接入力
        nhwc = q_int8.reshape(1, 1, 1024, 1)
        tfl.set_tensor(tfl_in["index"], nhwc)
        tfl.invoke()
        ort_logits = tfl.get_tensor(tfl_out["index"]).reshape(-1)   # INT8 (32,)
        ort_idx = int(np.argmax(ort_logits))

        # C) neutron-runner — 同じ NHWC INT8 .bin を投げる
        bin_path = bin_dir / f"in_{i:04d}.bin"
        nhwc.tofile(bin_path)
        out_path = bin_dir / f"out_{i:04d}.bin"
        cmd = [str(NEUTRON_RUNNER),
               "--input", str(args.neutron_tflite),
               "--dataset", str(bin_path),
               "--use_neutron_runtime", "true",
               "--output-results", str(out_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not out_path.exists():
            err = (r.stdout + r.stderr)[-200:]
            print(f"  WARN neutron-runner fail on {i}: {err}")
            nc_idx = -1
        else:
            nc_out = np.fromfile(out_path, dtype=np.int8)
            nc_idx = int(np.argmax(nc_out))

        row = {
            "i": i, "wav": str(wav_path.relative_to(args.eval_set)),
            "truth_idx": true_idx, "truth_state": state_str,
            "pt_idx": pt_idx, "ort_idx": ort_idx, "nc_idx": nc_idx,
            "pt_state": "s" + "".join(str((pt_idx >> k) & 1) for k in range(5)),
            "ort_state": "s" + "".join(str((ort_idx >> k) & 1) for k in range(5)),
            "nc_state": "s" + "".join(str((nc_idx >> k) & 1) for k in range(5)) if nc_idx >= 0 else "FAIL",
            "truth_cls14": class_of(np.asarray(bits := idx_to_bits(true_idx))),
            "pt_cls14":    class_of(np.asarray(idx_to_bits(pt_idx))),
            "ort_cls14":   class_of(np.asarray(idx_to_bits(ort_idx))),
            "nc_cls14":    class_of(np.asarray(idx_to_bits(nc_idx))) if nc_idx >= 0 else "FAIL",
        }
        rows.append(row)
        if i < 5 or i % 10 == 0:
            print(f"  [{i:3d}] truth={state_str}({true_idx:2d}/{row['truth_cls14']}) "
                  f"PT={row['pt_state']}({pt_idx:2d}/{row['pt_cls14']}) "
                  f"ORT={row['ort_state']}({ort_idx:2d}/{row['ort_cls14']}) "
                  f"NC={row['nc_state']}({nc_idx:2d}/{row['nc_cls14']})")

    # 5. 集計
    n = len(rows)
    nc_ok = sum(1 for r in rows if r["nc_idx"] >= 0)
    def acc_32(key): return sum(1 for r in rows if r[key + "_idx"] == r["truth_idx"]) / n
    def acc_14(key): return sum(1 for r in rows if r[key + "_cls14"] == r["truth_cls14"]) / n
    def agreement(k1, k2):
        return sum(1 for r in rows
                   if r[k1 + "_idx"] == r[k2 + "_idx"]
                   and r[k2 + "_idx"] >= 0) / n

    summary = {
        "n_total": n, "n_neutron_ok": nc_ok,
        "acc_32cls": {"pt": acc_32("pt"), "ort": acc_32("ort"), "nc": acc_32("nc")},
        "acc_14cls": {"pt": acc_14("pt"), "ort": acc_14("ort"), "nc": acc_14("nc")},
        "agreement_32cls": {
            "pt_vs_ort": agreement("pt", "ort"),
            "ort_vs_nc": agreement("ort", "nc"),
            "pt_vs_nc":  agreement("pt", "nc"),
        },
    }
    print("\n== SUMMARY ==")
    print(json.dumps(summary, indent=2))
    (args.work_dir / "summary.json").write_text(json.dumps({
        "summary": summary, "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nartifacts: {args.work_dir}")

if __name__ == "__main__":
    main()
