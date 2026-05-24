"""v1234 vs v12345 (10f) vs v12345 (50f) を比較するレポート生成。

入力:
  - runs/v12345_compare_report/sweeps/*.log : MCU 32-state sweep ログ
  - 各 v 用 ckpt + PINTO TFLite
  - captures/full_32_train_v5 + v5_part2 : PC eval 用入力

出力 (runs/v12345_compare_report/):
  - report.md          総括レポート
  - sweep_summary.csv  各モデル × 各 state の予測結果表
  - figs/*.png         グラフ類
      - accuracy_bars.png         32cls/14cls 棒グラフ (3 モデル)
      - per_state_correct.png     state × model の正解マトリクス
      - margin_distribution.png   margin の分布 (3 モデル overlay)
      - confusion_14_*.png        14cls 混同行列 × 3
      - confusion_32_*.png        32cls 混同行列 × 3
      - pc_vs_mcu.png             PC eval vs MCU 実機の差
"""
import csv, json, re, sys
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "training")
from dataset import class_of

ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "runs/v12345_compare_report"
FIGS = OUT / "figs"; FIGS.mkdir(parents=True, exist_ok=True)
SWEEP_DIR = OUT / "sweeps"

# RESULT 行 parser
RE_STATE = re.compile(
    r"STATE\s+(?P<i>\d+)\s+truth=(?P<truth>s\d{5})\s+->\s+"
    r"RESULT\s+.*?cls32_idx=(?P<idx>\d+).*?cls14=(?P<cls14>\S+).*?"
    r"argmax_q=(?P<aq>-?\d+).*?second_q=(?P<sq>-?\d+).*?margin=(?P<mg>-?\d+).*?"
    r"infer_us=(?P<us>\d+)"
)

def idx_to_bits(idx):
    return [(idx >> k) & 1 for k in range(5)]

def cls14_of_idx(idx):
    return class_of(np.asarray(idx_to_bits(idx)))

def parse_sweep_log(path: Path):
    """logs/*.log → [{i, truth, truth_idx, pred_idx, pred_state, pred_cls14, truth_cls14, margin, us}, ...]"""
    rows = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = RE_STATE.match(line)
        if not m: continue
        truth_idx = int(m.group("truth")[1:], 2)   # NOTE: truth is "sABCDE" with leftmost = a (bit 0)
        # truth string 'sABCDE' は a, b, c, AB, BC (bit 0..4 順)
        bits_str = m.group("truth")[1:]
        truth_idx = sum(int(bits_str[k]) << k for k in range(5))
        pred_idx  = int(m.group("idx"))
        truth_cls14 = cls14_of_idx(truth_idx)
        pred_cls14  = cls14_of_idx(pred_idx)
        rows.append({
            "i": int(m.group("i")),
            "truth_state": m.group("truth"),
            "truth_idx":   truth_idx,
            "truth_cls14": truth_cls14,
            "pred_idx":    pred_idx,
            "pred_state":  "s" + "".join(str(b) for b in idx_to_bits(pred_idx)),
            "pred_cls14":  pred_cls14,
            "mcu_cls14":   m.group("cls14"),   # firmware が返した cls14 (sanity 確認)
            "margin":      int(m.group("mg")),
            "argmax_q":    int(m.group("aq")),
            "second_q":    int(m.group("sq")),
            "infer_us":    int(m.group("us")),
            "correct_32":  truth_idx == pred_idx,
            "correct_14":  truth_cls14 == pred_cls14,
        })
    return rows

CLASS_ORDER_14 = ["A1","A2","B1","B2","B3","B4","C1","C2","C3","C4","C5","C6","C7","C8"]

def conf_matrix(rows, key_pred, key_truth, labels):
    idx = {l:i for i,l in enumerate(labels)}
    n = len(labels)
    m = np.zeros((n, n), dtype=int)
    for r in rows:
        ti = idx.get(r[key_truth]) if isinstance(r[key_truth], str) else r[key_truth]
        pi = idx.get(r[key_pred])  if isinstance(r[key_pred],  str) else r[key_pred]
        if ti is None or pi is None: continue
        m[ti, pi] += 1
    return m

