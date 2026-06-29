# 🔴 2026-06-29 NN训练:质量AI腔判别
"""eval.py — 在 held-out test 集上评估「human vs AI腔」分类器

【报告】
  · chunk 级：accuracy / F1 / AUC / PR-AUC / TPR@1%FPR / 混淆矩阵
  · doc 级：同 source 文件多 chunk logit 取均值聚合后再判（线上按整章/整 cluster 判）
  · 按 kind 拆分（ai_replica 复刻硬负 vs ai_generated 多生成器 vs ai_draft）
    —— 看对抗鲁棒性：复刻样本(MAGA 式 humanized 硬负)上掉多少？
  · 风险提示：若 by_author test 上 AUC 远低于 random_file → 学到的是作者身份捷径。

【用法】
  python eval.py --model-dir runs/macbert_v1 --data-dir data --split test
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_ORDER  # noqa: E402


def load_jsonl(path):
    return [json.loads(l) for l in Path(path).open(encoding="utf-8") if l.strip()]


def feat_vec(rec, scaler):
    f = rec["feats"]
    return [(float(f.get(k, 0.0)) - scaler[k]["mean"]) / scaler[k]["std"]
            for k in FEATURE_ORDER]


def confusion(labels, preds):
    tp = sum(1 for y, p in zip(labels, preds) if y == 1 and p == 1)
    tn = sum(1 for y, p in zip(labels, preds) if y == 0 and p == 0)
    fp = sum(1 for y, p in zip(labels, preds) if y == 0 and p == 1)
    fn = sum(1 for y, p in zip(labels, preds) if y == 1 and p == 0)
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def metrics(probs, labels, thr=0.5):
    from sklearn.metrics import (roc_auc_score, f1_score, average_precision_score,
                                 accuracy_score, roc_curve, precision_score,
                                 recall_score)
    import numpy as np
    probs = np.asarray(probs)
    labels = np.asarray(labels)
    preds = (probs >= thr).astype(int)
    out = {
        "n": int(len(labels)),
        "acc": round(float(accuracy_score(labels, preds)), 4),
        "f1": round(float(f1_score(labels, preds, zero_division=0)), 4),
        "precision": round(float(precision_score(labels, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, preds, zero_division=0)), 4),
        "confusion": confusion(labels.tolist(), preds.tolist()),
    }
    if len(set(labels.tolist())) == 2:
        out["auc"] = round(float(roc_auc_score(labels, probs)), 4)
        out["pr_auc"] = round(float(average_precision_score(labels, probs)), 4)
        fpr, tpr, _ = roc_curve(labels, probs)
        idx = np.where(fpr <= 0.01)[0]
        out["tpr_at_1pct_fpr"] = round(float(tpr[idx[-1]]) if len(idx) else 0.0, 4)
    return out


def predict(model, loader, device, fp16):
    import torch
    model.eval()
    probs = []
    with torch.no_grad():
        for batch in loader:
            ids = batch["input_ids"].to(device)
            mask = batch["attention_mask"].to(device)
            feats = batch["feats"].to(device)
            with torch.cuda.amp.autocast(enabled=fp16):
                logits, _ = model(ids, mask, feats)
            p = torch.softmax(logits, -1)[:, 1].float().cpu().numpy()
            probs.extend(p.tolist())
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--split", default="test")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoTokenizer

    # train.py 的 build_model 复用
    from train import build_model, make_torch_dataset

    model_dir = Path(args.model_dir)
    cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    scaler_blob = json.loads((model_dir / "feat_scaler.json").read_text(encoding="utf-8"))
    scaler = scaler_blob["scaler"]
    device = "cuda" if torch.cuda.is_available() else "cpu"

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = build_model(cfg["base"], cfg["use_feats"], cfg.get("quality_head", False),
                        cfg.get("lora", False)).to(device)
    model.load_state_dict(torch.load(model_dir / "model.pt", map_location=device))

    recs = load_jsonl(Path(args.data_dir) / f"{args.split}.jsonl")
    ds = make_torch_dataset(recs, tokenizer, scaler, args.max_len)
    loader = DataLoader(ds, batch_size=args.batch)
    probs = predict(model, loader, device, args.fp16)
    labels = [int(r["label"]) for r in recs]

    report = {"_marker": "🔴 2026-06-29 NN训练:质量AI腔判别",
              "model_dir": str(model_dir), "split": args.split}
    report["chunk_level"] = metrics(probs, labels)

    # —— doc 级聚合（同 source 均值）——
    by_src = defaultdict(lambda: {"probs": [], "label": None})
    for r, p in zip(recs, probs):
        by_src[r["source"]]["probs"].append(p)
        by_src[r["source"]]["label"] = int(r["label"])
    doc_probs = [sum(v["probs"]) / len(v["probs"]) for v in by_src.values()]
    doc_labels = [v["label"] for v in by_src.values()]
    report["doc_level"] = metrics(doc_probs, doc_labels)

    # —— 按 kind 拆分（对抗鲁棒性）——
    by_kind = defaultdict(lambda: {"probs": [], "labels": []})
    for r, p in zip(recs, probs):
        by_kind[r["kind"]]["probs"].append(p)
        by_kind[r["kind"]]["labels"].append(int(r["label"]))
    report["by_kind"] = {}
    for k, v in by_kind.items():
        # 单类（如全 ai_replica）只报 recall/acc
        report["by_kind"][k] = metrics(v["probs"], v["labels"])

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    # 醒目摘要到 stderr
    c = report["chunk_level"]
    print(f"\n[chunk] acc={c['acc']} f1={c['f1']} auc={c.get('auc')} "
          f"pr_auc={c.get('pr_auc')} tpr@1%fpr={c.get('tpr_at_1pct_fpr')}",
          file=sys.stderr)
    print(f"[confusion] {c['confusion']}", file=sys.stderr)


if __name__ == "__main__":
    main()
