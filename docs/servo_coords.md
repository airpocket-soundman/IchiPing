# サーボ座標系 と キャリブレーション仕様

IchiPing の 5 サーボ（窓 × 3、扉 × 2）には **2 つの座標系**が共存します。本ドキュメントは仕様と運用ルールを明確化するためのもの。実装側は [`firmware/shared/include/servo_config.h`](../firmware/shared/include/servo_config.h) と [`firmware/shared/source/collector_display.c`](../firmware/shared/source/collector_display.c) が正本。

## 2 つの座標系

| 名前 | 単位 | 性質 | 誰が使うか |
|---|---|---|---|
| **mechanical_deg** | 0〜180° | PCA9685 / SG90 への生 PWM 角度。SG90 ホーンの取付角に依存するので **個体差あり** | ファーム内 PCA9685 ドライバ、`SERVO` コマンドの引数 |
| **logical_deg** | 0〜75° (窓) or 0〜90° (扉) | 「閉位置をゼロ、開方向を正」の **論理表示角度**。個体差を吸収済み | TFT 表示、ログ、PC 側可視化 |

両者の変換は次の通り:

```
logical_deg = sign × (mechanical_deg − home_deg[i])
sign        = +1 if open_deg[i] > home_deg[i] else −1
mechanical_deg = home_deg[i] + sign × logical_deg
```

## 既定値

`home_deg`（閉）と `open_deg`（全開）は **フラッシュに保持される個体校正値**。`kind` は機構固定で WINDOW/DOOR のいずれか。

| servo | PWM ch | kind | 期待 logical_max | 既定 home_deg | 既定 open_deg |
|---|---|---|---|---|---|
| `a`  | **0** | WINDOW | **75°** | 0° | 75° |
| `b`  | **1** | WINDOW | **75°** | 0° | 75° |
| `c`  | **2** | WINDOW | **75°** | 0° | 75° |
| `AB` | **3** | DOOR   | **90°** | 0° | 90° |
| `BC` | **4** | DOOR   | **90°** | 0° | 90° |

**PWM ch** は **PCA9685 / LU9685 どちらのバックエンドでも同じ番号**。サーボ抽象層 [`servo_driver.h`](../firmware/shared/include/servo_driver.h) が `servo_set_first_n_deg(deg, 5)` で配列インデックス = チップのチャネル番号として書き込むため、PCA9685 (ch 0..15 の最初 5) でも LU9685 (ch 0..19 の最初 5) でも `a → 0` / `b → 1` / `c → 2` / `AB → 3` / `BC → 4` の対応が共通で成り立つ。ビルド時に `-D SERVO_BACKEND_PCA9685` / `-D SERVO_BACKEND_LU9685_I2C` のどちらを定義しても、ICHP フレームの `servo_deg[0..4]` も同じ順序で並ぶ。

- **窓を 75° で止める理由**: 模型の窓サッシが完全 90° まで開くと外枠と干渉してビビる。実機で確認した clear 角度。
- **扉を 90° で止める理由**: 扉は壁直交方向が「全開」。90° を超えるとアクリル壁と接触。

校正後の典型値:
- `home_deg`: ±10° 程度の個体差
- `open_deg`: `home_deg + logical_max` の周辺、機構余裕で ±5° 程度

## 校正手順（09_collector の `SERVO` + `SET HOME` / `SET OPEN`）

1. 09_collector を起動して [`pc/collector_client.py`](../pc/collector_client.py) を接続
2. 1 ch ずつマニュアル動作で「閉」位置を探る:
   ```
   > SERVO a 0
   > SERVO a 5
   > SERVO a 10
   ```
3. 当たり位置を home に焼き付け:
   ```
   > SET HOME a 12
   ```
4. 同様に「全開」を探って焼き付け:
   ```
   > SERVO a 80
   > SERVO a 85
   > SET OPEN a 87
   ```
   このとき `open - home = 87 - 12 = 75°` が logical_max（窓 = 75）と一致するように物理調整するのが理想
