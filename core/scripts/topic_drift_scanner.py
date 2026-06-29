# 🔴 2026-06-29 NN主题漂移/情感弧线集成
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""topic_drift_scanner.py — 主题漂移检测 scanner（advisory · embedding-based · 2026-06-29）

用已有的 embedding_store.compute_embedding() 计算每段与 cluster scope_summary 的余弦距离，
距离突然增大 = 跑题。三种 advisory issue:
  · TOPIC_DRIFT_DETECTED  — 某段余弦距离 > 全文均值 + 2σ（单点偏离）
  · TOPIC_DRIFT_SUSTAINED — 连续 3+ 段距离持续偏大（持续跑题）
  · TOPIC_RETURN_ABRUPT   — 跑题后突然回归（距离骤降）→ 转场生硬

【依赖】embedding_store.compute_embedding() + cosine_similarity()。
  EMBED_BACKEND 未设（默认 hash = md5 n-gram 袋·无真语义距离意义）→ 静默返回空列表。
  只有配了真后端（mstyle / local / ruoyu_style / api）才运行。

【北极星⑤】所有 issue 永远 advisory，绝不进 HARD_GATE_CODES。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── 后端检测 ──────────────────────────────────────────────────────────────────

def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    也检查 .env 的 GEN_EMBED__* API 配置（由 embedding_store._load_embed_profile 消费）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


# ── 段落切分 ─────────────────────────────────────────────────────────────────

def _split_paragraphs(text: str, min_len: int = 5) -> list[str]:
    """按换行切段，过滤太短的行。"""
    paras: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= min_len:
            paras.append(line)
    return paras


# ── 距离计算 ─────────────────────────────────────────────────────────────────

def _compute_distances(para_embeddings: list[list[float]],
                       scope_embedding: list[float],
                       cosine_fn) -> list[float]:
    """计算每段与 scope_summary 的余弦距离 (1 - similarity)。"""
    return [1.0 - cosine_fn(pe, scope_embedding) for pe in para_embeddings]


# ── 滑动窗口 ─────────────────────────────────────────────────────────────────

def _sliding_window_mean(values: list[float], window: int = 5) -> list[float]:
    """滑动窗口均值（边界用有效窗口长度）。返回与 values 等长的列表。"""
    n = len(values)
    if n == 0:
        return []
    result: list[float] = []
    half = window // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        result.append(sum(values[lo:hi]) / (hi - lo))
    return result


# ── CHANGES 剥离（与 style_similarity_scanner 一致·防元数据污染段落分布）────────────

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _strip_changes(text: str) -> str:
    """剥离正文尾部 CHANGES 段（系统写作时拼接·主题分析不应纳入元数据）。幂等安全。"""
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


# ── 主检测函数 ────────────────────────────────────────────────────────────────