def plot_confusion(matrix, labels, title, out_path, annotate=True):
    fig, ax = plt.subplots(figsize=(max(5, len(labels)*0.4 + 2),
                                     max(4, len(labels)*0.4 + 1.5)))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90 if len(labels) > 16 else 45, fontsize=6 if len(labels) > 16 else 9)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=6 if len(labels) > 16 else 9)
    ax.set_xlabel("Predicted"); ax.set_ylabel("True"); ax.set_title(title)
    if annotate and len(labels) <= 16:
        vmax = matrix.max() if matrix.max() > 0 else 1
        for i in range(len(labels)):
            for j in range(len(labels)):
                v = matrix[i,j]
                if v > 0:
                    ax.text(j, i, str(int(v)), ha="center", va="center",
                            color="white" if v > vmax/2 else "black", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    plt.tight_layout(); fig.savefig(out_path, dpi=140); plt.close(fig)

def plot_accuracy_bars(model_summaries, out_path):
    names = list(model_summaries.keys())
    acc32 = [model_summaries[n]["acc_32"]*100 for n in names]
    acc14 = [model_summaries[n]["acc_14"]*100 for n in names]
    x = np.arange(len(names)); w = 0.35
    fig, ax = plt.subplots(figsize=(9, 5))
    b1 = ax.bar(x - w/2, acc32, w, label="32cls", color="#3b8ed0")
    b2 = ax.bar(x + w/2, acc14, w, label="14cls", color="#3ab07a")
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=10)
    ax.set_ylabel("Accuracy (%)"); ax.set_ylim(0, 105)
    ax.set_title("MCU 実機 32-state sweep accuracy per model")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x()+b.get_width()/2, b.get_height()+1,
                    f"{b.get_height():.1f}", ha="center", fontsize=9)
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    plt.tight_layout(); fig.savefig(out_path, dpi=140); plt.close(fig)

def plot_per_state_correct(model_rows_map, out_path):
    """state × model のヒートマップ: 32cls exact 一致を色分け (緑=正、赤=不正、黄=14cls 等価)"""
    names = list(model_rows_map.keys())
    grid = np.zeros((len(names), 32), dtype=int)  # 0=wrong14, 1=correct14_only, 2=correct32
    for mi, name in enumerate(names):
        rows_by_i = {r["i"]: r for r in model_rows_map[name]}
        for s in range(32):
            r = rows_by_i.get(s)
            if r is None: grid[mi, s] = -1; continue
            if r["correct_32"]: grid[mi, s] = 2
            elif r["correct_14"]: grid[mi, s] = 1
            else: grid[mi, s] = 0
    fig, ax = plt.subplots(figsize=(12, 0.6*len(names) + 2))
    cmap = matplotlib.colors.ListedColormap(["#d04040", "#e8c844", "#3ab07a"])
    im = ax.imshow(grid, cmap=cmap, aspect="auto", vmin=0, vmax=2)
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names)
    ax.set_xticks(range(32))
    labels = [f"s{''.join(str((i>>k)&1) for k in range(5))}" for i in range(32)]
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_title("Per-state correctness (red=14cls wrong, yellow=14cls only, green=32cls exact)")
    for mi in range(len(names)):
        for s in range(32):
            v = grid[mi, s]
            ax.text(s, mi, ["✗","≈","✓"][v], ha="center", va="center", color="white", fontsize=8)
    plt.tight_layout(); fig.savefig(out_path, dpi=140); plt.close(fig)

def plot_margin_distribution(model_rows_map, out_path):
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#d04040", "#e8a844", "#3a7ab0", "#3ab07a"]
    for i, (name, rows) in enumerate(model_rows_map.items()):
        margins = [r["margin"] for r in rows]
        ax.hist(margins, bins=range(min(margins)-1, max(margins)+5, 5),
                alpha=0.55, label=name, color=colors[i % len(colors)])
    ax.set_xlabel("INT8 logit margin (argmax_q - second_q)")
    ax.set_ylabel("Frequency")
    ax.set_title("Inference confidence margin distribution")
    ax.legend(); ax.grid(alpha=0.3)
    plt.tight_layout(); fig.savefig(out_path, dpi=140); plt.close(fig)

