# pc/training — IchiPing v1 学習パイプライン

DigiKey v1 用の 1D / 2D-CNN（[NN 設計](../../docs/nn_design.html): **14-class + 32-class 両 head 共存**）の訓練・エクスポート一式。
本番モデルは [`model_32cls_neutron.py`](model_32cls_neutron.py)（Neutron 互換 XL, ~104K params）で、
MCU 実機 14cls / 32cls とも 100% 達成（[v12345 検証レポート](../../docs/v12345_report.html)）。
本ドキュメントの旧 6-head 混合構成記述（`model.py` の `IchiPingV1`）は legacy で、本番経路では未使用。

## ファイル構成

| ファイル | 役割 |
|---|---|
| [features.py](features.py) | WAV → 整合フィルタで RIR 抽出 → 1024 bin log-magnitude スペクトル |
| [model.py](model.py) | 共有 backbone + 6 ヘッド (any_open / door_AB / door_BC / window_a/b/c) |
| [dataset.py](dataset.py) | `captures/<run_id>/sXXXXX/` 形式を読む PyTorch Dataset (新 layout) |
| [augment.py](augment.py) | TimeShift / LevelJitter / NoiseOverlay / GaussianHiss + Compose |
| [train.py](train.py) | 訓練ループ・ベストチェックポイント保存 |
| [infer.py](infer.py) | PC 推論 + 14 状態 macro-F1 / 混同行列 |
| [export_onnx.py](export_onnx.py) | PyTorch → ONNX (opset 13, 固定形状) |
| [quantize.py](quantize.py) | INT8 量子化 (eiq-onnx2tflite or onnxruntime PTQ) |

## サンプル所要量

| フェーズ | 状態あたり | 総数 (32 真状態) | 拡張後実効 | 所要 |
|---|---:|---:|---:|---:|
| PoC | 10 | 320 | ~2,000 | ~8 分 |
| **本収集 v1**（推奨）| **30** | **960** | **~5,000** | **~25 分** |
| 本収集 v2 | 100 | 3,200 | ~20,000 | ~80 分 |

`pc/plans/gen32.py` の `REPEATS = 30` で再生成すれば本収集 v1 のプランが揃う。

## 反復ループ（device 収集 → PC 推論 → 改善 → MCU デプロイ）

```powershell
cd pc

# A. 実機で 960 サンプル収集
uv run python collector_client.py --port COM3 \
    --plan plans/full_32_v2.yaml --run-id full_32_v2

# B. PC で訓練 (FP32, 5-10 分)
uv run --extra training python -m training.train \
    --captures captures/full_32_v2 \
    --out runs/v1 --epochs 50

# C. PC 推論で検証
uv run --extra training python -m training.infer \
    --captures captures/full_32_v2 \
    --ckpt runs/v1/best.pt \
    --out reports/v1_eval/
#   → reports/v1_eval/{report.json, confusion_matrix.png, confusion.csv}

# D-1. ONNX エクスポート
uv run --extra training python -m training.export_onnx \
    --ckpt runs/v1/best.pt --out runs/v1/best.onnx

# D-2. INT8 量子化 (NXP eiq-onnx2tflite)
uv run --extra training python -m training.quantize \
    --onnx runs/v1/best.onnx --out runs/v1/best_int8.tflite \
    --captures captures/full_32_v2 --backend eiq

# E. (Windows / eIQ Toolkit CLI) Neutron Converter
eiq-converter --plugin eiq-converter-neutron \
              --custom-options "target mcxn94x" \
              runs/v1/best_int8.tflite runs/v1/best_neutron.tflite

# F. (firmware) C 配列化して flash 書込み
xxd -i runs/v1/best_neutron.tflite > firmware/projects/10_inference/source/model_data.h
```

各ステップの目安: A=25 分 / B=5-10 分 / C=1 分 / D=2-3 分 / E=2 分。
**反復 1 周あたり ~45 分**、1 日で 5-10 周回せる。

## クイックスタート

データは `pc/receiver.py` が生成する `captures/` ディレクトリ（labels.csv + frame_*.wav）を想定。

