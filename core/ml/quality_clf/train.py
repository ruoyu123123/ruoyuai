# 🔴 2026-06-29 NN训练:质量AI腔判别
"""train.py — fine-tune 中文 encoder 做「human vs AI腔」二分类（+可选质量回归头）

【⚠️ 别在写脚本时跑·留给主代理在 RTX 4070Ti(12GB) 上集中跑】

【架构】（调研接地·见 README §研究结论）
  encoder(hfl/chinese-macbert-base·102M·12GB 可全量 FT·fp16)
     → masked mean-pool 句向量 h
     ⊕ 可选融合 24 维确定性风格特征(features.py·抗题材捷径·arXiv:2503.00258)
     → MLP → ┬ 二分类头 (human/ai·focal loss 治不均衡)
              └ 可选质量回归头 (--quality-head·需质量标签·当前无→默认关)

【为什么这样】
  · encoder 抓词法/语义指纹；风格特征抓题材无关统计信号(burstiness/套话密度/标点分布)，
    二者互补 → 抗「只学作者身份/题材」的捷径(调研#1风险)。
  · focal loss(γ=2)+加权采样器处理类不均衡，**不丢训练数据**(respects 别抽样)。
  · 长文已在 data_prep 切成 ~480CJK chunk；推理时多 chunk logit 取均值聚合(见 eval.py)。

【超参】(调研默认·arXiv:2509.00731 / HFL)
  lr=2e-5 · batch=16 · max_len=512 · epochs=3 · fp16 · AdamW wd=0.01 · warmup=8%
  大模型(deberta-large/710M/bge-m3)→ 加 --lora(peft r=16 α=32)。

【依赖】torch(cu124)+transformers+datasets+peft+scikit-learn（见 requirements.txt）
【用法】
  python train.py --data-dir data --base hfl/chinese-macbert-base \
      --out-dir runs/macbert_v1 --use-feats --epochs 3 --fp16
  # 大模型 LoRA：--base IDEA-CCNL/Erlangshen-DeBERTa-v2-710M-Chinese --lora
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_ORDER, N_FEATURES  # noqa: E402  纯 stdlib·安全


# ============ 数据 ============

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def build_feat_scaler(records: list[dict]) -> dict:
    """从 train 算每维 mean/std（z-score 标准化·存盘供推理复用）。"""
    import statistics
    cols = {k: [] for k in FEATURE_ORDER}
    for r in records:
        f = r["feats"]
        for k in FEATURE_ORDER:
            cols[k].append(float(f.get(k, 0.0)))
    scaler = {}
    for k, vals in cols.items():
        mu = statistics.fmean(vals) if vals else 0.0
        sd = statistics.pstdev(vals) if len(vals) > 1 else 1.0
        scaler[k] = {"mean": mu, "std": sd if sd > 1e-6 else 1.0}
    return scaler


def feat_vec(rec: dict, scaler: dict) -> list[float]:
    f = rec["feats"]
    return [(float(f.get(k, 0.0)) - scaler[k]["mean"]) / scaler[k]["std"]
            for k in FEATURE_ORDER]


def make_torch_dataset(records, tokenizer, scaler, max_len):
    import torch
    from torch.utils.data import Dataset

    class DS(Dataset):
        def __init__(self, recs):
            self.recs = recs

        def __len__(self):
            return len(self.recs)

        def __getitem__(self, i):
            r = self.recs[i]
            enc = tokenizer(r["text"], truncation=True, max_length=max_len,
                            padding="max_length", return_tensors="pt")
            return {
                "input_ids": enc["input_ids"].squeeze(0),
                "attention_mask": enc["attention_mask"].squeeze(0),
                "feats": torch.tensor(feat_vec(r, scaler), dtype=torch.float),
                "label": torch.tensor(int(r["label"]), dtype=torch.long),
            }
    return DS(records)


# ============ 模型 ============

def build_model(base, use_feats, use_quality, lora):
    import torch
    import torch.nn as nn
    from transformers import AutoModel

    class AIToneClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = AutoModel.from_pretrained(base)
            if lora:
                from peft import LoraConfig, get_peft_model, TaskType
                cfg = LoraConfig(
                    task_type=TaskType.FEATURE_EXTRACTION, r=16, lora_alpha=32,
                    lora_dropout=0.05,
                    target_modules=["query", "key", "value", "dense"])
                self.encoder = get_peft_model(self.encoder, cfg)
            h = self.encoder.config.hidden_size
            in_dim = h + (N_FEATURES if use_feats else 0)
            self.use_feats = use_feats
            self.use_quality = use_quality
            self.dropout = nn.Dropout(0.1)
            self.trunk = nn.Sequential(nn.Linear(in_dim, 256), nn.GELU(),
                                       nn.Dropout(0.1))
            self.cls_head = nn.Linear(256, 2)
            if use_quality:
                self.reg_head = nn.Linear(256, 1)

        def _pool(self, last_hidden, mask):
            m = mask.unsqueeze(-1).float()
            summed = (last_hidden * m).sum(1)
            cnt = m.sum(1).clamp(min=1e-6)
            return summed / cnt  # masked mean-pool

        def forward(self, input_ids, attention_mask, feats=None):
            out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
            pooled = self._pool(out.last_hidden_state, attention_mask)
            rep = pooled
            if self.use_feats:
                rep = torch.cat([pooled, feats], dim=-1)
            rep = self.trunk(self.dropout(rep))
            logits = self.cls_head(rep)
            reg = self.reg_head(rep).squeeze(-1) if self.use_quality else None
            return logits, reg

    return AIToneClassifier()


def focal_loss(logits, target, gamma=2.0, alpha=None):
    """多类 focal loss（治类不均衡·Lin 2017）。alpha: 每类权重 tensor 或 None。"""
    import torch
    import torch.nn.functional as F
    logp = F.log_softmax(logits, dim=-1)
    p = logp.exp()
    logp_t = logp.gather(1, target.unsqueeze(1)).squeeze(1)
    p_t = p.gather(1, target.unsqueeze(1)).squeeze(1)
    loss = -((1 - p_t) ** gamma) * logp_t
    if alpha is not None:
        loss = loss * alpha.gather(0, target)
    return loss.mean()


# ============ 评估指标 ============

def eval_metrics(probs, labels):
    """probs: P(ai) 列表；labels: 0/1。返回 AUC/F1/PR-AUC/TPR@1%FPR/acc。"""
    from sklearn.metrics import (roc_auc_score, f1_score, average_precision_score,
                                 accuracy_score, roc_curve)
    import numpy as np
    probs = np.asarray(probs)
    labels = np.asarray(labels)
    pred = (probs >= 0.5).astype(int)
    out = {"acc": float(accuracy_score(labels, pred)),
           "f1": float(f1_score(labels, pred, zero_division=0))}
    if len(set(labels.tolist())) == 2:
        out["auc"] = float(roc_auc_score(labels, probs))
        out["pr_auc"] = float(average_precision_score(labels, probs))
        fpr, tpr, _ = roc_curve(labels, probs)
        idx = np.where(fpr <= 0.01)[0]
        out["tpr_at_1pct_fpr"] = float(tpr[idx[-1]]) if len(idx) else 0.0
    return out


# ============ 训练循环 ============

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--base", default="hfl/chinese-macbert-base")
    ap.add_argument("--out-dir", default="runs/macbert_v1")
    ap.add_argument("--use-feats", action="store_true", help="融合 24 维风格特征(抗捷径·推荐开)")
    ap.add_argument("--quality-head", action="store_true",
                    help="额外质量回归头·需 jsonl 带 quality 字段(当前无→默认关)")
    ap.add_argument("--lora", action="store_true", help="大模型用 LoRA(r=16)")
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--warmup-frac", type=float, default=0.08)
    ap.add_argument("--gamma", type=float, default=2.0, help="focal loss γ")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--weighted-sampler", action="store_true", default=True,
                    help="按类频率加权采样(治不均衡·不丢数据)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import torch
    import numpy as np
    from torch.utils.data import DataLoader, WeightedRandomSampler
    from transformers import AutoTokenizer, get_linear_schedule_with_warmup

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("[WARN] 未检测到 CUDA —— 本脚本设计在 GPU 上跑，CPU 仅供 smoke。", file=sys.stderr)

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_recs = load_jsonl(data_dir / "train.jsonl")
    val_recs = load_jsonl(data_dir / "val.jsonl")
    if not train_recs:
        print("[FATAL] train.jsonl 为空·先跑 data_prep.py", file=sys.stderr)
        sys.exit(2)

    scaler = build_feat_scaler(train_recs)
    (out_dir / "feat_scaler.json").write_text(
        json.dumps({"feature_order": FEATURE_ORDER, "scaler": scaler},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    train_ds = make_torch_dataset(train_recs, tokenizer, scaler, args.max_len)
    val_ds = make_torch_dataset(val_recs, tokenizer, scaler, args.max_len)

    # —— 不均衡：加权采样器 + focal alpha ——
    labels = [int(r["label"]) for r in train_recs]
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    cls_count = [max(1, n_neg), max(1, n_pos)]  # [human, ai]
    if args.weighted_sampler and n_pos and n_neg:
        w = [1.0 / cls_count[l] for l in labels]
        sampler = WeightedRandomSampler(w, num_samples=len(w), replacement=True)
        train_loader = DataLoader(train_ds, batch_size=args.batch, sampler=sampler)
    else:
        train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch)
    alpha = torch.tensor([len(labels) / (2 * c) for c in cls_count],
                         dtype=torch.float, device=device)

    model = build_model(args.base, args.use_feats, args.quality_head, args.lora).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=args.lr,
                              weight_decay=args.weight_decay)
    total_steps = (len(train_loader) // args.grad_accum) * args.epochs
    sched = get_linear_schedule_with_warmup(
        optim, int(total_steps * args.warmup_frac), total_steps)
    scaler_amp = torch.cuda.amp.GradScaler(enabled=args.fp16)

    best_key, best = "pr_auc", -1.0
    for epoch in range(args.epochs):
        model.train()
        optim.zero_grad()
        for step, batch in enumerate(train_loader):
            ids = batch["input_ids"].to(device)
            mask = batch["attention_mask"].to(device)
            feats = batch["feats"].to(device)
            y = batch["label"].to(device)
            with torch.cuda.amp.autocast(enabled=args.fp16):
                logits, _ = model(ids, mask, feats)
                loss = focal_loss(logits, y, args.gamma, alpha) / args.grad_accum
            scaler_amp.scale(loss).backward()
            if (step + 1) % args.grad_accum == 0:
                scaler_amp.unscale_(optim)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler_amp.step(optim)
                scaler_amp.update()
                optim.zero_grad()
                sched.step()
            if step % 50 == 0:
                print(f"ep{epoch} step{step}/{len(train_loader)} loss={loss.item()*args.grad_accum:.4f}",
                      file=sys.stderr)

        # —— val ——
        model.eval()
        probs, ys = [], []
        with torch.no_grad():
            for batch in val_loader:
                ids = batch["input_ids"].to(device)
                mask = batch["attention_mask"].to(device)
                feats = batch["feats"].to(device)
                with torch.cuda.amp.autocast(enabled=args.fp16):
                    logits, _ = model(ids, mask, feats)
                p = torch.softmax(logits, -1)[:, 1].float().cpu().numpy()
                probs.extend(p.tolist())
                ys.extend(batch["label"].tolist())
        m = eval_metrics(probs, ys)
        print(f"[val ep{epoch}] {json.dumps(m)}", file=sys.stderr)
        if m.get(best_key, m["f1"]) > best:
            best = m.get(best_key, m["f1"])
            torch.save(model.state_dict(), out_dir / "model.pt")
            (out_dir / "config.json").write_text(json.dumps({
                "base": args.base, "use_feats": args.use_feats,
                "quality_head": args.quality_head, "lora": args.lora,
                "max_len": args.max_len, "feature_order": FEATURE_ORDER,
                "best_val": m, "best_metric": best_key,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            tokenizer.save_pretrained(out_dir)
            print(f"  ↑ saved best ({best_key}={best:.4f})", file=sys.stderr)

    print(f"\n=== 训练完成·best {best_key}={best:.4f} → {out_dir} ===", file=sys.stderr)


if __name__ == "__main__":
    main()
