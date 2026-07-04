#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trope_tag_canonicalizer.py — trope 标签 canonical 化 · R23 W11 Batch-GG · P0

【缺口】fanfic ecosystem 修跨 cluster 统计层根因：storyboard / cluster brief 里
trope tag 是自由文本（「重生」/「重生归来」/「再活一次」同义不同写）。cross-cluster
aggregator 直接 count 字面会把同一 trope 拆成多个低频项 → 跨 cluster 趋势分析失真。

【做法 · 确定性 · 零 LLM/零联网】
  · 维护 core/data/trope_canon.json（synonym → canonical 占位 30 同义对）
  · build_manifest 注入前 / cross-cluster aggregator 读取前调 canonicalize_tags() 合并
  · 新 surface（不在 map 内）≥3 次出现 → 写入 _数据库/.trope_promotion_queue.json
    供后续 advisory（不自动改 trope_canon.json，需人审）

【北极星】②④⑤ 规范统计层不改 writer 原文 · cluster · advisory · 占位 _placeholder=true

env TROPE_CANON_MODE: off / shadow（默认） / active
  · off    → canonicalize_tags 退化为 identity（debug 用）
  · shadow → 合并但不写 promotion_queue
  · active → 合并 + 写 promotion_queue + 暴露 advisory

用法（脚本）:
  python trope_tag_canonicalizer.py --project <root>          # 扫所有 cluster 报 promotion
  python trope_tag_canonicalizer.py --normalize "重生归来,再活一次,杀手"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from collections import Counter

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_DATA = Path(__file__).resolve().parent.parent / "data" / "trope_canon.json"
_CACHE: dict | None = None

ISSUE_CODE_NEW_SURFACE = "TROPE_NEW_SURFACE_PROMOTION_CANDIDATE"
ISSUE_CODE_DICT_THIN = "TROPE_CANON_DICT_THIN"

# 金标准校准 2026-07-04：content_embed_separability_20260704 报告——新 surface vs 已有
# canonical 值最近邻余弦阈值。晋升建议本质是合并判定（把新 surface 归并到某已有 canonical
# trope），误并代价高（取严格位）：content_vs_style_confound 族 neg_p95=0.5495≈0.55。
NEAREST_CANONICAL_SIM_THRESHOLD = 0.55


