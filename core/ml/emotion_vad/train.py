# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""train.py — 中文 VAD 回归 fine-tune（torch + transformers）。

🔴 **写好别由本 agent 跑 GPU**（用户纪律：主代理集中跑）。本脚本就绪即可，跑法见文末/README。

设计（12GB RTX 4070Ti·fp16）：
  · 默认基座 hfl/chinese-roberta-wwm-ext（base ~102M）→ batch 32 轻松；large(325M) 用 batch 8 + 梯度累积
  · AMP fp16 + AdamW + linear warmup + 梯度裁剪
  · CCC+MSE 混合 loss（model.combined_loss）
  · 每 epoch 验证（Pearson/CCC per dim·见 eval.metrics）·best CCC 存 checkpoint
  · 确定性 seed·early stop
  · dominance 维：标签为 null 的样本按掩码忽略（中文 D 弱标签覆盖不全·见 README）

跑法（主代理·示例）：
  # base / VA / fp16
  python train.py --base hfl/chinese-roberta-wwm-ext --dims va --epochs 6 \
      --batch 32 --lr 2e-5 --fp16 --out checkpoints/va_base
  # large（12GB·梯度累积）
  python train.py --base hfl/chinese-roberta-wwm-ext-large --dims va --epochs 6 \
      --batch 8 --grad-accum 4 --lr 1e-5 --fp16 --max-len 256 --out checkpoints/va_large
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
    from transformers import AutoTokenizer, get_linear_schedule_with_warmup
except ImportError as e:  # pragma: no cover
    print("[FATAL] train.py 需要 torch+transformers+numpy（core/ml/.venv）。错误：%s" % e, file=sys.stderr)
    sys.exit(2)

from model import VADRegressor, combined_loss, DIM_SETS
from metrics import vad_metrics  # 复用 eval 的指标实现（单一真理源）

PROC = HERE / "data" / "processed"


def set_seed(seed: int):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class VADDataset(Dataset):
    def __init__(self, path: Path, tokenizer, dim_names, max_len: int):
        self.rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.tok = tokenizer
        self.dim_names = dim_names
        self.max_len = max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        enc = self.tok(r["text"], truncation=True, max_length=self.max_len,
                       padding="max_length", return_tensors="pt")
        targets, mask = [], []
        for d in self.dim_names:
            v = r.get(d)
            if v is None:
                targets.append(0.0); mask.append(0.0)   # 缺标签（如 D 未命中）→ 掩码忽略
            else:
                targets.append(float(v)); mask.append(1.0)
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "token_type_ids": enc.get("token_type_ids", torch.zeros_like(enc["input_ids"])).squeeze(0),
            "targets": torch.tensor(targets, dtype=torch.float),
            "label_mask": torch.tensor(mask, dtype=torch.float),
        }


def masked_loss(pred, targets, label_mask, alpha):
    """对有标签的维度算 combined_loss；某维度全 batch 无标签 → 跳过该维。"""
    # 逐维掩码：把无标签位置的 pred 对齐到 target（loss=0），但 CCC 需按维过滤
    total, logs = 0.0, {}
    n_used = 0
    for j in range(pred.shape[1]):
        m = label_mask[:, j] > 0.5
        if m.sum() < 2:   # CCC 需 ≥2 样本算方差
            continue
        p = pred[m, j:j + 1]
        t = targets[m, j:j + 1]
        l, d = combined_loss(p, t, alpha)
        total = total + l
        n_used += 1
        logs[f"dim{j}"] = d
    if n_used == 0:
        return None, logs
    return total / n_used, logs


def evaluate(model, loader, device, dim_names):
    model.eval()
    preds = {d: [] for d in dim_names}
    golds = {d: [] for d in dim_names}
    with torch.no_grad():
        for b in loader:
            out = model(b["input_ids"].to(device), b["attention_mask"].to(device),
                        b["token_type_ids"].to(device))
            out = out.cpu().numpy()
            tg = b["targets"].numpy(); mk = b["label_mask"].numpy()
            for j, d in enumerate(dim_names):
                for i in range(out.shape[0]):
                    if mk[i, j] > 0.5:
                        preds[d].append(float(out[i, j])); golds[d].append(float(tg[i, j]))
    return vad_metrics(preds, golds)


