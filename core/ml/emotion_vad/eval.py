# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""eval.py — held-out 评估 + 对标启发式（lexicon 聚合）基线。

报告每维 MAE/RMSE/Pearson/CCC，并和「词典聚合启发式」基线对比 ——
这正是系统现状（_score_vad / emotion_curve 用词典/关键词查表）的代表，量化「真模型 vs 启发式」增量。

lexicon 基线（纯 stdlib·复刻 SO-CAL 简化版·CVAW/CVAP 真词典聚合）：
  对每句，找所有作为子串出现的词典词 → V/A 取均值；零命中 → 0.5 中性。

跑法：
  python eval.py --baseline-only         # 仅跑词典基线（无需 torch·可立即跑·看启发式有多弱）
  python eval.py --ckpt checkpoints/va_base   # 跑训好的模型 + 对标基线
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from metrics import vad_metrics  # noqa: E402

PROC = HERE / "data" / "processed"


def _load_jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------- lexicon 基线
def lexicon_baseline(test_rows, lexicon_rows, dim_names):
    """CVAW/CVAP 词典聚合·V/A 句级预测。D 维词典没有 → 该维不参与（基线对 D 弃权）。"""
    # 长词优先（更具体）·只保留 V/A
    lex = sorted(lexicon_rows, key=lambda r: -len(r["text"]))
    preds = {d: [] for d in dim_names}
    golds = {d: [] for d in dim_names}
    covered = 0
    for r in test_rows:
        t = r["text"]
        vs, as_ = [], []
        for w in lex:
            wt = w["text"]
            if wt and wt in t:
                vs.append(w["valence"]); as_.append(w["arousal"])
        if vs:
            covered += 1
        pv = sum(vs) / len(vs) if vs else 0.5
        pa = sum(as_) / len(as_) if as_ else 0.5
        for d in dim_names:
            g = r.get(d)
            if g is None:
                continue
            if d == "valence":
                preds[d].append(pv); golds[d].append(g)
            elif d == "arousal":
                preds[d].append(pa); golds[d].append(g)
            # dominance: 词典无 → 基线弃权（不计入）
    cov = covered / len(test_rows) if test_rows else 0.0
    return vad_metrics(preds, golds), cov


# --------------------------------------------------------------------------- model
def model_predict(test_rows, ckpt_dir, dim_names, max_len=128, batch=32):
    try:
        import torch
        from transformers import AutoTokenizer
        from model import VADRegressor
    except ImportError as e:
        print(f"[FATAL] 跑模型评估需 torch+transformers：{e}", file=sys.stderr)
        sys.exit(2)
    model, meta = VADRegressor.load(ckpt_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    tok = AutoTokenizer.from_pretrained(ckpt_dir)
    preds = {d: [] for d in dim_names}
    golds = {d: [] for d in dim_names}
    texts = [r["text"] for r in test_rows]
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            enc = tok(chunk, truncation=True, max_length=max_len, padding=True, return_tensors="pt")
            out = model(enc["input_ids"].to(device), enc["attention_mask"].to(device),
                        enc.get("token_type_ids", None).to(device) if "token_type_ids" in enc else None)
            out = out.cpu().numpy()
            for bi, r in enumerate(test_rows[i:i + batch]):
                for j, d in enumerate(meta["dim_names"]):
                    g = r.get(d)
                    if g is None:
                        continue
                    preds[d].append(float(out[bi, j])); golds[d].append(float(g))
    return vad_metrics(preds, golds), meta


def _print_table(title, m, dim_names):
    print(f"\n== {title} ==")
    print(f"{'dim':10s} {'n':>6s} {'MAE':>8s} {'RMSE':>8s} {'Pearson':>9s} {'CCC':>8s}")
    for d in dim_names:
        x = m.get(d, {})
        def f(k):
            v = x.get(k)
            return f"{v:.4f}" if isinstance(v, (int, float)) else "  -  "
        print(f"{d:10s} {x.get('n', 0):>6d} {f('mae'):>8s} {f('rmse'):>8s} {f('pearson'):>9s} {f('ccc'):>8s}")


def main():
    ap = argparse.ArgumentParser(description="VAD 评估 + 对标词典启发式基线")
    ap.add_argument("--ckpt", default=None, help="训好的 checkpoint 目录（如 checkpoints/va_base）")
    ap.add_argument("--baseline-only", action="store_true", help="只跑词典基线（无需 torch）")
    ap.add_argument("--test", default=str(PROC / "sentence_test.jsonl"))
    ap.add_argument("--dims", default="va", choices=["va", "vad"])
    ap.add_argument("--out", default=str(HERE / "data" / "eval_report.json"))
    args = ap.parse_args()

    dim_names = ("valence", "arousal") if args.dims == "va" else ("valence", "arousal", "dominance")
    test_rows = _load_jsonl(Path(args.test))
    lexicon_rows = _load_jsonl(PROC / "word_lexicon.jsonl")
    print(f"test={len(test_rows)} 句 · lexicon={len(lexicon_rows)} 词 · dims={dim_names}")

    report = {"_doc": "🔴 2026-06-29 NN训练:情绪VAD回归 评估报告", "test_n": len(test_rows)}

    base_m, cov = lexicon_baseline(test_rows, lexicon_rows, dim_names)
    _print_table(f"启发式基线（CVAW/CVAP 词典聚合·覆盖率 {cov:.1%}）", base_m, dim_names)
    report["lexicon_baseline"] = {"metrics": base_m, "coverage": round(cov, 4)}

    if not args.baseline_only:
        if not args.ckpt:
            print("\n[NOTE] 未传 --ckpt·只跑了基线。训练后：python eval.py --ckpt <dir>", file=sys.stderr)
        else:
            model_m, meta = model_predict(test_rows, str(HERE / args.ckpt) if not Path(args.ckpt).is_absolute() else args.ckpt, dim_names)
            _print_table(f"训练模型（{meta['base_model']}）", model_m, dim_names)
            report["model"] = {"ckpt": args.ckpt, "base_model": meta["base_model"], "metrics": model_m}
            # 增量
            print("\n== 增量（模型 − 基线·CCC↑越多越好）==")
            for d in dim_names:
                mc = model_m.get(d, {}).get("ccc")
                bc = base_m.get(d, {}).get("ccc")
                if isinstance(mc, (int, float)) and isinstance(bc, (int, float)):
                    print(f"  {d}: ΔCCC = {mc - bc:+.4f}  (model {mc:.3f} vs lexicon {bc:.3f})")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 报告 → {args.out}")


if __name__ == "__main__":
    main()
