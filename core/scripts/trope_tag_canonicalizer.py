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

_DATA = Path(__file__).resolve().parent.parent / "data" / "trope_canon.json"
_CACHE: dict | None = None

ISSUE_CODE_NEW_SURFACE = "TROPE_NEW_SURFACE_PROMOTION_CANDIDATE"
ISSUE_CODE_DICT_THIN = "TROPE_CANON_DICT_THIN"


def _mode() -> str:
    m = (os.environ.get("TROPE_CANON_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


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
