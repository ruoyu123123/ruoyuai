#!/usr/bin/env python3
"""
memory_layer.py — 三层记忆系统（移植自Mem0/Letta概念）

三层架构：
  1. 章节记忆(chapter) — 最近N章的详细内容（高精度/高成本）
  2. 摘要记忆(summary) — 全书章节摘要（中精度/低成本）
  3. 归档记忆(archive) — 压缩后的长期事实（低精度/最低成本）

用法：
  python memory_layer.py <项目路径> <章节号> --action search --query "许遥的父亲"
  python memory_layer.py <项目路径> <章节号> --action build
  python memory_layer.py <项目路径> <章节号> --action stats
"""
from __future__ import annotations
import json, re, sys, math
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


def load_json(p: Path, default=None):
    if not p.exists(): return default
    try: return json.loads(p.read_text(encoding="utf-8"))
    except: return default


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


class MemoryLayer:
    def __init__(self, project_root: Path, current_ch: int):
        self.root = project_root
        self.db = project_root / "_数据库"
        self.ch = current_ch

    def _load_chapter_memory(self, window: int = 3) -> list[dict]:
        """层1：最近N章的完整正文片段。
        v18：统一走 cio.read_body() —— 旧实现只 glob 平铺布局 + split "---CHANGES---"，
        既漏嵌套布局，又匹配不到 "---CHANGES_FACTUAL---" 致 JSON 混入记忆。"""
        results = []
        for ch in range(max(1, self.ch - window), self.ch):
            try:
                body = cio.read_body(self.root, ch)
            except FileNotFoundError:
                continue
            results.append({"layer": "chapter", "ch": ch, "content": body[-1000:]})
        return results

    def _load_summary_memory(self) -> list[dict]:
        """层2：全书摘要"""
        data = load_json(self.db / "故事块摘要.json", {"chapters": []})
        results = []
        for s in data.get("chapters", []):
            ch = s.get("ch", s.get("chapter", 0))
            if ch >= self.ch: continue
            results.append({"layer": "summary", "ch": ch,
                           "content": s.get("summary", ""), "emotion": s.get("emotion", {})})
        return results

    def _load_archive_memory(self) -> list[dict]:
        """层3：归档事实（从人物卡/世界观/伏笔表提取关键事实）"""
        results = []
        cards = load_json(self.db / "人物卡.json", {}).get("characters", [])
        for c in cards:
            facts = c.get("locked_facts", [])
            arc = c.get("growth_arc", [])
            content = f"角色{c.get('name')}: " + "; ".join(facts[:3])
            if arc:
                latest = arc[-1]
                content += f" [最新状态ch{latest.get('ch')}: {latest.get('state', '')}]"
            results.append({"layer": "archive", "source": "人物卡", "content": content})
        world = load_json(self.db / "世界观.json", {}).get("entries", [])
        for w in world[:10]:
            results.append({"layer": "archive", "source": "世界观",
                           "content": f"{w.get('id','')}: {'; '.join(w.get('keywords',[])[:5])}"})
        return results

    def build(self) -> dict:
        """构建三层记忆索引"""
        ch_mem = self._load_chapter_memory()
        sum_mem = self._load_summary_memory()
        arc_mem = self._load_archive_memory()
        return {"chapter_count": len(ch_mem), "summary_count": len(sum_mem),
                "archive_count": len(arc_mem), "total": len(ch_mem)+len(sum_mem)+len(arc_mem)}

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """跨三层检索最相关的记忆"""
        all_memories = (self._load_chapter_memory() +
                       self._load_summary_memory() +
                       self._load_archive_memory())
        if not all_memories: return []
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
        """轻量级实体提取（v16·受KGGen启发，纯正则实现）。"""
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
        """记忆层统计"""
        info = self.build()
        ref_path = self.db / "关系.json"
        refs = load_json(ref_path, {}).get("reference_counts", {})
        forgotten = []
        for cat, items in refs.items():
            for name, data in items.items():
                if isinstance(data, dict) and self.ch - data.get("last_ch", 0) >= 5:
                    forgotten.append(f"{cat}/{name} (last ch{data.get('last_ch')})")
        info["forgotten_elements"] = forgotten[:10]
        return info


def main():
    if len(sys.argv) < 3:
        print("用法: python memory_layer.py <项目路径> <章节号> --action search|build|stats [--query ...]",
              file=sys.stderr); sys.exit(2)
    root = Path(sys.argv[1]).resolve()
    ch = int(sys.argv[2])
    action = "stats"
    query = ""
    for i, a in enumerate(sys.argv):
        if a == "--action" and i+1 < len(sys.argv): action = sys.argv[i+1]
        if a == "--query" and i+1 < len(sys.argv): query = sys.argv[i+1]
    ml = MemoryLayer(root, ch)
    if action == "build":
        print(json.dumps(ml.build(), ensure_ascii=False, indent=2))
    elif action == "search":
        if not query: print("需要 --query 参数", file=sys.stderr); sys.exit(1)
        results = ml.search(query)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif action == "stats":
        print(json.dumps(ml.stats(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