def main():
    ap = argparse.ArgumentParser(description="中文 VAD 回归 fine-tune（GPU·主代理跑）")
    ap.add_argument("--base", default="hfl/chinese-roberta-wwm-ext")
    ap.add_argument("--dims", default="va", choices=list(DIM_SETS))
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--alpha", type=float, default=0.5, help="loss = alpha·CCC + (1-alpha)·MSE")
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--patience", type=int, default=2, help="val CCC 不升的 early-stop 容忍 epoch 数")
    ap.add_argument("--out", default="checkpoints/va_base")
    args = ap.parse_args()

    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu":
        print("[WARN] 未检测到 CUDA·将在 CPU 跑（极慢·仅冒烟）。生产请用 GPU。", file=sys.stderr)

    dim_names = DIM_SETS[args.dims]
    tok = AutoTokenizer.from_pretrained(args.base)
    model = VADRegressor(base_model=args.base, dims=args.dims).to(device)

    train_ds = VADDataset(PROC / "sentence_train.jsonl", tok, dim_names, args.max_len)
    val_ds = VADDataset(PROC / "sentence_val.jsonl", tok, dim_names, args.max_len)
    train_ld = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=0, drop_last=True)
    val_ld = DataLoader(val_ds, batch_size=args.batch, shuffle=False, num_workers=0)

    no_decay = ["bias", "LayerNorm.weight"]
    params = [
        {"params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         "weight_decay": args.weight_decay},
        {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
         "weight_decay": 0.0},
    ]
    opt = torch.optim.AdamW(params, lr=args.lr)
    total_steps = (len(train_ld) // args.grad_accum) * args.epochs
    sched = get_linear_schedule_with_warmup(opt, int(total_steps * args.warmup_ratio), total_steps)
    use_amp = args.fp16 and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    best_ccc, best_epoch, bad = -1.0, -1, 0
    out_dir = HERE / args.out
    history = []
    for ep in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        run_loss, steps = 0.0, 0
        for it, b in enumerate(train_ld):
            with torch.amp.autocast("cuda", enabled=use_amp):
                pred = model(b["input_ids"].to(device), b["attention_mask"].to(device),
                             b["token_type_ids"].to(device))
                loss, _ = masked_loss(pred, b["targets"].to(device), b["label_mask"].to(device), args.alpha)
                if loss is None:
                    continue
                loss = loss / args.grad_accum
            scaler.scale(loss).backward()
            if (it + 1) % args.grad_accum == 0:
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt); scaler.update(); sched.step(); opt.zero_grad()
            run_loss += float(loss) * args.grad_accum; steps += 1
        metrics = evaluate(model, val_ld, device, dim_names)
        mean_ccc = float(np.mean([metrics[d]["ccc"] for d in dim_names]))
        print(f"[epoch {ep}] train_loss={run_loss / max(1, steps):.4f}  val_meanCCC={mean_ccc:.4f}  "
              + "  ".join(f"{d}:CCC={metrics[d]['ccc']:.3f}/r={metrics[d]['pearson']:.3f}" for d in dim_names))
        history.append({"epoch": ep, "train_loss": run_loss / max(1, steps), "val": metrics, "mean_ccc": mean_ccc})
        if mean_ccc > best_ccc:
            best_ccc, best_epoch, bad = mean_ccc, ep, 0
            model.save(str(out_dir), tokenizer=tok,
                       extra_meta={"best_epoch": ep, "best_val_mean_ccc": mean_ccc,
                                   "train_args": vars(args)})
            print(f"  ↑ best (meanCCC={mean_ccc:.4f}) → {out_dir}")
        else:
            bad += 1
            if bad >= args.patience:
                print(f"[early-stop] val CCC {args.patience} epoch 未升·停于 epoch {ep}（best={best_epoch}）")
                break
    (out_dir / "train_history.json").write_text(
        json.dumps({"history": history, "best_epoch": best_epoch, "best_val_mean_ccc": best_ccc},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[DONE] best epoch={best_epoch} meanCCC={best_ccc:.4f}·checkpoint={out_dir}")
    print("[NEXT] python eval.py --ckpt %s" % args.out)


if __name__ == "__main__":
    main()
