# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""train.py — 中文连贯性二分类 fine-tune（torch + transformers）。

🔴 **写好别由本 agent 跑 GPU**（用户纪律：主代理集中跑）。本脚本就绪即可，跑法见文末。

设计（12GB RTX 4070Ti·bf16）：
  · 默认基座 hfl/chinese-roberta-wwm-ext（base ~102M）→ batch 16 + gradient_checkpointing
  · AMP bf16 + AdamW + linear warmup + cosine decay + 梯度裁剪
  · Focal Loss（model.focal_loss·治正负样本不均衡）
  · 每 epoch 验证（Accuracy/Precision/Recall/F1）·best F1 存 checkpoint
  · 确定性 seed · early stop
  · num_workers=0（Windows 兼容）

跑法（主代理·示例）：
  # base / bf16
  python train.py --data-dir data --output runs/coherence_v1 --epochs 3 --batch-size 16 --lr 2e-5 --bf16
  # fp16
  python train.py --data-dir data --output runs/coherence_v1 --epochs 3 --batch-size 16 --lr 2e-5 --fp16
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import numpy as np
    import torch
    from torch.utils.data import Dataset, DataLoader
    from transformers import AutoTokenizer
    try:  # cosine decay 优先；老 transformers 无 cosine → 退线性 warmup（仍保留 warmup）
        from transformers import get_cosine_schedule_with_warmup as make_scheduler
        _SCHED_NAME = "cosine"
    except ImportError:
        from transformers import get_linear_schedule_with_warmup as make_scheduler
        _SCHED_NAME = "linear"
except ImportError as e:  # pragma: no cover
    print("[FATAL] train.py 需要 torch+transformers+numpy（core/ml/.venv）。错误：%s" % e, file=sys.stderr)
    sys.exit(2)

from model import CoherenceClassifier, focal_loss, bce_loss


def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# ----------------------------------------------------------------------------- metrics（纯 Python·零外部依赖）
def _binary_metrics(preds: list[int], golds: list[int]) -> dict:
    """手算二分类 Accuracy / Precision / Recall / F1（无 sklearn 依赖）。"""
    n = len(preds)
    if n == 0:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "n": 0}
    tp = sum(1 for p, g in zip(preds, golds) if p == 1 and g == 1)
    fp = sum(1 for p, g in zip(preds, golds) if p == 1 and g == 0)
    fn = sum(1 for p, g in zip(preds, golds) if p == 0 and g == 1)
    correct = sum(1 for p, g in zip(preds, golds) if p == g)
    acc = correct / n
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return {"accuracy": round(acc, 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4), "n": n}


def _spearman(preds_prob: list[float], golds: list[int]) -> float | None:
    """可选 Spearman 相关（scipy 缺失 → None）。"""
    try:
        from scipy.stats import spearmanr
        corr, _ = spearmanr(preds_prob, golds)
        return round(float(corr), 4) if corr == corr else None  # nan check
    except ImportError:
        return None


# ----------------------------------------------------------------------------- dataset
class CoherenceDataset(Dataset):
    def __init__(self, path: Path, tokenizer, max_len: int):
        self.rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.tok = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        enc = self.tok(r["text"], truncation=True, max_length=self.max_len,
                       padding="max_length", return_tensors="pt")
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "token_type_ids": enc.get("token_type_ids", torch.zeros_like(enc["input_ids"])).squeeze(0),
            "label": torch.tensor(int(r["label"]), dtype=torch.long),
        }