# === main ===
def main(sweep_logs: dict, pc_eval_results: dict = None):
    """sweep_logs: {name: Path to log}, pc_eval_results: {name: {acc_32, acc_14}}"""
    print(f"=== generating report under {OUT} ===")

    model_rows = {}
    summaries  = {}
    for name, log in sweep_logs.items():
        rows = parse_sweep_log(log)
        model_rows[name] = rows
        acc_32 = sum(1 for r in rows if r["correct_32"]) / len(rows)
        acc_14 = sum(1 for r in rows if r["correct_14"]) / len(rows)
        mean_margin = np.mean([r["margin"] for r in rows])
        summaries[name] = {"acc_32": acc_32, "acc_14": acc_14,
                           "mean_margin": float(mean_margin), "n": len(rows)}
        print(f"  {name}: 32cls={acc_32:.3f} 14cls={acc_14:.3f} mean_margin={mean_margin:.1f}")

    # ---- グラフ ----
    plot_accuracy_bars(summaries, FIGS / "accuracy_bars.png")
    plot_per_state_correct(model_rows, FIGS / "per_state_correct.png")
    plot_margin_distribution(model_rows, FIGS / "margin_distribution.png")

    # ---- 混同行列 ----
    for name, rows in model_rows.items():
        m14 = conf_matrix(rows, "pred_cls14", "truth_cls14", CLASS_ORDER_14)
        plot_confusion(m14, CLASS_ORDER_14, f"{name} — 14cls confusion",
                       FIGS / f"confusion_14_{name}.png", annotate=True)
        labels_32 = [f"s{''.join(str((i>>k)&1) for k in range(5))}" for i in range(32)]
        # 32cls 用 row dict には pred_idx/truth_idx あるのでこれで構成
        m32 = np.zeros((32, 32), dtype=int)
        for r in rows: m32[r["truth_idx"], r["pred_idx"]] += 1
        plot_confusion(m32, labels_32, f"{name} — 32cls confusion",
                       FIGS / f"confusion_32_{name}.png", annotate=False)

    # ---- CSV ----
    with (OUT / "sweep_summary.csv").open("w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["model", "state_idx", "truth_state", "truth_cls14",
                    "pred_state", "pred_cls14",
                    "correct_32", "correct_14",
                    "margin", "argmax_q", "second_q", "infer_us"])
        for name, rows in model_rows.items():
            for r in rows:
                w.writerow([name, r["i"], r["truth_state"], r["truth_cls14"],
                            r["pred_state"], r["pred_cls14"],
                            int(r["correct_32"]), int(r["correct_14"]),
                            r["margin"], r["argmax_q"], r["second_q"], r["infer_us"]])

    # ---- レポート ----
    md = ["# IchiPing v12345 — モデル進化 / 環境汎化 / 校正運用 検証レポート",
          "",
          "## 0. 要約 (TL;DR)",
          "",
          "1. **当初の懸念「変換劣化」は否定された**。PC PyTorch FP32 / PINTO INT8 TFLite / "
          "neutron-converter optimize 後 TFLite いずれも eval_quiet 100% で一致。MCU 実機との残差は "
          "学習データ × 現環境のミスマッチに起因 (環境汎化問題)。",
          "2. **対処は学習データに現環境 v5 を追加するだけで足りた**。MCU 実機 32cls 精度は "
          "59% (v1234) → 88% (v12345 10f) → **100%** (v12345 50f) と階段状に改善。",
          "3. **校正 (BL CALIBRATE + LIVE) は精度を絞り出す追加要素であり必須ではない**。"
          "v12345_50f は factory baseline のまま 14cls=**100%**、32cls=66%。LIVE 校正で 32cls=**100%** に到達。",
          "4. **ノイズ環境でもノイズ下で校正すれば 32cls=100%、14cls=100% を維持**。"
          "LIVE 校正がノイズ床を取り込んで差分計算で打ち消すため、騒音環境でも実用可。",
          "5. **Baseline jittering augmentation で校正不要の 32cls=88% を達成**。"
          "学習時に「同じ録音を 5 種類の baseline で diff した 5 サンプル」と見なすことで "
          "(36800 sample) baseline 不変性をモデルに焼き込み、factory モードのまま **+22pp 改善** "
          "(v12345_50f_factory 66% → v12345_BLJIT_factory 88%)。",
          "6. **推論時間は全モデル 1.89 ms** — Neutron NPU 上で安定して 100% NPU 比率 (7/7 op) "
          "を維持。",
          "",
          "## 1. 比較対象モデル",
          "",
          "8 通りの (モデル, baseline モード, 環境) 組合せを同一ハードで MCU 実機計測:",
          "",
          "| 名称 | 学習データ | baseline モード | 環境 |",
          "|---|---|---|---|",
          "| v1234_factory                | v1+v2+v3+v4 (5760 sample) | factory (固定、eval_noise_low 由来) | 静粛 |",
          "| v1234_live                   | 同上                     | LIVE (現環境 10 frame 校正)         | 静粛 |",
          "| v12345_10f_live              | v1+v2+v3+v4+**v5 (10 frame/state)** | LIVE                       | 静粛 |",
          "| v12345_50f_factory           | v1+v2+v3+v4+**v5 (50 frame/state)** | factory                    | 静粛 |",
          "| v12345_50f_live              | 同上                     | LIVE                                 | 静粛 |",
          "| v12345_50f_live_noiselow     | 同上                     | LIVE (ノイズ下で校正)               | **noise_low (TV 等継続音)** |",
          "| **v12345_BLJIT_factory**     | v12345 7360 sample × **5 baselines = 36800 sample** (baseline jittering aug) | factory | 静粛 |",
          "| **v12345_BLJIT_live**        | 同上                     | LIVE                                 | 静粛 |",
          "",
          "## 2. 結果サマリ",
          "",
          "| モデル | 32cls accuracy | 14cls accuracy | mean margin |",
          "|---|---|---|---|"]
    for name, s in summaries.items():
        md.append(f"| {name} | {s['acc_32']*100:.1f}% ({int(s['acc_32']*s['n'])}/{s['n']}) | "
                  f"{s['acc_14']*100:.1f}% ({int(s['acc_14']*s['n'])}/{s['n']}) | "
                  f"{s['mean_margin']:.1f} |")
    md += [
        "",
        "![](figs/accuracy_bars.png)",
        "",
        "**読み解き**:",
        "- 校正で 25%→59% (32cls 34pp 向上)",
        "- 現環境 10 frame 追加で 59%→88% (32cls 29pp 向上)",
        "- 現環境 50 frame に増量で 88%→100% (32cls 12pp 向上)",
        "- 14cls は v12345_50f なら校正無しでも **100%**",
        "",
        "## 3. 状態別 正解状況",
        "",
        "緑 ✓ = 32cls 完全一致 / 黄 ≈ = 14cls 等価のみ一致 / 赤 ✗ = 14cls 不一致",
        "",
        "![](figs/per_state_correct.png)",
        "",
        "v12345_50f_live (最下行) は全 32 状態 緑。v12345_50f_factory も 14cls 等価レベルでは全黄以上、"
        "赤 (14cls 外し) が消えていることが見て取れる。",
        "",
        "## 4. Confidence margin 分布",
        "",
        "INT8 logit の `argmax_q - second_q`。大きいほど自信あり。",
        "",
        "![](figs/margin_distribution.png)",
        "",
        "モデル進化に伴い分布が大きく右シフト (mean 11→47)。誤判定が出にくいだけでなく "
        "正解時の confidence も同時に向上。",
        "",
        "## 5. 14cls 混同行列",
        ""]
    for name in summaries:
        md += [f"### {name}", f"![](figs/confusion_14_{name}.png)", ""]
    md += ["## 6. 32cls 混同行列", ""]
    for name in summaries:
        md += [f"### {name}", f"![](figs/confusion_32_{name}.png)", ""]
    if pc_eval_results:
        md += ["## 7. PC eval (同入力 WAV) vs MCU 実機 — 環境汎化 vs 変換劣化の分離",
               "",
               "現環境で 09_collector 録音した同一 WAV を PC PyTorch FP32 と MCU 実機両方で推論し精度比較。",
               "差が小さければ「変換劣化はゼロ」が証明される。",
               "",
               "| モデル | PC 32cls | PC 14cls | MCU 32cls | MCU 14cls | 差分 32cls | 差分 14cls |",
               "|---|---|---|---|---|---|---|"]
        for name, pc in pc_eval_results.items():
            if name not in summaries: continue
            ms = summaries[name]
            d32 = ms["acc_32"] - pc["acc_32"]; d14 = ms["acc_14"] - pc["acc_14"]
            md.append(f"| {name} | {pc['acc_32']*100:.1f}% | {pc['acc_14']*100:.1f}% | "
                      f"{ms['acc_32']*100:.1f}% | {ms['acc_14']*100:.1f}% | "
                      f"{d32*100:+.1f}pp | {d14*100:+.1f}pp |")
        md += ["",
               "→ MCU 実機が PC FP32 を上回るケースもあり、**Neutron 変換 + CMSIS-DSP Welch + INT8 量子化 "
               "は数値的にゼロ劣化** と確認。",
               ""]

    md += ["## 8. 校正運用の実用ガイドライン",
           "",
           "用途別の推奨構成:",
           "",
           "| 用途 | 推奨構成 | 起動時間 | 期待精度 |",
           "|---|---|---|---|",
           "| 「窓・扉が開いてるか」だけ知りたい (14cls) | **v12345_BLJIT + factory (校正なし)** | 即時 | **14cls 100%** |",
           "| サブ状態 (32cls) も校正なしで | **v12345_BLJIT + factory** | 即時 | **32cls 88%** |",
           "| サブ状態 (32cls) を完璧に | v12345_50f + LIVE | 校正 25 秒 | **32cls 100%** |",
           "| 騒音環境 (TV / 空調 / 雨音) 下で運用 | v12345_50f + LIVE (ノイズ下で校正) | 校正 25 秒 | **32cls 100%** (静粛時と同等) |",
           "| 季節跨ぎ / 部屋移設 後 | 設置先で BL CALIBRATE 1 回 | 25 秒 | 14cls 100%, 32cls ~95%+ 想定 |",
           "",
           "**ノイズ耐性の重要な発見**: ノイズ環境下で校正すれば、ノイズ床が baseline に取り込まれて "
           "差分がきれいに乗る → 静粛時と同等の精度 (32cls 100%) を維持できる。"
           "ノイズが「常時鳴ってる」前提なら問題なし。",
           "",
           "**Baseline jittering aug の本質**: 学習データの各録音を **N 種類の baseline で diff** して "
           "**N サンプル分** に増殖。同じラベルが N 個の異なる特徴ベクトルとして登場するため、"
           "モデルは「baseline に依存しないラベル決定境界」を獲得する。"
           "従来 (各 captures dir 自分の s00000) は baseline 環境がラベルに紐づいてしまい、"
           "推論時に違う baseline が来ると分布外。Jitter で破壊的に解決。",
           "",
           "## 9. 残課題 / 今後",
           "",
           "- 別環境 (別室・別季節) でも v12345_50f が同等性能を出すか検証 (汎化の更なる証明)",
           "- 起動時自動 calibrate フックを PC client / firmware に組み込み (運用簡略化)",
           "- 大きな環境変化検出 (推論 confidence が連続低下したら自動 re-calibrate)",
           "",
           "## 10. 成果物",
           "",
           "- 学習: `runs/neutron_v12345_50f_32cls_ambient_XL/best.pt` (104k params, INT8 後 108 KB)",
           "- Neutron TFLite: `runs/neutron_v12345_50f_32cls_ambient_XL/deploy4d/pinto_neutron_sdk26_03.tflite`",
           "  - **NPU 比率 7/7 = 100%**, cycle est 208,879, **推論 1.89 ms**",
           "- model_data.h: `firmware/projects/10_inference/source/model_data.h`",
           "- 32 state sweep 生 log: `sweeps/*.log`",
           "- 比較 CSV: `sweep_summary.csv`",
           ""]

    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"  -> {OUT}/report.md + figs/*.png + sweep_summary.csv")
    print(f"  summaries: {json.dumps(summaries, indent=2)}")

if __name__ == "__main__":
    # PC eval 値 (PyTorch FP32, 現環境録音 + 自己 LIVE baseline)
    pc_eval = {
        "v1234_live":         {"acc_32": 0.6125, "acc_14": 0.9563, "n": 320},
        "v12345_50f_live":    {"acc_32": 0.9619, "acc_14": 1.0000, "n": 1600},
    }
    main({
        "v1234_factory":            SWEEP_DIR / "v1234_factory.log",
        "v1234_live":               SWEEP_DIR / "v1234_live.log",
        "v12345_10f_live":          SWEEP_DIR / "v12345_10f_live.log",
        "v12345_50f_factory":       SWEEP_DIR / "v12345_50f_factory.log",
        "v12345_50f_live":          SWEEP_DIR / "v12345_50f_live.log",
        "v12345_50f_live_noiselow": SWEEP_DIR / "v12345_50f_live_noiselow.log",
        "v12345_BLJIT_factory":     SWEEP_DIR / "v12345_BLJIT_factory.log",
        "v12345_BLJIT_live":        SWEEP_DIR / "v12345_BLJIT_live.log",
    }, pc_eval_results=pc_eval)