5. 全 5 ch ぶん繰り返す
6. 現値を確認:
   ```
   > GET HOME
   OK HOME a=12.0 b=8.0 c=15.0 AB=5.0 BC=18.0
   > GET OPEN
   OK OPEN a=87.0 b=82.0 c=90.0 AB=95.0 BC=108.0
   ```
7. フラッシュ永続化 — **MVP は未実装**（[`SAVE HOME`](../firmware/projects/09_collector/README.md) が `ERR NOT_IMPL` を返す）。代わりに [`firmware/shared/source/servo_config.c`](../firmware/shared/source/servo_config.c) の `SERVO_CONFIG_DEFAULTS` を上の値で書き換えてリビルド・再書込
8. 以降は boot 時に同じ home / open 位置に戻る

## ディスプレイ表示

09_collector は ILI9341 240×320 TFT に以下を表示します（[collector_display.c](../firmware/shared/source/collector_display.c) 参照）:

![09_collector の ILI9341 240×320 ステータスパネル](img/collector_display_panel.svg)

行は **`a` / `b` / `c`（WINDOW, max +75°）** と **`AB` / `BC`（DOOR, max +90°）** の 5 サーボぶん。`値 / max`、進捗バー、状態バッジ（CLOSED 緑 / OPEN 橙 / MID 黄）の順。フッタは励起モード + ソフト音量（%）と `trial 現在/総数`。

### 状態判定（色分け）

| 状態 | 条件 | 色 |
|---|---|---|
| **CLOSED** | logical_deg ≤ 3° | 緑 (`ILI9341_GREEN`) |
| **OPEN** | logical_deg ≥ 95% × logical_max | 橙 (`ILI9341_ORANGE`) |
| **MID** | それ以外 | 黄 (`ILI9341_YELLOW`) |

### バー表示

`fill_w = (logical_deg / logical_max) × bar_width` で塗りつぶし。logical_max を越えた値は飽和（バー満タン）して表示。

## ICHP フレーム内 `servo_deg[5]` との関係

09_collector が送出する ICHP フレームの `servo_deg[]` 5 要素は **mechanical_deg**（生 PCA9685 角度）。理由は「実機・別 PC で再現する際に PCA9685 へ直接書ける値の方がデバッグしやすい」から。

PC 側で論理角度に変換したい場合は、`servo_config_to_logical()` 相当の処理を Python 側に実装するか、`labels.csv` 解析時に `home_deg` / `open_deg` を別途参照する。`captures/<label>/labels.csv` の `a..BC` 列も mechanical_deg。

## 仕様 vs 実装の対応表

| 仕様項目 | 実装場所 |
|---|---|
| home / open / kind の構造体 | [`servo_config.h`](../firmware/shared/include/servo_config.h) `servo_config_t` |
| 既定値（リビルド前提のハードコード） | [`servo_config.c`](../firmware/shared/source/servo_config.c) `SERVO_CONFIG_DEFAULTS` |
| logical ↔ mechanical 変換 | [`servo_config.c`](../firmware/shared/source/servo_config.c) `servo_config_to_logical` / `_to_mechanical` |
| TFT パネルレイアウト | [`collector_display.c`](../firmware/shared/source/collector_display.c) |
| `SET HOME` / `SET OPEN` コマンド | [`ichp_cmd.h`](../firmware/shared/include/ichp_cmd.h) `ICHP_CMD_SET_HOME` |
| ランダムパターン生成（home / open 二値） | [`09_collector/main.c`](../firmware/projects/09_collector/main.c) `build_trial_pattern` |
| 校正運用手順 | 本ドキュメント §校正手順 |

## 関連ドキュメント

- [`09_collector/README.md`](../firmware/projects/09_collector/README.md) — コマンド一覧、ビルド手順
- [`docs/spec.html`](spec.html) §7 — 実験模型の機構と寸法
- [`hardware/wiring.md`](../hardware/wiring.md) §2.5 — PCA9685 チャネル割当
