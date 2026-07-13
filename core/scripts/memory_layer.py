#!/usr/bin/env python3
"""检索最近 cluster 正文、cluster 摘要与长期事实。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json  # noqa: E402
import cluster_lookup  # noqa: E402
import cluster_summary_reader as csr  # noqa: E402


def load_json(path: Path, default=None):
    return atomic_json.load_json(path, default=default)


def _cluster_number(cluster_id: str) -> int:
    number = cluster_lookup.cluster_num(cluster_id)
    if number is None:
        raise ValueError(f"cluster_id 非法: {cluster_id!r}")
    return number


def _cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    key = cluster_id.removeprefix("cluster_")
    return project_root / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt"


def _tokens(text: str) -> list[str]:
    chars = re.sub(r'[^一-鿿a-zA-Z0-9]', '', text)
    words = re.findall(r'[一-鿿]{2,4}', text)
    bigrams = [chars[i:i+2] for i in range(len(chars)-1)]
    return words + bigrams


def _cosine(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys: return 0.0
    dot = sum(a[k]*b[k] for k in keys)
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    return dot/(na*nb) if na and nb else 0.0


def _tfidf_vec(text: str, idf: dict) -> dict:
    tokens = _tokens(text)
    tf = Counter(tokens)
    total = max(len(tokens), 1)
    return {t: (c/total) * idf.get(t, 1.0) for t, c in tf.items()}


def _content_backend_ready() -> bool:
    """返回内容语义后端是否就绪。"""
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


DEFAULT_SEMANTIC_SEARCH_FLOOR = 0.42


def _semantic_search_floor() -> float:
    raw = os.environ.get("MEMORY_LAYER_SEMANTIC_FLOOR")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_SEMANTIC_SEARCH_FLOOR


def _search_semantic(query: str, memories: list[dict], top_k: int) -> list[dict]:
    """用内容嵌入余弦排序检索，后端故障时显式失败。"""
    try:
        from embedding_store import (compute_content_embedding, cosine_similarity,
                                      prefetch_content_embeddings)
        prefetch_content_embeddings([query] + [m.get("content", "") for m in memories])
        q_emb = compute_content_embedding(query)
    except Exception as exc:
        raise RuntimeError("记忆查询语义编码失败") from exc
    if not q_emb:
        raise RuntimeError("记忆查询语义编码为空")
    floor = _semantic_search_floor()
    scored = []
    for mem in memories:
        try:
            emb = compute_content_embedding(mem["content"])
        except Exception as exc:
            raise RuntimeError("记忆内容语义编码失败") from exc
        if not emb or len(emb) != len(q_emb):
            raise RuntimeError("记忆内容 embedding 为空或维度不一致")
        sim = cosine_similarity(emb, q_emb)
        if sim > floor:
            scored.append({**mem, "score": round(sim, 4),
                          "content": mem["content"][:200], "method": "semantic"})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]


class MemoryLayer:
    def __init__(self, project_root: Path, current_cluster_id: str):
        self.root = Path(project_root)
        self.db = self.root / "_数据库"
        if not isinstance(current_cluster_id, str) or not re.fullmatch(
            r"cluster_\d{3,}", current_cluster_id
        ):
            raise ValueError("current_cluster_id 必须是规范 cluster_NNN")
        self.cluster_id = current_cluster_id
        self.cluster_number = _cluster_number(current_cluster_id)

    def _history(self) -> list[dict]:
        return [
            record for record in csr.get_clusters(self.root)
            if _cluster_number(record["cluster_id"]) < self.cluster_number
        ]

    def _load_cluster_memory(self, window: int = 3) -> list[dict]:
        """读取最近 N 个已完成 cluster 的正文尾部。"""
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise ValueError("window 必须是正整数，单位为 cluster")
        results = []
        for record in self._history()[-window:]:
            cluster_id = record["cluster_id"]
            path = _cluster_draft_path(self.root, cluster_id)
            try:
                body = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise FileNotFoundError(f"cluster 终稿不存在: {path}") from exc
            if not body.strip():
                raise ValueError(f"cluster 终稿为空: {path}")
            results.append({
                "layer": "cluster",
                "cluster_id": cluster_id,
                "content": body[-1000:],
            })
        return results

    def _load_summary_memory(self) -> list[dict]:
        """读取当前 cluster 之前的所有严格摘要记录。"""
        return [{
            "layer": "summary",
            "cluster_id": record["cluster_id"],
            "content": record["summary"],
            "emotion": record["emotion"],
        } for record in self._history()]

    def _load_archive_memory(self) -> list[dict]:
        """从人物卡与世界观提取长期事实。"""
        results = []
        cards = load_json(self.db / "人物卡.json", {}).get("characters", [])
        for c in cards:
            facts = c.get("locked_facts", [])
            arc = c.get("growth_arc", [])
            content = f"角色{c.get('name')}: " + "; ".join(facts[:3])
            if arc:
                latest = arc[-1]
                source_cluster = latest.get("_source_cluster")
                if source_cluster:
                    content += f" [最新状态{source_cluster}: {latest.get('state', '')}]"
            results.append({"layer": "archive", "source": "人物卡", "content": content})
        world = load_json(self.db / "世界观.json", {}).get("entries", [])
        for w in world[:10]:
            results.append({"layer": "archive", "source": "世界观",
                           "content": f"{w.get('id','')}: {'; '.join(w.get('keywords',[])[:5])}"})
        return results

    def build(self) -> dict:
        """返回三层记忆规模。"""
        cluster_mem = self._load_cluster_memory()
        sum_mem = self._load_summary_memory()
        arc_mem = self._load_archive_memory()
        return {
            "cluster_count": len(cluster_mem),
            "summary_count": len(sum_mem),
            "archive_count": len(arc_mem),
            "total": len(cluster_mem) + len(sum_mem) + len(arc_mem),
        }

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """跨三层检索最相关的记忆。内容嵌入后端就绪时用余弦排序，
        否则 TF-IDF 词袋余弦（同义改写查不到·占位法）。"""
        all_memories = (self._load_cluster_memory() +
                       self._load_summary_memory() +
                       self._load_archive_memory())
        if not all_memories: return []
        if _content_backend_ready():
            return _search_semantic(query, all_memories, top_k)
        docs = [m["content"] for m in all_memories]
        docs.append(query)
        n = len(docs)
        all_tokens = [_tokens(d) for d in docs]
        df = Counter()
        for toks in all_tokens:
            for t in set(toks): df[t] += 1
        idf = {t: math.log((n+1)/(c+1))+1 for t, c in df.items()}
        query_vec = _tfidf_vec(query, idf)
        scored = []
        for i, mem in enumerate(all_memories):
            vec = _tfidf_vec(docs[i], idf)
            sim = _cosine(vec, query_vec)
            if sim > 0.01:
                scored.append({**mem, "score": round(sim, 4), "content": mem["content"][:200]})
        scored.sort(key=lambda x: -x["score"])
        return scored[:top_k]

    def extract_entities(self, text: str) -> list[dict]:
        """按项目已知人物、地点和道具提取实体。"""
        cards = load_json(self.db / "人物卡.json", {}).get("characters", [])
        known_chars = {c.get("name") for c in cards if c.get("name")}
        world = load_json(self.db / "世界观.json", {}).get("entries", [])
        known_locs = set()
        for w in world:
            known_locs.update(w.get("keywords", []))
        items_data = load_json(self.db / "道具.json", {"items": []}).get("items", [])
        known_items = {i.get("name") for i in items_data if i.get("name")}
        entities = []
        for name in known_chars:
            c = text.count(name)
            if c > 0: entities.append({"type": "character", "name": name, "count": c})
        for loc in known_locs:
            if loc and len(loc) >= 2 and loc in text:
                entities.append({"type": "location", "name": loc, "count": text.count(loc)})
        for item in known_items:
            if item and item in text:
                entities.append({"type": "item", "name": item, "count": text.count(item)})
        entities.sort(key=lambda x: -x["count"])
        return entities

    def stats(self) -> dict:
        """返回当前记忆层统计。"""
        return self.build()


def main():
    parser = argparse.ArgumentParser(description="cluster 三层记忆检索")
    parser.add_argument("project")
    parser.add_argument("cluster_id")
    parser.add_argument("--action", choices=("search", "build", "stats"), default="stats")
    parser.add_argument("--query", default="")
    args = parser.parse_args()
    ml = MemoryLayer(Path(args.project).resolve(), args.cluster_id)
    if args.action == "build":
        print(json.dumps(ml.build(), ensure_ascii=False, indent=2))
    elif args.action == "search":
        if not args.query:
            parser.error("search 需要 --query")
        results = ml.search(args.query)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(ml.stats(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