def _mode() -> str:
    m = (os.environ.get("TROPE_CANON_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ── 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）───────
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方不给建议·仍要求人审）。
    """
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


def _embed_canonical_targets(canonical_targets: list) -> "list[tuple[str, list]] | None":
    """内容后端就绪时把去重后的 canonical 目标值编码一次，供本次扫描内所有新 surface
    候选复用（避免 O(候选 × 目标) 重复编码·2026-07-02）。

    未配内容后端 / 无目标 / 编码异常 → None（调用方不给建议·仍要求人审）。
    """
    if not _content_backend_ready() or not canonical_targets:
        return None
    try:
        from embedding_store import compute_content_embedding
        out = []
        for cand in canonical_targets:
            ce = compute_content_embedding(cand)
            if ce:
                out.append((cand, ce))
        return out or None
    except Exception:
        return None


def _nearest_canonical_suggestion(surface: str, target_embs) -> "dict | None":
    """内容后端下：新 surface 与已有 canonical 值的最近邻建议（相似度 ≥ 阈值才给）。

    target_embs 为 None（无内容后端/编码失败）/ surface 空 / 计算异常 → None
    （调用方不加字段·字段缺省·仍要求人审·绝不自动改 trope_canon.json）。
    """
    if not target_embs or not surface:
        return None
    try:
        from embedding_store import compute_content_embedding, cosine_similarity
        se = compute_content_embedding(surface)
        if not se:
            return None
        best_name, best_sim = None, -1.0
        for cand, ce in target_embs:
            if len(ce) != len(se):
                continue
            sim = cosine_similarity(se, ce)
            if sim > best_sim:
                best_name, best_sim = cand, sim
        if best_name is not None and best_sim >= NEAREST_CANONICAL_SIM_THRESHOLD:
            return {"canonical": best_name, "similarity": round(best_sim, 4)}
    except Exception:
        return None
    return None


def load_canon() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_DATA.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _CACHE = {"canonical_map": {}, "_placeholder": True}
    return _CACHE


def canonicalize_tag(tag: str) -> str:
    """单个 tag 字面 → canonical · off 退化 identity · 命中 map 取 canonical · 未命中保留原 surface"""
    if _mode() == "off":
        return tag
    if not isinstance(tag, str):
        return tag
    s = tag.strip()
    if not s:
        return s
    m = load_canon().get("canonical_map") or {}
    return m.get(s, s)


def canonicalize_tags(tags) -> list:
    """tags list/None → canonical list · 去重保序"""
    if not tags:
        return []
    seen = []
    seen_set = set()
    for t in tags:
        c = canonicalize_tag(t)
        if c and c not in seen_set:
            seen.append(c)
            seen_set.add(c)
    return seen


def _collect_all_tags(project_root: Path) -> list[str]:
    """从事件簇.json / storyboard 收集所有 trope tag · placeholder 字段名兼容"""
    tags: list[str] = []
    db = project_root / "_数据库"
    if not db.exists():
        return tags
    candidates = ["事件簇.json", "走向卡.json", "story_storyboard.json"]
    keys_to_scan = ["trope_tags", "tropes", "tag_list", "tags"]
    for fname in candidates:
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        clusters = obj.get("clusters") if isinstance(obj, dict) else None
        if isinstance(clusters, list):
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                for k in keys_to_scan:
                    v = c.get(k)
                    if isinstance(v, list):
                        tags.extend([str(x) for x in v if isinstance(x, str)])
    return tags


def scan_for_promotions(project_root: str | Path) -> dict:
    """扫项目所有 cluster trope tag · 报新 surface≥3 次的候选"""
    mode = _mode()
    out = {
        "scanner": "trope_tag_canonicalizer", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
    }
    if mode == "off":
        return out
    root = Path(project_root)
    tags = _collect_all_tags(root)
    if not tags:
        out["note"] = "无 trope 标签可扫"
        return out
    canon_map = load_canon().get("canonical_map") or {}
    # 字典稀薄 advisory（占位状态）
    if len(canon_map) < 30:
        out["violations"].append({
            "kind": "trope_canon", "severity": "info",
            "code": ISSUE_CODE_DICT_THIN,
            "message": f"trope_canon.json 占位条目 {len(canon_map)} < 30 · 仅基础同义合并",
            "_doc": "R23 W11 占位状态 · 真版词典 defer · advisory",
        })

    new_surface_counter: Counter = Counter()
    canonical_counter: Counter = Counter()
    for t in tags:
        s = t.strip()
        if not s:
            continue
        c = canonicalize_tag(s)
        canonical_counter[c] += 1
        if s not in canon_map and s != c:
            # surface == canonical 意味着未命中 map（identity 返回）
            pass
        if s not in canon_map:
            new_surface_counter[s] += 1

    promotion_threshold = int(load_canon().get("_doc_promotion_threshold", 3))
    promotion_candidates = [
        {"surface": k, "count": v}
        for k, v in new_surface_counter.items()
        if v >= promotion_threshold
    ]
    # 真后端就绪时给每个候选加 embedding 最近邻 canonical 建议（不改 canonicalize_tag 本体·
    # 不自动改 trope_canon.json·仍要求人审）；无真后端 → 字段缺省（2026-07-02）
    if promotion_candidates:
        canonical_targets = sorted(set(canon_map.values()))
        # 🔴 2026-07-03 Wave-4：canonical 目标值 + 候选 surface 两侧文本一次性 prefetch
        # （真后端子进程按条调用极贵·合并成一次批调用）——下面 _embed_canonical_targets /
        # _nearest_canonical_suggestion 内的逐条 compute_content_embedding 全部命中缓存。
        if _content_backend_ready():
            try:
                from embedding_store import prefetch_content_embeddings
                prefetch_content_embeddings(
                    canonical_targets + [c["surface"] for c in promotion_candidates])
            except Exception:
                pass
        target_embs = _embed_canonical_targets(canonical_targets)
        for cand in promotion_candidates:
            suggestion = _nearest_canonical_suggestion(cand["surface"], target_embs)
            if suggestion is not None:
                cand["embedding_nearest_canonical"] = suggestion["canonical"]
                cand["embedding_similarity"] = suggestion["similarity"]
    out["canonical_distribution"] = dict(canonical_counter.most_common(10))
    out["promotion_candidates"] = promotion_candidates
    out["total_tags_scanned"] = len(tags)
    out["unique_canonicals"] = len(canonical_counter)

    if promotion_candidates:
        if mode == "active":
            queue_path = root / "_数据库" / ".trope_promotion_queue.json"
            try:
                queue_path.parent.mkdir(parents=True, exist_ok=True)
                queue_path.write_text(
                    json.dumps({"_doc": "trope_tag_canonicalizer · 待人审晋升队列",
                                "candidates": promotion_candidates},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except OSError as e:
                out["note"] = f"promotion_queue 写失败：{str(e)[:120]}"
            for c in promotion_candidates:
                out["violations"].append({
                    "kind": "trope_canon", "severity": "minor",
                    "code": ISSUE_CODE_NEW_SURFACE,
                    "message": f"新 surface「{c['surface']}」出现 {c['count']} 次 · 建议晋升 canonical",
                    "_doc": "R23 W11 Batch-GG·P0·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = f"{len(promotion_candidates)} 个新 surface 待晋升"
        else:
            print(f"[SHADOW] trope_canon: {len(promotion_candidates)} 候选 — 不写 queue", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="trope tag canonical 化 · R23 W11 Batch-GG")
    ap.add_argument("--project", default=None)
    ap.add_argument("--normalize", default=None,
                    help="逗号分隔 tag 列表 · 输出 canonical 化结果")
    args = ap.parse_args()
    if args.normalize:
        tags = [t.strip() for t in args.normalize.split(",") if t.strip()]
        result = canonicalize_tags(tags)
        print(json.dumps({"input": tags, "canonical": result}, ensure_ascii=False, indent=2))
        sys.exit(0)
    if not args.project:
        print("Need --project or --normalize", file=sys.stderr)
        sys.exit(2)
    rep = scan_for_promotions(args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
