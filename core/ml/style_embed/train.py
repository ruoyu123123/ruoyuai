#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:风格声纹嵌入
"""train.py — 对比学习 fine-tune 风格/声纹嵌入模型(torch + sentence-transformers)

🔴 本脚本写好但**不在子代理里跑 GPU**(主代理集中跑单卡 RTX 4070Ti 12GB)。
   data_prep.py 产数据后, 主代理用 venv python 跑本脚本即可。怎么跑见文件尾 + README.md。

== SOTA 接地与设计抉择 ==
· UAR/LUAR(EMNLP2021)/DramaCV(2406.11368): 同作者(同角色)多文档对比, 监督对比目标。
· SupCon(2004.11362): 多正例监督对比 → 同 label 全为正例, 不像 InfoNCE 只认 1 个正例。
· 🔴 抉择: 我们只有 8-10 位作者(class 极少) → MNRL/InfoNCE 的 in-batch negative 里
  必然撞上"同作者却被当负例"的 false negative(8 个作者, batch 64 必撞)。因此
  **作者级默认用 label-based 监督对比(BatchAllTripletLoss = ST 里的 SupCon 等价物)**,
  它把同 label 全当正例, 无 false-negative 问题。MNRL 仅在 class 多的场景
  (角色级 245 类 / 将来作者扩到几百位)才推荐。
· StyleDistance/mstyledistance(2410.12757/2502.15168): content-independent 风格嵌入
  (合成平行样本对比·40 风格特征·多语含中文) → 作为**基座**最对口"风格(非语义)"。
  同作者跨题材正例会进一步把模型推向**题材无关的作者风格信号**(去题材, 北极星④)。

== 12GB 显存 ==
· 基座 sub-1B(mstyledistance ~XLM-R-base / bge-base-zh 102M 级) → fp16/bf16 全量 fine-tune 可行。
· batch 调到不 OOM(默认 48·grad_accum 放大有效 batch → in-batch 对比更稳)·max_seq 384。
· --lora 走 peft(更省显存/更快·略弱)·全量 fine-tune 是首选。

用法见文件尾。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def _require_deps():
    try:
        import torch  # noqa
        import datasets  # noqa
        import sentence_transformers  # noqa
    except ImportError as e:
        sys.exit(f"[FATAL] 缺依赖({e}). 用 venv 装: "
                 f"core/ml/.venv/Scripts/python.exe -m pip install "
                 f"'sentence-transformers>=3.0' datasets peft accelerate")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"[FATAL] 缺数据 {path} — 先跑 data_prep.py")
    return [json.loads(l) for l in path.open(encoding="utf-8")]


# ── 构建训练 Dataset ─────────────────────────────────────────────────────────
def build_label_dataset(task: str):
    """label-based 监督对比(BatchAllTripletLoss/SupCon): columns = (sentence, label)。"""
    from datasets import Dataset
    if task == "author":
        rows = load_jsonl(DATA / "author_chunks_train.jsonl")
        sents = [r["text"] for r in rows]
        labels = [int(r["author_id"]) for r in rows]
    else:  # character (phase2): 每角色台词切成多个 sub-view(≈DramaCV 采样 8 句成 1 view)
        rows = load_jsonl(DATA / "character_dialogues.jsonl")
        label_map, sents, labels = {}, [], []
        for r in rows:
            lab = label_map.setdefault(r["label"], len(label_map))
            utts = r["text"].split("\n")
            view = 16  # 每 16 句拼一个 view(多 view → 同角色多正例)
            for i in range(0, len(utts), view):
                chunk = "\n".join(utts[i:i + view])
                if len(chunk) >= 30:
                    sents.append(chunk)
                    labels.append(lab)
    n_classes = len(set(labels))
    print(f"[data] task={task} samples={len(sents)} classes={n_classes}")
    return Dataset.from_dict({"sentence": sents, "label": labels}), n_classes


def build_pairs_dataset():
    """MNRL: columns = (anchor, positive)。"""
    from datasets import Dataset
    rows = load_jsonl(DATA / "author_pairs_train.jsonl")
    print(f"[data] MNRL pairs={len(rows)}")
    return Dataset.from_dict({"anchor": [r["anchor"] for r in rows],
                              "positive": [r["positive"] for r in rows]})


# ── 训练时验证: 同/异作者对的二分类 AUC(cosine)─────────────────────────────
def build_val_evaluator(max_pairs: int = 4000, seed: int = 42):
    import random
    from sentence_transformers.evaluation import BinaryClassificationEvaluator
    rows = load_jsonl(DATA / "author_chunks_val.jsonl")
    by_author: dict[int, list[str]] = {}
    for r in rows:
        by_author.setdefault(int(r["author_id"]), []).append(r["text"])
    rng = random.Random(seed)
    s1, s2, lab = [], [], []
    authors = list(by_author)
    half = max_pairs // 2
    for _ in range(half):  # 正例: 同作者
        a = rng.choice(authors)
        if len(by_author[a]) < 2:
            continue
        x, y = rng.sample(by_author[a], 2)
        s1.append(x); s2.append(y); lab.append(1)
    for _ in range(half):  # 负例: 异作者
        a, b = rng.sample(authors, 2)
        s1.append(rng.choice(by_author[a])); s2.append(rng.choice(by_author[b])); lab.append(0)
    return BinaryClassificationEvaluator(s1, s2, lab, name="author_verif",
                                         show_progress_bar=False)


def main():
    ap = argparse.ArgumentParser(description="对比学习 fine-tune 风格/声纹嵌入")
    ap.add_argument("--base-model", default="StyleDistance/mstyledistance",
                    help="基座(默认 mstyledistance·风格最对口; 备选 BAAI/bge-base-zh-v1.5)")
    ap.add_argument("--task", choices=["author", "character"], default="author")
    ap.add_argument("--loss", choices=["batch-all-triplet", "batch-hard-triplet", "mnrl"],
                    default="batch-all-triplet",
                    help="少作者(8-10)默认 batch-all-triplet(SupCon 等价·无 false-neg); "
                         "多类/角色级可用 mnrl")
    ap.add_argument("--output", default=str(HERE / "runs" / "style_embed_v1"))
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--batch-size", type=int, default=48, help="12GB 起点·OOM 就降到 32/24")
    ap.add_argument("--grad-accum", type=int, default=2, help="放大有效 batch(对比学习受益)")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--max-seq", type=int, default=384, help="chunk 512CJK≈token, 384 省显存")
    ap.add_argument("--margin", type=float, default=5.0, help="triplet margin")
    ap.add_argument("--precision", choices=["fp16", "bf16", "fp32"], default="bf16")
    ap.add_argument("--lora", action="store_true", help="走 peft LoRA(更省显存/更快·略弱)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    _require_deps()
    import torch
    from sentence_transformers import (SentenceTransformer, losses,
                                       SentenceTransformerTrainer,
                                       SentenceTransformerTrainingArguments)
    from sentence_transformers.training_args import BatchSamplers

    if not torch.cuda.is_available():
        print("[WARN] 未检测到 CUDA — 本脚本设计在 RTX 4070Ti 单卡跑。CPU 会极慢。",
              file=sys.stderr)

    # 1. 基座
    print(f"[model] 加载基座 {args.base_model}")
    model = SentenceTransformer(args.base_model)
    model.max_seq_length = args.max_seq

    if args.lora:
        from peft import LoraConfig
        # ST 5.x: model.add_adapter; 旧版用 peft 包裹 auto_model。优先 add_adapter。
        lcfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                          target_modules=["query", "key", "value", "dense"])
        try:
            model.add_adapter(lcfg)
            print("[model] LoRA 适配器已挂载")
        except Exception as e:
            print(f"[WARN] add_adapter 失败({e}) → 退回全量 fine-tune", file=sys.stderr)

    # 2. 数据 + loss
    if args.loss == "mnrl":
        train_ds = build_pairs_dataset()
        loss = losses.MultipleNegativesRankingLoss(model)
        batch_sampler = BatchSamplers.NO_DUPLICATES  # 防同 batch 重复 → 减 false-neg
    else:
        train_ds, n_classes = build_label_dataset(args.task)
        if args.loss == "batch-all-triplet":
            loss = losses.BatchAllTripletLoss(model, margin=args.margin)
        else:
            loss = losses.BatchHardTripletLoss(model, margin=args.margin)
        batch_sampler = BatchSamplers.GROUP_BY_LABEL  # 每 batch 多 label·多正例(SupCon 必需)

    evaluator = build_val_evaluator()
    # best-model 指标键随 ST 版本变 → 从 evaluator.primary_metric 动态取(防硬编码崩)
    best_metric = None
    pm = getattr(evaluator, "primary_metric", None)
    if pm:
        best_metric = pm if pm.startswith("eval_") else f"eval_{pm}"

    # 3. 训练参数(12GB 友好)
    targs = SentenceTransformerTrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        fp16=(args.precision == "fp16"),
        bf16=(args.precision == "bf16"),
        batch_sampler=batch_sampler,
        eval_strategy="steps", eval_steps=500,
        save_strategy="steps", save_steps=500, save_total_limit=2,
        logging_steps=50,
        load_best_model_at_end=bool(best_metric),
        metric_for_best_model=best_metric,  # 动态自 evaluator.primary_metric
        greater_is_better=True,
        seed=args.seed,
        dataloader_num_workers=2,
        gradient_checkpointing=True,  # 省显存(略慢) → 12GB 稳
        report_to=[],
    )

    trainer = SentenceTransformerTrainer(
        model=model, args=targs, train_dataset=train_ds,
        loss=loss, evaluator=evaluator)
    print(f"[train] 开始 · loss={args.loss} · sampler={batch_sampler} · "
          f"precision={args.precision} · effective_batch="
          f"{args.batch_size * args.grad_accum}")
    trainer.train()

    # 4. 保存(含 EMBED_BACKEND=ruoyu_style 接入用的最终权重)
    final = Path(args.output) / "final"
    model.save(str(final))
    (final / "ruoyu_meta.json").write_text(json.dumps({
        "_marker": "🔴 2026-06-29 NN训练:风格声纹嵌入",
        "base_model": args.base_model, "task": args.task, "loss": args.loss,
        "dim": model.get_sentence_embedding_dimension(),
        "max_seq": args.max_seq, "epochs": args.epochs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] 模型已存 {final} (dim={model.get_sentence_embedding_dimension()})")
    print("       接入: 见 INTEGRATION.md (EMBED_BACKEND=ruoyu_style)")


if __name__ == "__main__":
    main()

# ── 怎么跑(主代理单卡 RTX 4070Ti 12GB)──────────────────────────────────────
#  作者级(默认·SFS 用):
#    core/ml/.venv/Scripts/python.exe core/ml/style_embed/train.py \
#        --base-model StyleDistance/mstyledistance --task author \
#        --loss batch-all-triplet --batch-size 48 --epochs 3 --precision bf16
#  OOM → 降 --batch-size 32 --grad-accum 4 或加 --lora; 或换 --base-model BAAI/bge-base-zh-v1.5
#  角色级(phase2·千人千面 用):
#    ...train.py --task character --loss batch-all-triplet --batch-size 32 --epochs 5
#  评估: core/ml/style_embed/eval.py --model runs/style_embed_v1/final