# ----------------------------------------------------------------------------- evaluate
def evaluate(model, loader, device):
    """验证集评估 → binary metrics + 可选 spearman。"""
    model.eval()
    all_preds: list[int] = []
    all_golds: list[int] = []
    all_probs: list[float] = []
    with torch.no_grad():
        for b in loader:
            logits = model(b["input_ids"].to(device), b["attention_mask"].to(device),
                           b["token_type_ids"].to(device))
            probs = torch.softmax(logits, dim=-1)[:, 1].cpu().tolist()
            preds = logits.argmax(dim=-1).cpu().tolist()
            golds = b["label"].tolist()
            all_preds.extend(preds)
            all_golds.extend(golds)
            all_probs.extend(probs)
    metrics = _binary_metrics(all_preds, all_golds)
    metrics["spearman"] = _spearman(all_probs, all_golds)
    return metrics


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="中文连贯性二分类 fine-tune（GPU·主代理跑）")
    ap.add_argument("--base", default="hfl/chinese-roberta-wwm-ext")
    ap.add_argument("--data-dir", default="data", help="含 train.jsonl + val.jsonl")
    ap.add_argument("--output", default="runs/coherence_v1")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--max-len", type=int, default=512, help="max token 长（5-8 段连贯文本需要较长）")
    ap.add_argument("--focal-alpha", type=float, default=0.25, help="focal loss alpha")
    ap.add_argument("--focal-gamma", type=float, default=2.0, help="focal loss gamma")
    ap.add_argument("--loss", default="focal", choices=["focal", "bce"], help="loss 函数")
    ap.add_argument("--bf16", action="store_true", help="bf16 训练（推荐）")
    ap.add_argument("--fp16", action="store_true", help="fp16 训练（备选）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--patience", type=int, default=2, help="val F1 不升的 early-stop 容忍 epoch 数")
    ap.add_argument("--gradient-checkpointing", action="store_true", default=True,
                    help="梯度检查点（省显存·默认开）")
    args = ap.parse_args()

    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu":
        print("[WARN] 未检测到 CUDA·将在 CPU 跑（极慢·仅冒烟）。生产请用 GPU。", file=sys.stderr)

    tok = AutoTokenizer.from_pretrained(args.base)
    model = CoherenceClassifier(base_model=args.base).to(device)

    # 梯度检查点（省显存·RTX 4070Ti 12GB 友好）
    if args.gradient_checkpointing:
        model.encoder.gradient_checkpointing_enable()
        print("[INFO] gradient_checkpointing 已启用", file=sys.stderr)

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = HERE / data_dir

    train_ds = CoherenceDataset(data_dir / "train.jsonl", tok, args.max_len)
    val_ds = CoherenceDataset(data_dir / "val.jsonl", tok, args.max_len)
    train_ld = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                          num_workers=0, drop_last=True)
    val_ld = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    print(f"[INFO] train={len(train_ds)} val={len(val_ds)} base={args.base} sched={_SCHED_NAME} "
          f"lr={args.lr} batch={args.batch_size} epochs={args.epochs}", file=sys.stderr)

    # optimizer
    no_decay = ["bias", "LayerNorm.weight"]
    params = [
        {"params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         "weight_decay": args.weight_decay},
        {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
         "weight_decay": 0.0},
    ]
    opt = torch.optim.AdamW(params, lr=args.lr)
    total_steps = (len(train_ld) // args.grad_accum) * args.epochs
    sched = make_scheduler(opt, int(total_steps * args.warmup_ratio), total_steps)

    # AMP 设置
    use_bf16 = args.bf16 and device.type == "cuda"
    use_fp16 = args.fp16 and device.type == "cuda" and not use_bf16
    amp_dtype = torch.bfloat16 if use_bf16 else (torch.float16 if use_fp16 else None)
    use_amp = amp_dtype is not None
    scaler = torch.amp.GradScaler("cuda", enabled=use_fp16)  # bf16 不需要 scaler
    if use_amp:
        print(f"[INFO] AMP 启用: dtype={amp_dtype}", file=sys.stderr)

    # loss 函数
    loss_fn = focal_loss if args.loss == "focal" else bce_loss
    loss_kwargs = {"alpha": args.focal_alpha, "gamma": args.focal_gamma} if args.loss == "focal" else {}

    best_f1, best_epoch, bad = -1.0, -1, 0
    out_dir = HERE / args.output if not Path(args.output).is_absolute() else Path(args.output)
    history = []

    for ep in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        run_loss, steps = 0.0, 0

        for it, b in enumerate(train_ld):
            with torch.amp.autocast("cuda", enabled=use_amp, dtype=amp_dtype or torch.float32):
                logits = model(b["input_ids"].to(device), b["attention_mask"].to(device),
                               b["token_type_ids"].to(device))
                loss = loss_fn(logits, b["label"].to(device), **loss_kwargs)
                loss = loss / args.grad_accum

            if use_fp16:
                scaler.scale(loss).backward()
            else:
                loss.backward()

            if (it + 1) % args.grad_accum == 0:
                if use_fp16:
                    scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                if use_fp16:
                    scaler.step(opt)
                    scaler.update()
                else:
                    opt.step()
                sched.step()
                opt.zero_grad()

            run_loss += float(loss) * args.grad_accum
            steps += 1

        metrics = evaluate(model, val_ld, device)
        avg_loss = run_loss / max(1, steps)
        f1 = metrics["f1"]
        spearman_str = f"spearman={metrics['spearman']}" if metrics.get("spearman") is not None else ""
        print(f"[epoch {ep}] train_loss={avg_loss:.4f}  val_acc={metrics['accuracy']:.4f}  "
              f"val_P={metrics['precision']:.4f}  val_R={metrics['recall']:.4f}  "
              f"val_F1={f1:.4f}  {spearman_str}")
        history.append({"epoch": ep, "train_loss": avg_loss, "val": metrics, "f1": f1})

        if f1 > best_f1:
            best_f1, best_epoch, bad = f1, ep, 0
            model.save(str(out_dir), tokenizer=tok,
                       extra_meta={"best_epoch": ep, "best_val_f1": f1,
                                   "best_val_metrics": metrics,
                                   "train_args": vars(args)})
            print(f"  ↑ best (F1={f1:.4f}) → {out_dir}")
        else:
            bad += 1
            if bad >= args.patience:
                print(f"[early-stop] val F1 {args.patience} epoch 未升·停于 epoch {ep}（best={best_epoch}）")
                break

    (out_dir / "train_history.json").write_text(
        json.dumps({"history": history, "best_epoch": best_epoch, "best_val_f1": best_f1},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[DONE] best epoch={best_epoch} F1={best_f1:.4f}·checkpoint={out_dir}")


if __name__ == "__main__":
    main()