def scan_topic_drift(draft_text: str, scope_summary: str,
                     project_dir: str = None) -> list[dict]:
    """主题漂移检测。返回 advisory issue 列表。

    embedding_store 不可用（EMBED_BACKEND 未设 = 默认 hash）→ 返回空列表（静默降级）。
    段落太少（< 6）→ 返回空列表。所有 issue: gate_level = "advisory"。
    """
    # ── 前置守卫 ──
    if not draft_text or not scope_summary:
        return []
    draft_text = _strip_changes(draft_text)   # 防 CHANGES 元数据污染段落分布
    if not _has_real_embedding_backend():
        return []

    # ── 动态导入 embedding_store（系统 py 一定有·但 import 异常也兜底）──
    try:
        from embedding_store import compute_embedding, cosine_similarity
    except (ImportError, TypeError):
        return []

    paras = _split_paragraphs(draft_text)
    if len(paras) < 6:
        return []

    # ── 编码 ──
    try:
        scope_emb = compute_embedding(scope_summary)
        para_embs = [compute_embedding(p) for p in paras]
    except Exception:
        return []

    # 维度一致性守卫：compute_embedding 单条失败会兜底 hash(384)，与真后端维度不一致 →
    # cosine 退化为 0（假漂移）。维度混用直接跳过（北极星「不拿降级 hash 冒充真语义」）。
    if not scope_emb or any(len(e) != len(scope_emb) for e in para_embs):
        return []

    # ── 余弦距离 ──
    distances = _compute_distances(para_embs, scope_emb, cosine_similarity)
    if not distances:
        return []

    n = len(distances)
    mean_d = sum(distances) / n
    variance = sum((d - mean_d) ** 2 for d in distances) / n
    std_d = math.sqrt(variance) if variance > 0 else 0.001

    # 滑窗局部均值（window=5）：作为每条 issue 的 local_window_mean 上下文指标
    local_means = _sliding_window_mean(distances, window=5)

    issues: list[dict] = []

    # ── 规则 1: TOPIC_DRIFT_DETECTED ──────────────────────────────────────
    drift_threshold = mean_d + 2.0 * std_d
    for idx, d in enumerate(distances):
        if d > drift_threshold:
            issues.append({
                "code": "TOPIC_DRIFT_DETECTED",
                "gate_level": "advisory",
                "severity": "minor",
                "paragraph_index": idx,
                "paragraph_preview": paras[idx][:80],
                "distance": round(d, 4),
                "threshold": round(drift_threshold, 4),
                "mean_distance": round(mean_d, 4),
                "std_distance": round(std_d, 4),
                "local_window_mean": round(local_means[idx], 4),
                "message": (f"段落 {idx} 偏离主题（余弦距离 {d:.3f} > "
                            f"均值+2σ {drift_threshold:.3f}）：{paras[idx][:50]}…"),
            })

    # ── 规则 2: TOPIC_DRIFT_SUSTAINED ─────────────────────────────────────
    sustained_threshold = mean_d + 1.0 * std_d
    run_start: int | None = None
    for i in range(n + 1):
        above = (i < n and distances[i] > sustained_threshold)
        if above:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and (i - run_start) >= 3:
                _avg = sum(distances[run_start:i]) / (i - run_start)
                issues.append({
                    "code": "TOPIC_DRIFT_SUSTAINED",
                    "gate_level": "advisory",
                    "severity": "minor",
                    "paragraph_index": run_start,
                    "paragraph_preview": paras[run_start][:80],
                    "start_paragraph": run_start,
                    "end_paragraph": i - 1,
                    "span_length": i - run_start,
                    "distance": round(_avg, 4),
                    "avg_distance": round(_avg, 4),
                    "threshold": round(sustained_threshold, 4),
                    "local_window_mean": round(local_means[run_start], 4),
                    "message": (f"段落 {run_start}-{i - 1} 持续偏离主题"
                                f"（连续 {i - run_start} 段距离 > 均值+1σ）"),
                })
            run_start = None

    # ── 规则 3: TOPIC_RETURN_ABRUPT ───────────────────────────────────────
    for i in range(1, n):
        drop = distances[i - 1] - distances[i]
        if (drop > 2.0 * std_d
                and distances[i - 1] > drift_threshold
                and distances[i] < sustained_threshold):
            issues.append({
                "code": "TOPIC_RETURN_ABRUPT",
                "gate_level": "advisory",
                "severity": "minor",
                "paragraph_index": i,
                "paragraph_preview": paras[i][:80],
                "distance_before": round(distances[i - 1], 4),
                "distance_after": round(distances[i], 4),
                "drop": round(drop, 4),
                "distance": round(distances[i], 4),
                "threshold": round(sustained_threshold, 4),
                "local_window_mean": round(local_means[i], 4),
                "message": (f"段落 {i} 主题突然回归（距离从 {distances[i - 1]:.3f} "
                            f"骤降到 {distances[i]:.3f}）→ 转场可能生硬"),
            })

    return issues


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read_scope_summary(project_dir: str | None, draft_path: str) -> str | None:
    if not project_dir:
        return None
    p = Path(project_dir) / "_数据库" / "事件簇.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        draft_name = Path(draft_path).stem.replace("_draft", "")
        for c in data.get("clusters", []):
            if c.get("cluster_id", "") == draft_name:
                return c.get("scope_summary")
        if data.get("clusters"):
            return data["clusters"][0].get("scope_summary")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def main():
    ap = argparse.ArgumentParser(
        description="主题漂移检测 scanner（advisory · embedding-based）")
    ap.add_argument("draft_path", help="草稿文件路径")
    ap.add_argument("--scope-summary", default=None,
                    help="cluster scope_summary 文本（不传则从事件簇.json读）")
    ap.add_argument("--project", default=None, help="项目根目录")
    args = ap.parse_args()

    scope = args.scope_summary or _read_scope_summary(args.project, args.draft_path)
    if not scope:
        print(json.dumps([], ensure_ascii=False))
        sys.exit(0)

    text = Path(args.draft_path).read_text(encoding="utf-8")
    issues = scan_topic_drift(text, scope, args.project)
    print(json.dumps(issues, ensure_ascii=False, indent=2))
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
