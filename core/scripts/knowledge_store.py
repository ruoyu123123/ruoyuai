#!/usr/bin/env python3
"""knowledge_store.py — 本地知识库（跨项目共享·随蒸馏/调研/创作自动积累）

设计哲学：
  系统的知识不该只靠联网搜索（片面·不稳定）和 LLM 内置知识（会过时·幻觉）。
  每次蒸馏/调研/创作产生的高价值知识自动沉淀到本地，越用越厚。

存储结构：
  core/claude-home/knowledge/
  ├── genre/                    # 题材知识（修仙体系/都市异能/历史朝代...）
  │   ├── xuanhuan.jsonl        # 每行一条知识条目
  │   ├── xianxia.jsonl
  │   └── ...
  ├── technique/                # 描写技法（五感/动作/对话/心理...）
  │   ├── sensory.jsonl         # 感官描写素材
  │   ├── action.jsonl          # 动作描写素材
  │   ├── dialogue.jsonl        # 对话工艺素材
  │   └── ...
  ├── worldbuilding/            # 世界观模板（力量体系/社会结构...）
  │   └── patterns.jsonl
  └── research/                 # 调研沉淀（联网搜索高价值结果）
      └── findings.jsonl

条目格式（JSONL，每行一条）：
  {"topic": "cultivation_system", "source": "distill:惊悚乐园", "source_type": "distill",
   "content": "...", "tags": ["修仙", "境界"], "quality": 0.8, "created": "2026-06-19",
   "used_count": 0}

消费者：
  build_manifest._inject_knowledge_context() → 按 genre + scene_type 查询相关条目
  → 注入 gen_writer prompt（advisory·补充素材）

生产者：
  1. 蒸馏流水线 surface_runner → 提取题材知识/描写手法 → knowledge_store.add()
  2. novel-researcher 调研 → 高价值 findings → knowledge_store.add()
  3. 创作后 scanner 反馈 → 成功模式/失败模式 → knowledge_store.add()
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from frozen_util import bundle_root as _bundle_root
    _KNOWLEDGE_ROOT = _bundle_root() / "core" / "claude-home" / "knowledge"
except Exception:
    _KNOWLEDGE_ROOT = Path(__file__).resolve().parent.parent / "claude-home" / "knowledge"

CATEGORIES = {
    "genre": "题材知识（修仙体系/都市异能/历史朝代等）",
    "technique": "描写技法（五感/动作/对话/心理等）",
    "worldbuilding": "世界观模板（力量体系/社会结构等）",
    "research": "调研沉淀（联网搜索高价值结果）",
}


def _ensure_dirs():
    for cat in CATEGORIES:
        (_KNOWLEDGE_ROOT / cat).mkdir(parents=True, exist_ok=True)


def add(category: str, topic: str, content: str, *,
        source: str = "unknown", source_type: str = "manual",
        tags: list[str] | None = None, quality: float = 0.5) -> Path:
    """添加一条知识条目。

    Args:
        category: genre/technique/worldbuilding/research
        topic: 主题文件名（不含 .jsonl）
        content: 知识内容（一段文字）
        source: 来源标识（如 "distill:惊悚乐园" / "research:WebSearch" / "writing:凿窍纪_cluster_001"）
        source_type: distill/research/writing/manual
        tags: 标签列表
        quality: 质量分 0-1（默认 0.5，高质量的被优先注入）
    """
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {list(CATEGORIES)}, got '{category}'")
    _ensure_dirs()
    entry = {
        "topic": topic,
        "source": source,
        "source_type": source_type,
        "content": content.strip(),
        "tags": tags or [],
        "quality": round(quality, 2),
        "created": datetime.now().strftime("%Y-%m-%d"),
        "used_count": 0,
    }
    fpath = _KNOWLEDGE_ROOT / category / f"{topic}.jsonl"
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return fpath


def query(category: str, topic: Optional[str] = None, *,
          tags: list[str] | None = None, min_quality: float = 0.0,
          limit: int = 20) -> list[dict]:
    """查询知识条目。

    Args:
        category: genre/technique/worldbuilding/research
        topic: 主题（None=该分类下所有主题）
        tags: 标签过滤（任一匹配即收）
        min_quality: 最低质量分
        limit: 最多返回条数（按 quality 降序）
    """
    _ensure_dirs()
    results = []
    cat_dir = _KNOWLEDGE_ROOT / category
    if topic:
        files = [cat_dir / f"{topic}.jsonl"]
    else:
        files = list(cat_dir.glob("*.jsonl"))

    for fpath in files:
        if not fpath.exists():
            continue
        for line in fpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("quality", 0) < min_quality:
                continue
            if tags and not set(tags) & set(entry.get("tags", [])):
                continue
            results.append(entry)

    results.sort(key=lambda x: x.get("quality", 0), reverse=True)
    return results[:limit]


def query_for_genre(genre: str, limit: int = 10) -> list[dict]:
    """按题材查询知识（genre 分类 + research 中标记了该 genre 的）。"""
    results = query("genre", genre, limit=limit)
    research = query("research", tags=[genre], limit=limit)
    combined = results + research
    combined.sort(key=lambda x: x.get("quality", 0), reverse=True)
    return combined[:limit]


def query_for_scene(scene_type: str, limit: int = 10) -> list[dict]:
    """按场景类型查询描写技法。"""
    return query("technique", tags=[scene_type], limit=limit)


def stats() -> dict:
    """统计各分类条目数。"""
    _ensure_dirs()
    result = {}
    for cat in CATEGORIES:
        cat_dir = _KNOWLEDGE_ROOT / cat
        count = 0
        for f in cat_dir.glob("*.jsonl"):
            count += sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
        result[cat] = count
    result["total"] = sum(result.values())
    return result


def increment_used(category: str, topic: str, content_prefix: str):
    """标记某条目被使用（used_count +1）——用于后续按使用频率排序。"""
    fpath = _KNOWLEDGE_ROOT / category / f"{topic}.jsonl"
    if not fpath.exists():
        return
    lines = fpath.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if not line.strip():
            updated.append(line)
            continue
        try:
            entry = json.loads(line)
            if entry.get("content", "").startswith(content_prefix[:50]):
                entry["used_count"] = entry.get("used_count", 0) + 1
            updated.append(json.dumps(entry, ensure_ascii=False))
        except json.JSONDecodeError:
            updated.append(line)
    fpath.write_text("\n".join(updated) + "\n", encoding="utf-8")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="本地知识库管理")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("stats", help="统计各分类条目数")

    q = sub.add_parser("query", help="查询知识")
    q.add_argument("category", choices=list(CATEGORIES))
    q.add_argument("--topic", default=None)
    q.add_argument("--tags", nargs="*", default=None)
    q.add_argument("--limit", type=int, default=10)

    a = sub.add_parser("add", help="添加知识条目")
    a.add_argument("category", choices=list(CATEGORIES))
    a.add_argument("topic")
    a.add_argument("content")
    a.add_argument("--source", default="manual")
    a.add_argument("--tags", nargs="*", default=None)
    a.add_argument("--quality", type=float, default=0.5)

    args = ap.parse_args()
    if args.cmd == "stats":
        s = stats()
        for k, v in s.items():
            print(f"  {k}: {v}")
    elif args.cmd == "query":
        items = query(args.category, args.topic, tags=args.tags, limit=args.limit)
        for item in items:
            print(json.dumps(item, ensure_ascii=False))
    elif args.cmd == "add":
        p = add(args.category, args.topic, args.content,
                source=args.source, tags=args.tags or [], quality=args.quality)
        print(f"Added to {p}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
