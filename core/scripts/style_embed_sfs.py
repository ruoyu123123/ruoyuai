#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN风格声纹集成
"""style_embed_sfs.py — embedding-SFS（作者级 NN 风格保真度·SFS 落点①）

定义（见 core/ml/style_embed/INTEGRATION.md §1）：
    embedding_SFS(gen) = cosine( centroid(chunks(gen)),  author_centroid )
    author_centroid    = normalize( mean( embed(原文 chunk_i) ) )   # 缓存

与 style_evaluator 的**启发式** sfs_quick **并存对比**（shadow·不替换·不改判决）。
NN 风格向量学的是「这是不是同一作者写的」——与北极星「写出和作者风格一致的文章」同构，
比启发式只比「句长/段长分布像不像」更贴整体笔触神似。

🔴 默认安全：本脚本跑在**系统 py3.14（无 torch）**，编码经 `embedding_store.ruoyu_style_encode_batch`
   走 venv py3.10 subprocess 桥。venv/模型/torch 缺、桥失败 → available=False · embedding_sfs=None ·
   **绝不崩**（调用方当 shadow 信号缺省处理）。全 advisory · 绝不进 hard_gate（北极星⑤）。

确定性：model.eval()+L2 归一化（桥侧）+ 固定分块（data_prep chunk 协议）+ centroid 缓存带
   method/dim 指纹（不符则重算）。

用法：
  python style_embed_sfs.py --gen <复刻/生成文> --ref-dir workspace/styles/<书名>/原文
  python style_embed_sfs.py --gen <文> --author <书名>          # 自动定位 原文 目录 + 缓存 centroid
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_REPO_ROOT = _HERE.parents[1]
_STYLE_EMBED_DIR = _REPO_ROOT / "core" / "ml" / "style_embed"


# ── 分块协议（复用 data_prep · 与训练切分一致 · 纯 stdlib）─────────────────────
def _load_chunker():
    """优先复用 data_prep 的确定性分块（chunk_size=512/min_chunk=256·与训练一致）。
    import 失败（路径异常等）→ 内联兜底（同算法）。"""
    try:
        if str(_STYLE_EMBED_DIR) not in sys.path:
            sys.path.insert(0, str(_STYLE_EMBED_DIR))
        from data_prep import clean_chapter, chunk_by_paragraph, cjk_count  # type: ignore
        return clean_chapter, chunk_by_paragraph, cjk_count
    except Exception:
        import re as _re
        _CJK = _re.compile(r"[一-鿿]")
        _TITLE = _re.compile(r"^\s*第[0-9一二三四五六七八九十百千零两〇]+[章卷节回篇]")

        def cjk_count(t: str) -> int:
            return len(_CJK.findall(t))

        def clean_chapter(text: str) -> str:
            out = []
            for i, ln in enumerate(text.splitlines()):
                s = ln.replace("　", "").strip()
                if not s:
                    continue
                if i < 3 and _TITLE.match(s):
                    continue
                out.append(s)
            return "\n".join(out)

        def chunk_by_paragraph(text: str, chunk_size: int, min_chunk: int) -> list:
            paras = [p for p in text.split("\n") if p.strip()]
            windows, buf, buf_cjk = [], [], 0
            for p in paras:
                buf.append(p)
                buf_cjk += cjk_count(p)
                if buf_cjk >= chunk_size:
                    windows.append("\n".join(buf))
                    buf, buf_cjk = [], 0
            if buf:
                tail = "\n".join(buf)
                if cjk_count(tail) < min_chunk and windows:
                    windows[-1] = windows[-1] + "\n" + tail
                else:
                    windows.append(tail)
            return [w for w in windows if cjk_count(w) >= min_chunk]

        return clean_chapter, chunk_by_paragraph, cjk_count


def _chunks(text: str, chunk_size: int = 512, min_chunk: int = 256) -> list:
    clean_chapter, chunk_by_paragraph, cjk_count = _load_chunker()
    body = clean_chapter(text or "")
    chs = chunk_by_paragraph(body, chunk_size, min_chunk)
    if not chs and cjk_count(body) > 0:
        chs = [body]  # 太短整体当一块（不空转）
    return chs


# ── 向量工具（纯 python·不依赖 numpy）─────────────────────────────────────────
def _normalize(v: list) -> list:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else list(v)


def _centroid(vecs: list) -> "list | None":
    """mean(归一化向量) 再归一化。空 → None。"""
    if not vecs:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        nv = _normalize(v)
        for i in range(dim):
            acc[i] += nv[i]
    acc = [x / len(vecs) for x in acc]
    return _normalize(acc)


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ── 作者 centroid 缓存 ───────────────────────────────────────────────────────
def _author_centroid_cache_path(author: str) -> Path:
    return _REPO_ROOT / "workspace" / "styles" / author / ".style_centroid.json"


def _ref_texts_for_author(author: str, max_files: int = 200) -> list:
    ref_dir = _REPO_ROOT / "workspace" / "styles" / author / "原文"
    if not ref_dir.is_dir():
        return []
    files = sorted(ref_dir.glob("*.txt"))[:max_files]
    return [f.read_text(encoding="utf-8", errors="replace") for f in files]


def _build_author_centroid(ref_texts: list, model: str = "author"):
    """ref 原文 → chunk → 编码 → centroid。返回 (centroid|None, method, n_ref_chunks)。"""
    from embedding_store import ruoyu_style_encode_batch, ruoyu_style_dim
    ref_chunks = []
    for t in ref_texts:
        ref_chunks.extend(_chunks(t))
    if not ref_chunks:
        return None, None, 0
    embs = ruoyu_style_encode_batch(ref_chunks, model=model)
    if not embs:
        return None, None, 0
    method = f"ruoyu_style:{model}:dim{ruoyu_style_dim(model)}"
    return _centroid(embs), method, len(ref_chunks)


def _load_or_build_author_centroid(author: str, model: str = "author"):
    """带缓存（method/dim 指纹不符则重算）。返回 (centroid|None, method, n_ref_chunks)。"""
    from embedding_store import ruoyu_style_dim
    cur_method = f"ruoyu_style:{model}:dim{ruoyu_style_dim(model)}"
    cache = _author_centroid_cache_path(author)
    if cache.exists():
        try:
            rec = json.loads(cache.read_text(encoding="utf-8"))
            if rec.get("method") == cur_method and rec.get("centroid"):
                return rec["centroid"], rec["method"], rec.get("n_ref_chunks", 0)
        except Exception:
            pass
    ref_texts = _ref_texts_for_author(author)
    cent, method, n = _build_author_centroid(ref_texts, model=model)
    if cent is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(
                {"_marker": "🔴 2026-06-29 NN风格声纹集成", "author": author,
                 "method": method, "dim": len(cent), "n_ref_chunks": n,
                 "centroid": cent}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return cent, method, n


# ── 主入口 ──────────────────────────────────────────────────────────────────
def compute_embedding_sfs(gen_text: str,
                          ref_texts: "list | None" = None,
                          author: "str | None" = None,
                          model: str = "author") -> dict:
    """embedding-SFS（默认安全·永不抛）。

    ref 优先级：author（带缓存 centroid）> ref_texts（直接传入·不缓存·style_evaluator 用）。
    返回 {"available": bool, "embedding_sfs": float|None, "method": str|None,
          "n_ref_chunks": int, "n_gen_chunks": int, "reason": str?, "gate_level": "advisory"}。
    """
    out = {"available": False, "embedding_sfs": None, "method": None,
           "n_ref_chunks": 0, "n_gen_chunks": 0, "gate_level": "advisory",
           "_marker": "🔴 2026-06-29 NN风格声纹集成"}
    try:
        from embedding_store import ruoyu_style_available, ruoyu_style_encode_batch
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"import_failed: {str(e)[:120]}"
        return out
    if not ruoyu_style_available(model):
        out["reason"] = "ruoyu_style 桥不可用（venv/模型/torch 缺）→ 回退（shadow 缺省）"
        return out

    gen_chunks = _chunks(gen_text)
    if not gen_chunks:
        out["reason"] = "gen 无有效 chunk"
        return out
    out["n_gen_chunks"] = len(gen_chunks)

    # 作者 centroid
    if author:
        author_cent, method, n_ref = _load_or_build_author_centroid(author, model=model)
    elif ref_texts:
        author_cent, method, n_ref = _build_author_centroid(ref_texts, model=model)
    else:
        out["reason"] = "未提供 author 或 ref_texts"
        return out
    if author_cent is None:
        out["reason"] = "作者 centroid 构建失败（ref 无 chunk 或桥失败）"
        return out
    out["n_ref_chunks"] = n_ref
    out["method"] = method

    gen_embs = ruoyu_style_encode_batch(gen_chunks, model=model)
    if not gen_embs:
        out["reason"] = "gen 编码失败（桥回退）"
        return out
    gen_cent = _centroid(gen_embs)
    if gen_cent is None or len(gen_cent) != len(author_cent):
        out["reason"] = "gen centroid 维度不符"
        return out
    out["available"] = True
    out["embedding_sfs"] = round(_cosine(gen_cent, author_cent), 4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="embedding-SFS（作者级 NN 风格保真度·shadow·advisory）")
    ap.add_argument("--gen", required=True, help="复刻/生成文（文件或目录）")
    ap.add_argument("--author", default=None, help="书名（自动定位 workspace/styles/<书名>/原文 + 缓存 centroid）")
    ap.add_argument("--ref-dir", default=None, help="原文目录（不缓存·直接构建 centroid）")
    ap.add_argument("--model", default="author", choices=["author", "character"])
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    gen_path = Path(args.gen)
    if gen_path.is_dir():
        gen_text = "\n\n".join(f.read_text(encoding="utf-8", errors="replace")
                               for f in sorted(gen_path.glob("*.txt")))
    elif gen_path.is_file():
        gen_text = gen_path.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"[错误] --gen 路径不存在: {gen_path}", file=sys.stderr)
        return 1

    ref_texts = None
    if args.ref_dir and not args.author:
        rd = Path(args.ref_dir)
        if rd.is_dir():
            ref_texts = [f.read_text(encoding="utf-8", errors="replace")
                         for f in sorted(rd.glob("*.txt"))[:200]]

    report = compute_embedding_sfs(gen_text, ref_texts=ref_texts,
                                   author=args.author, model=args.model)
    js = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(js, encoding="utf-8")
        print(f"[报告已保存] {args.output}", file=sys.stderr)
    else:
        print(js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