```powershell
# 1. 環境を作る
cd pc
conda env create -f environment.yml
conda activate ichiping

# 2. データ収集（loopback でダミー、または実機 + 模型）
python emulator.py --out captures_dummy/stream.bin --frames 200 --cadence 0
python receiver.py --in captures_dummy/stream.bin --out captures_dummy

# 3. 訓練
python -m training.train --captures captures_dummy --out ../runs/v1_pilot --epochs 50

# 4. 推論物の確認
ls ../runs/v1_pilot/
#  -> best.pt        PyTorch チェックポイント
#  -> best.onnx      ONNX 形式（eIQ Toolkit で INT8 量子化する入力）
#  -> train_log.csv  per-epoch ロス
#  -> config.json    再現用メタ
```

## 確認方法

このディレクトリは実機・シリアル不要。**PC 内で完結**。

### A. 訓練が動く・loss が下がっていることの確認（本筋）

1. `emulator.py` で 200 サンプル分のダミーデータを作る（実機が無くても可）:
   ```powershell
   python emulator.py --out captures_dummy/stream.bin --frames 200 --cadence 0
   python receiver.py --in captures_dummy/stream.bin --out captures_dummy
   ```
2. 訓練を 5 エポックほど走らせる:
   ```powershell
   python -m training.train --captures captures_dummy --out ../runs/smoke --epochs 5
   ```
3. 期待出力（loss が下がる）:
   ```
   epoch 1: train_loss=0.6932 val_loss=0.6914 (any_open: 0.51 AUROC)
   epoch 2: train_loss=0.6810 val_loss=0.6720 ...
   ...
   saved best.pt @ epoch 4 (val_loss=0.6450)
   saved best.onnx
   ```

### B. ONNX エクスポートが成立していることの確認

onnxruntime で再ロードして推論できるか 1 サンプルで確かめる:

```powershell
python -c "
import onnxruntime as ort
import numpy as np
sess = ort.InferenceSession('../runs/smoke/best.onnx')
x = np.random.randn(1, 1, 1024).astype(np.float32)
out = sess.run(None, {sess.get_inputs()[0].name: x})
print('outputs:', [o.shape for o in out])
"
```

6 個の出力が返ってくれば OK。これが通れば eIQ Toolkit に入力できる。

### C. データセット読み込みの単体動作確認

```powershell
python -c "
from training.dataset import IchiPingDataset
ds = IchiPingDataset('captures_dummy')
print('size:', len(ds))
x, y = ds[0]
print('x:', x.shape, 'y keys:', list(y.keys()))
"
```

`x.shape == (1, 1024)`、`y` に 6 個のラベルが入っていれば OK。

### D. tensorboard で訓練を可視化（任意）

```powershell
tensorboard --logdir ../runs/smoke
```

ブラウザで http://localhost:6006

### E. 実機データで本訓練

v0.4 で 3 部屋模型を組んだあと、実機で 10000 サンプル収集して 50 エポック訓練:

```powershell
python -m training.train --captures ../captures_real --out ../runs/v1_pilot --epochs 50
```

## 設計の意図

- **backbone は `~30 K params`、INT8 で約 30 KB**。MCXN947 の NPU メモリ予算に収まる。
- **マルチタスク学習で共有 backbone** を 6 つのヘッドで使う。spec.html §4.3 の図を踏襲。
- **特徴は 1024 bin log-magnitude スペクトル**。MCU 側は PowerQuad で N=2048 rFFT を計算し、`bin 1..1024` を使う（DC を捨てて 1024 にする）。
- **ロス重み**は `train.py` 冒頭の `LOSS_WEIGHTS` 辞書で調整。データ分布に合わせて要チューニング。

## 次のステップ（v0.5 タスク）

1. `IP-0.5.1` PyTorch で訓練・評価 ← ここで完了
2. `IP-0.5.2` ONNX → eIQ Toolkit で INT8 量子化 + MCXN947 用 C コード生成（[docs/mcu_deployment.html](../../docs/mcu_deployment.html) 参照）
3. `IP-0.5.3` MCU 側に推論コードを組み込み、chirp → RIR → spectrum → NN → 結果のレイテンシ計測

## NN 詳細設計

アーキテクチャ図と各層の理由は [../../docs/nn_design.html](../../docs/nn_design.html) を参照。
