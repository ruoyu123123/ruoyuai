#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zero_shot_prototype.py — 通用 embedding 零样本原型分类工具（advisory 证据·2026-07-03）

【做法】SetFit/SimpleShot 范式：每类若干条典型例句 embed 后取质心（centroid），
待分类文本 embed 后取余弦相似度最高的质心 = nearest-centroid 分类。SimpleShot
(arXiv:1911.04623) 证明 L2-normalize + nearest-centroid 无需 meta-training 即是强基线；
HuggingFace SetFit 文档同样是少量例句 + Sentence-Transformer 质心距离做零/少样本分类
（HF SetFit zero-shot tutorial: https://huggingface.co/docs/setfit/main/en/tutorials/zero_shot）。
本工具是这套范式的最小可复用实现，供多个 scanner 接线复用，不重复造轮子。

【依赖】embedding_store.compute_embedding() + cosine_similarity()（同 topic_drift_scanner /
macguffin_entanglement_scanner 已验证模式）。EMBED_BACKEND 未设（默认 hash·无真语义）→
_has_real_embedding_backend() 返回 False → classify() 直接返回 None。

【北极星⑤】本工具只产 advisory 证据，绝不做 hard_gate 判定。调用方（各 scanner）必须在
classify() 返回 None 时 100% 回退自身原有词典/规则逻辑——真后端不可用或置信度不足时
零回归是硬约束，不是可选项。

用法:
    result = classify(text, {
        "laughter": ["她扑哧一声笑了出来", "太逗了，忍俊不禁"],
        "shock":    ["他瞳孔一缩，僵在原地", "心头一震，怎么会这样"],
    }, floor=0.5)
    if result is not None:
        label, score, margin = result["label"], result["score"], result["margin"]
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

SOURCE_TAG = "zero_shot_embedding"

# module 级质心缓存：key = (id(compute_embedding 函数对象), prototypes 内容 hash)。
# 用函数对象 id 而非 embedding_store.embedding_method() 字符串标签做 key——后者依赖
# _detect_backend() 的全局单例缓存，测试 monkeypatch compute_embedding 时不会同步刷新
# embedding_method() 的返回值，会导致 stale 后端标签把不同 mock 的质心缓存混在一起；
# id() 是当前实际生效函数对象的真身份，后端切换/被 monkeypatch 替换时 id 必然不同，
# 缓存正确失效，不会跨 mock/跨后端串命中。
_CENTROID_CACHE: dict = {}


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    跟 topic_drift_scanner._has_real_embedding_backend 判断逻辑完全一致（各文件各自留一份）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def clear_cache() -> None:
    """清空质心缓存（测试隔离用；生产环境常驻进程一般不需要调用）。"""
    _CENTROID_CACHE.clear()


def _prototypes_hash(label_prototypes: dict) -> str:
    raw = json.dumps(label_prototypes, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _l2_normalize(vec: list) -> list:
    norm = sum(v * v for v in vec) ** 0.5
    if norm <= 0:
        return vec
    return [v / norm for v in vec]


def _centroid(vectors: list) -> list:
    n = len(vectors)
    dim = len(vectors[0])
    avg = [sum(v[i] for v in vectors) / n for i in range(dim)]
    return _l2_normalize(avg)


def _build_centroids(label_prototypes: dict, compute_embedding) -> "dict | None":
    """每类例句 embed 后取质心。单条例句 embed 失败 → 跳过该条；

    某类维度与其它类不一致（后端切换未 rebuild）→ 该类整体跳过（不半真半假拼接）。
    全部类都失败 → None。
    """
    centroids: dict = {}
    dim = None
    for label, examples in label_prototypes.items():
        if not examples:
            continue
        vecs = []
        for ex in examples:
            try:
                v = compute_embedding(ex)
            except Exception:
                continue
            if v:
                vecs.append(v)
        if not vecs:
            continue
        this_dim = len(vecs[0])
        if any(len(v) != this_dim for v in vecs):
            continue
        if dim is None:
            dim = this_dim
        elif this_dim != dim:
            continue
        centroids[label] = _centroid(vecs)
    return centroids or None


def _get_centroids(label_prototypes: dict, compute_embedding) -> "dict | None":
    """按 (compute_embedding 函数身份, prototypes 内容 hash) 缓存质心，避免重复 embed。"""
    key = (id(compute_embedding), _prototypes_hash(label_prototypes))
    if key in _CENTROID_CACHE:
        return _CENTROID_CACHE[key]
    centroids = _build_centroids(label_prototypes, compute_embedding)
    if centroids is not None:
        _CENTROID_CACHE[key] = centroids
    return centroids


def classify(text: str, label_prototypes: dict, floor: float = 0.5) -> "dict | None":
    """embedding 零样本原型分类（nearest-centroid · SetFit/SimpleShot 范式）。

    每类 3-6 条典型例句 embed 取质心（模块级缓存，同 backend+同例句集不重复 embed），
    text embed 后取余弦相似度最高的质心当分类结果。

    门控/异常/低置信度全部返回 None（调用方 100% 回退自身原词典逻辑，零回归是硬约束）：
      · EMBED_BACKEND 未配置真后端
      · embedding_store 不可用 / compute_embedding 抛异常
      · 例句 embed 后无可用质心
      · 待分类文本 embed 失败 / 维度与质心不一致
      · top1 相似度 < floor

    返回: {"label": str, "score": float, "margin": float, "source": "zero_shot_embedding"}
    margin = top1 分数 - top2 分数（只有 1 类候选时 margin = top1 分数本身）。

    北极星⑤：本函数只产 advisory 证据，绝不做 hard_gate 判定。
    """
    if not text or not label_prototypes:
        return None
    if not _has_real_embedding_backend():
        return None
    try:
        from embedding_store import compute_embedding, cosine_similarity
    except Exception:
        return None
    try:
        centroids = _get_centroids(label_prototypes, compute_embedding)
        if not centroids:
            return None
        text_emb = compute_embedding(text)
        if not text_emb:
            return None
        scored = []
        for label, cvec in centroids.items():
            if len(cvec) != len(text_emb):
                return None  # 维度不一致（换后端未 rebuild）→ 不冒充语义，直接放弃
            scored.append((label, cosine_similarity(text_emb, cvec)))
        if not scored:
            return None
        scored.sort(key=lambda x: x[1], reverse=True)
        top_label, top_score = scored[0]
        margin = (top_score - scored[1][1]) if len(scored) > 1 else top_score
        if top_score < floor:
            return None
        return {
            "label": top_label,
            "score": round(top_score, 4),
            "margin": round(margin, 4),
            "source": SOURCE_TAG,
        }
    except Exception:
        return None
