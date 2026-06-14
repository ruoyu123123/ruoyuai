#!/usr/bin/env python3
"""
rag_retriever.py — 轻量级RAG检索模块（章节级）

解决超长篇小说（50+章）的上下文遗忘问题。
build_manifest.py 调用本模块检索与当前章节最相关的历史内容。

两种模式：
  1. TF-IDF模式（默认，纯Python，无外部依赖）
  2. Embedding模式（需配置.env，调用外部API，精度更高）

用法：
  python rag_retriever.py <项目路径> <章节号> [--top-k 3] [--mode tfidf|embedding]
  
输出：JSON列表，每项含 {chapter, score, snippet}
"""
from __future__ import annotations
import json, math, re, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写
try:
    import cluster_lookup  # 2026-05-29 复审修复：SC-1 blueprint list 归一守卫
except Exception:  # 防御：缺模块时不影响主检索路径
    cluster_lookup = None


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


def mmr_rerank(
    cand_indices: list[int],
    relevance: dict[int, float],
    sim_fn,
    k: int,
    alpha: float = 0.7,
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
    # 起点 = 相关性最高（并列取最小下标·确定性）
    remaining = list(cand_indices)
    remaining.sort(key=lambda i: (-relevance.get(i, 0.0), i))
    selected: list[int] = [remaining.pop(0)]
    while remaining and len(selected) < k:
        best_i = None
        best_score = None
        for i in remaining:
            max_sim_sel = max((sim_fn(i, s) for s in selected), default=0.0)
            mmr = alpha * relevance.get(i, 0.0) - (1.0 - alpha) * max_sim_sel
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


def retrieve_tfidf(project_root, current_ch: int, top_k: int = 3,
                   use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    # v17.5 修复：接受 str 或 Path
    project_root = Path(project_root) if not isinstance(project_root, Path) else project_root
    chapters = {}
    # v18：枚举章节号后统一走 cio.read_body() 取纯正文
    # （v18 已分离的直读 txt；旧混合 txt 自动剥离 CHANGES 段，杜绝各自 split）
    ch_nums_found = set()
    for f in (list(project_root.glob('章节/第*章/第*章*.txt'))
              + list(project_root.glob('第*章*.txt'))):
        if f.name.endswith('_changes.json'):
            continue
        m = re.search(r'第(\d+)章', f.name)
        if m:
            ch_nums_found.add(int(m.group(1)))
    for ch_num in sorted(ch_nums_found):
        if ch_num >= current_ch:
            continue
        try:
            chapters[ch_num] = cio.read_body(project_root, ch_num)
        except FileNotFoundError:
            continue

    if not chapters:
        return []

    summaries_path = project_root / '_数据库' / '故事块摘要.json'
    summaries = {}
    if summaries_path.exists():
        data = json.loads(summaries_path.read_text(encoding='utf-8'))
        # 2026-05-30 北极星复审：v2 账本 clusters[].chapters{} 拍平 + 兼容旧顶层 chapters（原读恒空、历史检索失效）
        _rows = [c for c in (data.get('chapters') or []) if isinstance(c, dict)]
        for _c in data.get('clusters', []) or []:
            if isinstance(_c, dict):
                for _k, _r in (_c.get('chapters') or {}).items():
                    if isinstance(_r, dict):
                        _rows.append({**_r, 'ch': int(_k) if str(_k).isdigit() else _r.get('ch', 0)})
        for s in _rows:
            ch = s.get('ch', s.get('chapter', 0))
            summaries[ch] = s.get('summary', '')

    plan_path = project_root / '_数据库' / '进度.json'
    current_plan = ''
    if plan_path.exists():
        progress = json.loads(plan_path.read_text(encoding='utf-8'))
        # 2026-05-29 复审修复：SC-1 — cluster_blueprint 规范形态=dict；城南项目实测为
        # list(25)（每项是逐章 scene 记录），裸 .items() 会 AttributeError 崩在 writer
        # 写作前检索路径。先 normalize_blueprint 归一成 dict 再迭代。
        if cluster_lookup is not None:
            _bp = cluster_lookup.normalize_blueprint(progress)
        else:
            _bp = progress.get('cluster_blueprint', {})
            if not isinstance(_bp, dict):
                _bp = {}
        _all_scenes = []
        for cid, cdata in _bp.items():
            if not isinstance(cdata, dict):
                continue
            _all_scenes.extend(cdata.get('scene_storyboard', []) or [])
        for p in _all_scenes:
            if p.get('ch') == current_ch:
                current_plan = json.dumps(p, ensure_ascii=False)
                break

    if not current_plan:
        return []

    ch_nums = sorted(chapters.keys())
    docs = []
    for ch in ch_nums:
        text = summaries.get(ch, '') or chapters[ch][:500]
        docs.append(text)
    docs.append(current_plan)

    vectors, _ = _tfidf_vectors(docs)
    query_vec = vectors[-1]
    # 每个候选章对 query 的相关性（按下标存·MMR 与回填都按下标取）
    rel = {i: _cosine(vectors[i], query_vec) for i in range(len(ch_nums))}
    # 先过相关性下限（< 0.01 视为不相关·与历史行为一致），得候选池
    cand = [i for i in range(len(ch_nums)) if rel[i] >= 0.01]
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
        ch = ch_nums[i]
        results.append({
            'chapter': ch,
            'score': round(rel[i], 4),
            'snippet': _snippet(summaries.get(ch, '') or chapters.get(ch, '')[:300]),
        })
    return results


def retrieve_embedding(project_root, current_ch: int, top_k: int = 3,
                       use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    """v17.6 D2: embedding 模式（SCORE 框架 hybrid retrieval 第二轮）。

    优先级：如果设置 OPENAI_API_KEY 环境变量 → 调 OpenAI；
    否则自动降级到 TF-IDF。

    实测：SCORE 测试 TF-IDF + 语义 = 23.6% coherence 提升 vs 纯 TF-IDF。
    MMR 重排参数透传 TF-IDF 降级路径（embedding 实装后亦复用 mmr_rerank·零额外 API）。
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[INFO] OPENAI_API_KEY 未设，降级 TF-IDF 模式", file=sys.stderr)
        return retrieve_tfidf(project_root, current_ch, top_k, use_mmr, mmr_alpha)
    try:
        # 延迟导入，避免无 openai 包时报错
        import openai
    except ImportError:
        print("[INFO] openai 包未安装，降级 TF-IDF 模式", file=sys.stderr)
        return retrieve_tfidf(project_root, current_ch, top_k, use_mmr, mmr_alpha)
    # 简化版：仅占位，实际生产用 OpenAI/Cohere embedding API
    # TODO: 调用 openai.embeddings.create() 用 text-embedding-3-small + mmr_rerank 重排
    # 当前先返回 TF-IDF 结果 + 标记 fallback
    results = retrieve_tfidf(project_root, current_ch, top_k, use_mmr, mmr_alpha)
    for r in results:
        r["mode"] = "tfidf_fallback (embedding not implemented yet)"
    return results


def main():
    if len(sys.argv) < 3:
        print('用法: python rag_retriever.py <项目路径> <章节号> [--top-k 3] [--mode tfidf|embedding|hybrid] [--no-mmr] [--mmr-alpha 0.7]', file=sys.stderr)
        sys.exit(2)

    project_root = Path(sys.argv[1]).resolve()
    chapter = int(sys.argv[2])
    top_k = 3
    mode = "tfidf"
    use_mmr = True        # P2 默认开 MMR 覆盖式选取（治近重复冗余）
    mmr_alpha = 0.7
    for i, a in enumerate(sys.argv):
        if a == '--top-k' and i+1 < len(sys.argv):
            top_k = int(sys.argv[i+1])
        if a == '--mode' and i+1 < len(sys.argv):
            mode = sys.argv[i+1]
        if a == '--no-mmr':
            use_mmr = False
        if a == '--mmr-alpha' and i+1 < len(sys.argv):
            mmr_alpha = float(sys.argv[i+1])

    if mode == "embedding":
        results = retrieve_embedding(project_root, chapter, top_k, use_mmr, mmr_alpha)
    elif mode == "hybrid":
        # 双路融合：TF-IDF + embedding 各取 top_k，按分数加权合并
        a = retrieve_tfidf(project_root, chapter, top_k, use_mmr, mmr_alpha)
        b = retrieve_embedding(project_root, chapter, top_k, use_mmr, mmr_alpha)
        # 简化：去重 + 取并集
        seen = set()
        results = []
        for r in a + b:
            if r["chapter"] not in seen:
                seen.add(r["chapter"])
                results.append(r)
            if len(results) >= top_k:
                break
    else:
        results = retrieve_tfidf(project_root, chapter, top_k, use_mmr, mmr_alpha)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    

if __name__ == '__main__':
    main()
