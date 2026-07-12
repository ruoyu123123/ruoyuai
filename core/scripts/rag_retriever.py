#!/usr/bin/env python3
"""检索与当前 cluster brief 最相关的历史 cluster。"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402
import cluster_summary_reader as csr  # noqa: E402


def _content_backend_ready() -> bool:
    """返回内容语义后端是否完整就绪。"""
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


def _chinese_tokens(text: str) -> list[str]:
    clean = re.sub(r'[^一-鿿a-zA-Z0-9]', ' ', text)
    chars = re.sub(r'\s+', '', clean)
    tokens = []
    for n in (2, 3, 4):
        tokens.extend(chars[i:i+n] for i in range(len(chars)-n+1))
    words = re.findall(r'[一-鿿]{2,4}', text)
    tokens.extend(words)
    return tokens


def _tfidf_vectors(docs: list[str]) -> tuple[list[dict], dict]:
    doc_tokens = [_chinese_tokens(d) for d in docs]
    df: Counter = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            df[t] += 1
    n = len(docs)
    vectors = []
    for tokens in doc_tokens:
        tf = Counter(tokens)
        total = max(len(tokens), 1)
        vec = {}
        for t, count in tf.items():
            idf = math.log((n + 1) / (df.get(t, 0) + 1)) + 1
            vec[t] = (count / total) * idf
        vectors.append(vec)
    return vectors, df


def _cosine(a: dict, b: dict) -> float:
    keys = set(a.keys()) & set(b.keys())
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tier_to_importance(tier) -> float:
    """把伏笔或角色 tier 映射为检索重要度。

    tier1=核心主线(最重要) / tier2=支线 / tier3=细节。映射 0-1·喂 mmr_rerank importance。
    非法/缺失 tier → 0.2（低·保守）。
    """
    try:
        t = int(tier)
    except (ValueError, TypeError):
        return 0.2
    return {1: 1.0, 2: 0.5, 3: 0.2}.get(t, 0.2)


def mmr_rerank(
    cand_indices: list[int],
    relevance: dict[int, float],
    sim_fn,
    k: int,
    alpha: float = 0.7,
    importance: "dict[int, float] | None" = None,
    importance_weight: float = 0.0,
) -> list[int]:
    """确定性贪心 MMR（Maximal Marginal Relevance）重排，零额外 API。

    治高方差作者纯相似 top-k 的「近重复冗余」：相似度第一名和第二名可能讲同一段笔法，
    MMR 在每步选 argmax( alpha*相关性 − (1-alpha)*与已选集合的最大相似度 )，
    强制覆盖不同笔法/不同历史片段。

    - cand_indices: 候选文档下标
    - relevance[i]: 文档 i 对 query 的相关性（0-1，越大越相关）
    - sim_fn(i, j): 文档 i 与 j 的两两相似度（0-1，对称）
    - k: 取多少个
    - alpha: 权重 ∈ [0.5,0.9]（越大越偏相关性·越小越偏多样性）

    确定性：每步打分相同时按 (−score, 下标) 升序定序——去随机，防成 temp1.0 外又一噪声源。
    """
    if k <= 0 or not cand_indices:
        return []

    # importance_weight 控制重要度先验对 MMR 分数的贡献。
    def _imp(i):
        return (importance.get(i, 0.0) if importance else 0.0) * importance_weight

    # 起点 = 相关性(+importance)最高（并列取最小下标·确定性）
    remaining = list(cand_indices)
    remaining.sort(key=lambda i: (-(relevance.get(i, 0.0) + _imp(i)), i))
    selected: list[int] = [remaining.pop(0)]
    while remaining and len(selected) < k:
        best_i = None
        best_score = None
        for i in remaining:
            max_sim_sel = max((sim_fn(i, s) for s in selected), default=0.0)
            mmr = alpha * relevance.get(i, 0.0) + _imp(i) - (1.0 - alpha) * max_sim_sel
            # 并列：score 高优先，再按下标小优先（去随机）
            key = (-mmr, i)
            if best_score is None or key < best_score:
                best_score = key
                best_i = i
        selected.append(best_i)
        remaining.remove(best_i)
    return selected


def _snippet(text: str, length: int = 200) -> str:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    result = []
    total = 0
    for line in lines:
        if total + len(line) > length:
            break
        result.append(line)
        total += len(line)
    return '\n'.join(result) if result else text[:length]


# 检索提示由查询扩展、块距防复读和用途标注组成。
# 1) query 扩展（确定性）：检索 query 从「当前章 plan 上下文」升级为「cluster brief
#    实体×属性组合词组」（characters/props/location × scope_summary 关键词 · 3-5 组 · 零 LLM）。
# 2) 时间距离防复读（确定性）：命中按块距分级——距当前 cluster ≤1 块 [NEAR_ECHO_RISK]（近块
#    内容禁直接复用·仅作连贯参考）/ 2-3 块 [PARAPHRASE]（需换写）/ >3 块 [OK]。
# 3) 用途标注（确定性启发式）：命中文本特征粗分类（含对话→对话风格参考 / 冲突词密→冲突节奏
#    参考 / 设定名词密→世界观碎片 / 兜底→前情事实参考）。
# 2+3 合成每条结果的 usage_hint 字段。全部 advisory（提示 writer 怎么用检索结果·不硬锁）。
# ============

NEAR_ECHO_NOTE = "近块内容禁直接复用·仅作连贯参考"
PARAPHRASE_NOTE = "需换写（勿沿用原句式原词面）"

_DIALOGUE_MARKS = ("“", "”", "「", "」")  # 中文左右双引号 + 直角引号
_CONFLICT_RE = re.compile(r"[打杀砍劈斩轰撞吼嘶逃追爆战怒拳刀枪血]|冲突|对峙|厮杀|搏斗|威胁")
_WORLDBUILDING_RE = re.compile(
    r"[界域族宗殿庙城国朝盟阵符箓丹窍]|规则|禁忌|设定|体系|等级|品阶|位格|血脉|功法|秘境|结界")


def classify_usage(text) -> str:
    """确定性分类检索片段用途，优先级为对话、冲突、世界观、前情。"""
    t = str(text or "")
    if any(m in t for m in _DIALOGUE_MARKS):
        return "对话风格参考"
    if len(_CONFLICT_RE.findall(t)) >= 2:
        return "冲突节奏参考"
    if len(_WORLDBUILDING_RE.findall(t)) >= 2:
        return "世界观碎片"
    return "前情事实参考"


def echo_tag(cluster_distance) -> "str | None":
    """把 cluster 距离映射为防复读标签。"""
    if cluster_distance is None:
        return None
    d = abs(int(cluster_distance))
    if d <= 1:
        return "[NEAR_ECHO_RISK]"
    if d <= 3:
        return "[PARAPHRASE]"
    return "[OK]"


def build_usage_hint(cluster_distance, text) -> str:
    """块距标签 + 用途分类 合成 usage_hint（advisory）。"""
    usage = classify_usage(text)
    tag = echo_tag(cluster_distance)
    if tag == "[NEAR_ECHO_RISK]":
        return f"{tag}·{usage}·{NEAR_ECHO_NOTE}"
    if tag == "[PARAPHRASE]":
        return f"{tag}·{usage}·{PARAPHRASE_NOTE}"
    if tag == "[OK]":
        return f"{tag}·{usage}"
    return usage


def _cluster_number(cluster_id: str) -> int:
    number = cluster_lookup.cluster_num(cluster_id)
    if number is None:
        raise ValueError(f"cluster_id 非法: {cluster_id!r}")
    return number


def annotate_usage_hints(current_cluster_id: str, results: list) -> list:
    """按 cluster 距离和文本用途补充检索提示。"""
    cur_num = _cluster_number(current_cluster_id)
    for r in results:
        if not isinstance(r, dict):
            continue
        text = r.get("snippet") or r.get("text_preview") or ""
        source_cluster = r.get("cluster_id")
        if not isinstance(source_cluster, str):
            raise ValueError("检索结果缺少 cluster_id")
        dist = cur_num - _cluster_number(source_cluster)
        r["usage_hint"] = build_usage_hint(dist, text)
    return results


def _find_brief(project_root, cluster_id: str) -> "dict | None":
    """从事件簇定位指定 cluster brief。"""
    root = Path(project_root)
    db = root if root.name == "_数据库" else root / "_数据库"
    path = db / "事件簇.json"
    if not path.exists():
        return None
    try:
        clusters = (json.loads(path.read_text(encoding="utf-8")) or {}).get("clusters") or []
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return None
    for c in clusters:
        if isinstance(c, dict) and c.get("cluster_id") == cluster_id:
            return c
    return None


def expand_query_from_brief(project_root, current_cluster_id: str,
                            max_groups: int = 5) -> list[str]:
    """把 cluster brief 扩展成实体与属性组合词组。

    实体 = characters_focus + storyboard characters/focal_character/location + anchor_props
    + hub_locations（保序去重）；属性 = scope_summary 的 CJK 词串关键词（截 4 字·剔除与
    实体重叠项）。每组 = 实体 + 2 个属性关键词轮转配对。
    无 brief / 无实体 / 无 scope 关键词 → []（调用方 query 零变化）。
    """
    brief = _find_brief(project_root, current_cluster_id)
    if not isinstance(brief, dict):
        return []
    entities: list[str] = []

    def _add(v):
        if isinstance(v, str):
            v = v.strip()
            if v and v not in entities:
                entities.append(v)

    for v in brief.get("characters_focus") or []:
        _add(v)
    for sc in brief.get("scene_storyboard") or []:
        if not isinstance(sc, dict):
            continue
        for v in sc.get("characters") or []:
            _add(v)
        _add(sc.get("focal_character"))
        _add(sc.get("location"))
    for v in brief.get("anchor_props") or []:
        if isinstance(v, dict):
            _add(v.get("name") or v.get("prop") or v.get("id"))
        else:
            _add(v)
    for v in brief.get("hub_locations") or []:
        _add(v)
    scope = str(brief.get("scope_summary") or "")
    if not entities or not scope.strip():
        return []
    kws: list[str] = []
    for run in re.findall(r"[一-鿿]{2,}", scope):
        for width in range(min(4, len(run)), 1, -1):
            for index in range(len(run) - width + 1):
                kw = run[index:index + width]
                if kw in kws or any(kw in e or e in kw for e in entities):
                    continue
                kws.append(kw)
    if not kws:
        return []
    groups: list[str] = []
    for i, ent in enumerate(entities[:max_groups]):
        attrs = list(dict.fromkeys([kws[(2 * i) % len(kws)], kws[(2 * i + 1) % len(kws)]]))
        groups.append(" ".join([ent] + attrs))
    return groups


def _cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    key = cluster_id.removeprefix("cluster_")
    return project_root / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt"


def _load_retrieval_corpus(project_root, current_cluster_id: str):
    """装载历史 cluster 摘要、完整草稿与当前 cluster brief 查询。"""
    project_root = Path(project_root)
    current_number = _cluster_number(current_cluster_id)
    records = [
        record for record in csr.get_clusters(project_root)
        if _cluster_number(record["cluster_id"]) < current_number
    ]
    if not records:
        return None

    brief = _find_brief(project_root, current_cluster_id)
    if not isinstance(brief, dict):
        raise ValueError(f"事件簇缺少当前 brief: {current_cluster_id}")
    scope = str(brief.get("scope_summary") or "").strip()
    storyboard = brief.get("scene_storyboard") or []
    if not scope and not storyboard:
        raise ValueError(f"当前 cluster brief 为空: {current_cluster_id}")

    cluster_ids = []
    drafts = {}
    summaries = {}
    docs = []
    for record in records:
        cluster_id = record["cluster_id"]
        path = _cluster_draft_path(project_root, cluster_id)
        try:
            draft = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError(f"cluster 终稿不存在: {path}") from exc
        if not draft.strip():
            raise ValueError(f"cluster 终稿为空: {path}")
        cluster_ids.append(cluster_id)
        drafts[cluster_id] = draft
        summaries[cluster_id] = record["summary"]
        docs.append(record["summary"] + "\n" + draft[:1500])

    current_plan = scope + "\n" + json.dumps(storyboard, ensure_ascii=False)
    expansion = expand_query_from_brief(project_root, current_cluster_id)
    query_doc = ("\n".join(expansion) + "\n" + current_plan) if expansion else current_plan
    docs.append(query_doc)
    return cluster_ids, docs, drafts, summaries


def retrieve_tfidf(project_root, current_cluster_id: str, top_k: int = 3,
                   use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    corpus = _load_retrieval_corpus(project_root, current_cluster_id)
    if corpus is None:
        return []
    cluster_ids, docs, drafts, summaries = corpus

    vectors, _ = _tfidf_vectors(docs)
    query_vec = vectors[-1]
    # 每个候选章对 query 的相关性（按下标存·MMR 与回填都按下标取）
    rel = {i: _cosine(vectors[i], query_vec) for i in range(len(cluster_ids))}
    # 先过相关性下限（< 0.01 视为不相关·与历史行为一致），得候选池
    cand = [i for i in range(len(cluster_ids)) if rel[i] >= 0.01]
    if not cand:
        return []

    # MMR 覆盖式选取（治近重复冗余）：候选 > top_k 才有去冗余意义；候选 ≤ top_k 时
    # MMR 退化为纯相关性排序（不强上 diversity·避免简单场景过度工程）。
    def _doc_sim(i: int, j: int) -> float:
        return _cosine(vectors[i], vectors[j])

    if use_mmr and len(cand) > top_k:
        order = mmr_rerank(cand, rel, _doc_sim, top_k, alpha=mmr_alpha)
    else:
        # 纯 top-k（确定性：score 高优先·并列下标小优先）
        order = sorted(cand, key=lambda i: (-rel[i], i))[:top_k]

    results = []
    for i in order:
        cluster_id = cluster_ids[i]
        results.append({
            'cluster_id': cluster_id,
            'score': round(rel[i], 4),
            'snippet': _snippet(summaries[cluster_id] or drafts[cluster_id][:300]),
        })
    return annotate_usage_hints(current_cluster_id, results)


# 内容嵌入校准报告的候选相关性下限。
_EMBED_MIN_RELEVANCE = 0.30


def retrieve_embedding(project_root, current_cluster_id: str, top_k: int = 3,
                       use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    """用内容嵌入相似度和 MMR 检索历史 cluster。"""
    if not _content_backend_ready():
        raise RuntimeError("内容语义后端未就绪")

    try:
        from embedding_store import (compute_content_embedding, cosine_similarity,
                                      prefetch_content_embeddings)
    except (ImportError, TypeError) as exc:
        raise RuntimeError("embedding_store 不可用") from exc

    corpus = _load_retrieval_corpus(project_root, current_cluster_id)
    if corpus is None:
        return []
    cluster_ids, docs, drafts, summaries = corpus

    # 一次预热全部历史 cluster 文本和当前 brief 查询。
    prefetch_content_embeddings(docs)
    embs = [compute_content_embedding(d) for d in docs]
    query_emb = embs[-1]
    if not query_emb or any((e is None) or len(e) != len(query_emb) for e in embs):
        raise RuntimeError("embedding 编码失败或维度不一致")

    rel = {i: cosine_similarity(embs[i], query_emb) for i in range(len(cluster_ids))}
    cand = [i for i in range(len(cluster_ids)) if rel[i] >= _EMBED_MIN_RELEVANCE]
    if not cand:
        return []

    def _doc_sim(i: int, j: int) -> float:
        return cosine_similarity(embs[i], embs[j])

    if use_mmr and len(cand) > top_k:
        order = mmr_rerank(cand, rel, _doc_sim, top_k, alpha=mmr_alpha)
    else:
        order = sorted(cand, key=lambda i: (-rel[i], i))[:top_k]

    results = []
    for i in order:
        cluster_id = cluster_ids[i]
        results.append({
            'cluster_id': cluster_id,
            'score': round(rel[i], 4),
            'snippet': _snippet(summaries[cluster_id] or drafts[cluster_id][:300]),
            'mode': 'embedding',
        })
    return annotate_usage_hints(current_cluster_id, results)


def main():
    parser = argparse.ArgumentParser(description="cluster 相关历史检索")
    parser.add_argument("project")
    parser.add_argument("cluster_id")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--mode", choices=("tfidf", "embedding", "hybrid"), default="tfidf")
    parser.add_argument("--no-mmr", action="store_true")
    parser.add_argument("--mmr-alpha", type=float, default=0.7)
    args = parser.parse_args()
    project_root = Path(args.project).resolve()
    use_mmr = not args.no_mmr

    if args.mode == "embedding":
        results = retrieve_embedding(
            project_root, args.cluster_id, args.top_k, use_mmr, args.mmr_alpha
        )
    elif args.mode == "hybrid":
        a = retrieve_tfidf(
            project_root, args.cluster_id, args.top_k, use_mmr, args.mmr_alpha
        )
        b = retrieve_embedding(
            project_root, args.cluster_id, args.top_k, use_mmr, args.mmr_alpha
        )
        seen = set()
        results = []
        for r in a + b:
            if r["cluster_id"] not in seen:
                seen.add(r["cluster_id"])
                results.append(r)
            if len(results) >= args.top_k:
                break
    else:
        results = retrieve_tfidf(
            project_root, args.cluster_id, args.top_k, use_mmr, args.mmr_alpha
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    

if __name__ == '__main__':
    main()
