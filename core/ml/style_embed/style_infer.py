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


def load_model(model_dir):
    """加载 SentenceTransformer 模型（venv 侧）。

    🔴 2026-07-03 Wave-5 常驻 daemon 集成 — 供 `core/ml/daemon/model_daemon.py` 首次请求时
    调用一次并常驻缓存复用（取代每次 subprocess 都重新加载）；main() CLI 路径仍每次独立调用一次，
    行为与重构前逐字节一致。
    """
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(model_dir))
    model.eval()  # 确定性：关 dropout 等
    return model


def encode_with_model(model, texts, batch_size=32, max_cjk=8000):
    """已加载模型 → 批量编码 texts → L2 归一化 embedding（6 位小数·与 main() 原输出格式一致）。

    daemon 侧（模型常驻）与 CLI 侧（main() 每次独立加载）共用本函数，确保两条路径编码逻辑
    单一真理源、不会因为重构分叉出不同数值。
    """
    truncated = [str(t)[:max_cjk] for t in texts]
    embs = model.encode(
        truncated,
        normalize_embeddings=True,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [[round(float(x), 6) for x in e] for e in embs]


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
        from sentence_transformers import SentenceTransformer  # noqa: F401  保留原始 import 探测（错误信息逐字节不变）
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 无法 import sentence_transformers（venv 缺 torch?）: {e}", file=sys.stderr)
        return 2

    try:
        model = load_model(mp)
        embs = encode_with_model(model, texts, batch_size=args.batch_size, max_cjk=args.max_cjk)
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 编码失败: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.output, "w", encoding="utf-8") as fh:
            for e in embs:
                fh.write(json.dumps({"embedding": e}, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] 写出失败: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
