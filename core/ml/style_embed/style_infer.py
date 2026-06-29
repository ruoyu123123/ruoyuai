#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN风格声纹集成
"""style_infer.py — 批量编码桥（venv py3.10 + torch 侧 · 被系统 py3.14 经 subprocess 调用）

若渝主流水线跑**系统 Python 3.14（无 torch）**，fine-tune 风格/声纹模型只能在
**venv Python 3.10（torch CUDA）** 加载。本脚本是两进程隔离的「编码桥」：
读 jsonl（每行 {"text": ...}）→ SentenceTransformer 编码 → jsonl（每行 {"embedding": [...]}）。
由 `core/scripts/embedding_store.py::ruoyu_style_encode_batch` 经 subprocess 调用。

确定性（同输入同输出）：
  · model.eval() + torch.no_grad（SentenceTransformer.encode 内置）
  · normalize_embeddings=True（L2 归一化·cosine 即点积）
  · 固定 batch_size · 固定 device 顺序 · 输出 float 截 6 位小数

用法（一般不手调·由桥调用）：
  python style_infer.py --model <SentenceTransformer 目录> --input in.jsonl --output out.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="风格/声纹嵌入批量编码桥（venv 侧）")
    ap.add_argument("--model", required=True, help="SentenceTransformer 模型目录")
    ap.add_argument("--input", required=True, help="输入 jsonl（每行 {\"text\": ...}）")
    ap.add_argument("--output", required=True, help="输出 jsonl（每行 {\"embedding\": [...]}）")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--max-cjk", type=int, default=8000,
                    help="单条编码前截断的最大字符数（与 embedding_store 现有 [:8000] 一致）")
    args = ap.parse_args()

    mp = Path(args.model)
    if not mp.exists():
        print(f"[FATAL] 模型目录不存在: {mp}", file=sys.stderr)
        return 2

    texts: list[str] = []
    try:
        with open(args.input, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                texts.append(str(json.loads(line).get("text", ""))[: args.max_cjk])
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 读取输入失败: {e}", file=sys.stderr)
        return 2

    if not texts:
        Path(args.output).write_text("", encoding="utf-8")
        return 0

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 无法 import sentence_transformers（venv 缺 torch?）: {e}", file=sys.stderr)
        return 2

    try:
        model = SentenceTransformer(str(mp))
        model.eval()  # 确定性：关 dropout 等
        embs = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=args.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 编码失败: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.output, "w", encoding="utf-8") as fh:
            for e in embs:
                fh.write(json.dumps(
                    {"embedding": [round(float(x), 6) for x in e]},
                    ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 写出失败: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
