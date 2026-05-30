# IchiPing — モデル学習 → MCXN947 へのデプロイ パイプライン

このリポで「新しい学習データから NPU 100% 推論を実機に焼く」ための **再現可能な手順**。
2026-05-30 に WSL 環境を作り直して 100% NPU を復元した記録を元にしている。

## なぜ専用ドキュメントが要るか

deployable な Neutron tflite の出力は、**converter のバージョンに firmware の Neutron driver が縛られる**。
firmware に同梱されている `mcuxsdk` middleware の `NeutronDriver` / `Register_NEUTRON_GRAPH()` は
`neutron_converter_SDK_26_03` (Linux Python pkg, v1.0.0) が出す microcode 形式と ABI 整合している。

| converter | 生成 microcode | mcuxsdk firmware で実行 |
|---|---|---|
| `neutron_converter_SDK_26_03` 1.0.0 (Linux, WSL) | SDK_26_03 形式、7/7 = **100% NPU** | ✅ 動作 |
| `neutron-converter.exe` 3.1.1 (Windows native) | MLIR フロー、8/9 = 89% NPU (PAD が CPU 落ち) | ❌ `NeutronGraph` で hang |

したがって **本番は WSL + SDK_26_03 一択**。Windows の `neutron-converter.exe` は op 数の見積もり等で参考になるが、
deploy 用 tflite には使わない。

## 全体フロー

```
PyTorch best.pt
   │ training/export_neutron_4d.py
   ▼
ONNX FP32 (4D shape, Reshape-free)
   │ onnx2tf --qt per-channel (Calib 200 frame NHWC)
   ▼
PINTO INT8 TFLite (model_fp32_4d_full_integer_quant.tflite)
   │ WSL: nc.convertModel(..., "mcxn94x")
   ▼
pinto_neutron_sdk26_03.tflite  ← deployable, 7/7 = 100% NPU
   │ Python: xxd 風に C 配列化
   ▼
firmware/projects/10_inference/source/model_data.h
   │ cmake build + pyocd flash
   ▼
MCXN947 Neutron NPU で 1.89 ms 推論
```

## WSL 環境セットアップ (`/opt/nc_venv`)

過去は `/tmp/nc_venv` だったが、WSL 再起動で `/tmp` がワイプされる事故が 2026-05-30 に発生。
本番は **`/opt/nc_venv` に置いて永続化**する。

```bash
# Ubuntu-24.04 WSL を root で開いて実行
sudo apt-get install -y python3-venv python3-pip
python3 -m venv /opt/nc_venv
source /opt/nc_venv/bin/activate

pip install --upgrade pip

# ortools は libabsl 2407 を含むバージョンに固定。新しい版だと
# libabsl_log_internal_check_op.so のシンボルが食い違って Neutron 起動不能。
pip install 'ortools==9.12.4544' numpy

# NXP eIQ repo は **public**、認証不要 (2026-05-30 時点で確認)
pip install --extra-index-url https://eiq.nxp.com/repository/ neutron-converter-SDK-26-03

# 動作確認
LD_LIBRARY_PATH=/opt/nc_venv/lib/python3.12/site-packages/ortools.libs:/opt/nc_venv/lib/python3.12/site-packages/ortools/.libs:$LD_LIBRARY_PATH \
python -c "import neutron_converter_SDK_26_03.neutron_converter as nc; print('OK', dir(nc))"
```

ポイント:
- `ortools<9.13` でないと libabsl 2505 系で hash 不一致になる (試行錯誤の末 9.12.4544 が動く最古確認版)
- `LD_LIBRARY_PATH` を 2 つの `ortools.libs` パスに通すのが必須 (`/opt/nc_venv/.../ortools.libs` と `/opt/nc_venv/.../ortools/.libs` の両方)
- NXP repo `https://eiq.nxp.com/repository/` は public — Username / Password プロンプトは出ない

## 変換コマンド (Python)

```python
# WSL 側 Python から呼ぶ
import neutron_converter_SDK_26_03.neutron_converter as nc
from pathlib import Path

in_p  = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_<run>_XL/deploy4d/pinto_final/model_fp32_4d_full_integer_quant.tflite")
out_p = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_<run>_XL/deploy4d/pinto_neutron_sdk26_03.tflite")
b = nc.convertModel(list(in_p.read_bytes()), "mcxn94x")
out_p.write_bytes(bytes(b))
```

正常出力例 (今回の v67/v678/v123456 全てで):
```
Number of operators converted     = 7
Number of operators NOT converted = 0
Operator conversion ratio         = 7 / 7 = 1
Total size = 120,144 (bytes) (All)
```

