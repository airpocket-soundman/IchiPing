# pc/training — IchiPing v1 学習パイプライン

DigiKey v1 用の 1D-CNN マルチタスク NN（spec.html §4.3）の訓練・エクスポート一式。

## ファイル構成

| ファイル | 役割 |
|---|---|
| [features.py](features.py) | WAV → 整合フィルタで RIR 抽出 → 1024 bin log-magnitude スペクトル |
| [model.py](model.py) | 共有 backbone + 6 ヘッド (any_open / door_AB / door_BC / window_a/b/c) |
| [dataset.py](dataset.py) | `captures/` の labels.csv + WAV 群を読む PyTorch Dataset |
| [train.py](train.py) | 訓練ループ・ベストチェックポイント保存・ONNX エクスポート |

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