## 既存スクリプト

| script | 入力 | 出力 |
|---|---|---|
| [pc/training/train_32cls.py](../pc/training/train_32cls.py) | captures dir × N | `runs/<name>/best.pt` |
| [pc/export_neutron_4d.py](../pc/export_neutron_4d.py) | best.pt | ONNX 4D + NXP INT8 tflite + Win Neutron tflite |
| [pc/_run_v67_pipeline.py](../pc/_run_v67_pipeline.py) | v6+v7 captures | (旧) winconv 経路 |
| [pc/_run_v678_pipeline.py](../pc/_run_v678_pipeline.py) | v6+v7+v8 captures | (旧) winconv 経路 |

旧スクリプトは Windows `neutron-converter.exe` で止まる構成のままなので、**WSL SDK_26_03 変換ステップを別途実行する**必要がある。
近々一本化予定 (今は `wsl -d Ubuntu-24.04 -u root -- bash <<'WSL' … WSL` で手動補完)。

## model_data.h 生成

```python
from pathlib import Path
tflite = Path('runs/neutron_v678_XL/deploy4d/pinto_neutron_sdk26_03.tflite')
data = tflite.read_bytes()
hdr = Path('../firmware/projects/10_inference/source/model_data.h')
lines = [
    '/* <run-name>: <captures>, <baselines>, <epochs>ep, WSL SDK_26_03 = 7/7 100% NPU */',
    '#ifndef ICHP_MODEL_DATA_H', '#define ICHP_MODEL_DATA_H',
    '#include <stdint.h>',
    f'#define ICHP_MODEL_DATA_LEN {len(data)}',
    'static const uint8_t ichp_model_data[ICHP_MODEL_DATA_LEN] __attribute__((aligned(16))) = {',
]
for i in range(0, len(data), 12):
    lines.append('    ' + ', '.join(f'0x{b:02x}' for b in data[i:i+12]) + ',')
lines += ['};', '#endif', '']
hdr.write_text(chr(10).join(lines), encoding='utf-8')
```

## firmware build + flash

```bash
# Windows shell から
export SdkRootDirPath="d:/GitHub" \
       ARMGCC_DIR="C:/Users/yamas/.mcuxpressotools/arm-gnu-toolchain-14.2.rel1-mingw-w64-x86_64-arm-none-eabi" \
       MCUX_VENV_PATH="C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts" \
       POSTPROCESS_UTILITY="C:/Users/yamas/.mcuxpressotools/mcux-fixelf-14.2.2/mcux-fixelf.exe" \
       PATH="C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts:$PATH"

"/c/Program Files/CMake/bin/cmake.EXE" --build \
  "d:/GitHub/IchiPing/firmware/projects/10_inference/debug" --target all

"C:/Users/yamas/.mcuxpressotools/.mcux-venv-3.12/Scripts/pyocd.exe" flash --target mcxn947vdf \
  "d:/GitHub/IchiPing/firmware/projects/10_inference/debug/ichiping_10_inference_cm33_core0.elf"
```

flash 後は **USB ケーブル抜き挿し**でボード再起動 (`pyocd reset` は MCXN947 で不安定)。

## トラブルシュート

### `Invalid AP address (#0)`
flash 中に MCU が異常状態に入って SWD ロック。**ISP リカバリ手順**:
1. USB を抜く
2. ISP ボタンを押したまま USB を挿す
3. ISP を離す → ROM bootloader モード
4. `pyocd flash` で復旧

### sweep で `STATE N ONLY_0_SAMPLES`
INFER が無応答 = TFLite Invoke でハング。原因候補:
- model_data.h が WSL SDK_26_03 経由じゃない (`pinto_neutron_winconv.tflite` 等を使った)
- mcuxsdk が更新されて Neutron driver ABI が変わった

### sweep で `BL: 空 STATUS: 空`
firmware 自体が起動してない。typically model parse 失敗 or arena 不足。

### `ImportError: libabsl_log_internal_check_op.so.2407.0.0`
ortools が新しすぎる。9.12.4544 に下げる。

## 関連ドキュメント

- [docs/mcu_deployment.html](mcu_deployment.html) — INT8 量子化と Neutron 変換の理論的背景
- [docs/v12345_report.html](v12345_report.html) — 過去デプロイモデルの検証レポート
- [docs/nn_design.html](nn_design.html) — Neutron 互換 32-class Conv2D アーキ
- [pc/runs/v1_6_fftdiff/index.html](../pc/runs/v1_6_fftdiff/index.html) — v1〜v6 ハード変更影響の FFT 解析
