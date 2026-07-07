#!/usr/bin/env python3
"""
rag_retriever.py — 轻量级RAG检索模块（章节级）

解决超长篇小说（50+章）的上下文遗忘问题。
build_manifest.py 调用本模块检索与当前章节最相关的历史内容。

两种模式：
  1. TF-IDF模式（默认，纯Python，无外部依赖）
  2. Embedding模式（需配置.env，调用外部API，精度更高）

A6 检索三段式（2026-07-08 二轮移植·AI_NovelGenerator）：query 扩展（brief 实体×属性组合词组）
→ 时间距离防复读（块距 [NEAR_ECHO_RISK]/[PARAPHRASE]/[OK]）→ 用途标注（对话/冲突/世界观/前情
启发式）。后两段合成每条结果的 usage_hint（advisory）。

用法：
  python rag_retriever.py <项目路径> <章节号> [--top-k 3] [--mode tfidf|embedding]

输出：JSON列表，每项含 {chapter, score, snippet, usage_hint}
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


def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方回退 TF-IDF）。
    """
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
    """伏笔/角色 tier → importance 权重（B1：tier→importance 贯通·分桶变排序）。

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

    # B1: importance 先验权重（伏笔 tier→importance）融进 score。importance_weight 默认 0
    # → _imp 恒 0 → 与原行为逐字节一致（零回归）。weight>0 时高 tier 内容在检索中优先。
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


# ============ A6 检索三段式（2026-07-08 二轮移植 · AI_NovelGenerator
# prompt_definitions.py:61-158 + chapter.py:176-216 · research/open_source_writing_systems_round2.md A6）
#
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
    """A6-3 用途粗分类（启发式·确定性·零 LLM）。优先级：对话 > 冲突 > 世界观 > 前情兜底。"""
    t = str(text or "")
    if any(m in t for m in _DIALOGUE_MARKS):
        return "对话风格参考"
    if len(_CONFLICT_RE.findall(t)) >= 2:
        return "冲突节奏参考"
    if len(_WORLDBUILDING_RE.findall(t)) >= 2:
        return "世界观碎片"
    return "前情事实参考"


def echo_tag(cluster_distance) -> "str | None":
    """A6-2 块距 → 防复读标签。块距不可知（cluster 反查失败）→ None（诚实不臆造）。"""
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


def annotate_usage_hints(project_root, current_ch: int, results: list) -> list:
    """给检索命中批量打 usage_hint（原地修改并返回）。

    块距经 cluster_lookup 权威反查（章→cluster·禁机械拼接）；反查不可用/失败 → 只打
    用途分类不带距离标签。条目兼容 chapter（rag）/ ch（selective_history）两种键。
    """
    cur_num = None
    if cluster_lookup is not None:
        try:
            cur_num = cluster_lookup.cluster_num(
                cluster_lookup.ch_to_cluster_id(project_root, current_ch))
        except Exception:
            cur_num = None
    for r in results:
        if not isinstance(r, dict):
            continue
        text = r.get("snippet") or r.get("text_preview") or ""
        dist = None
        src_ch = r.get("chapter", r.get("ch"))
        if cur_num is not None and isinstance(src_ch, int):
            try:
                src_num = cluster_lookup.cluster_num(
                    cluster_lookup.ch_to_cluster_id(project_root, src_ch))
            except Exception:
                src_num = None
            if src_num is not None:
                dist = cur_num - src_num
        r["usage_hint"] = build_usage_hint(dist, text)
    return results


def _find_brief_for_ch(project_root, current_ch: int) -> "dict | None":
    """定位当前章所属 cluster 的 brief（事件簇.json）。优先 cluster_lookup 权威反查，
    退 chapter_range 扫描。找不到 → None（调用方零变化）。"""
    root = Path(project_root)
    db = root if root.name == "_数据库" else root / "_数据库"
    path = db / "事件簇.json"
    if not path.exists():
        return None
    try:
        clusters = (json.loads(path.read_text(encoding="utf-8")) or {}).get("clusters") or []
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return None
    target = None
    if cluster_lookup is not None:
        try:
            target = cluster_lookup.ch_to_cluster_id(project_root, current_ch)
        except Exception:
            target = None
    if target is not None and cluster_lookup is not None:
        for c in clusters:
            if (isinstance(c, dict)
                    and cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == target):
                return c
    for c in clusters:
        if not isinstance(c, dict):
            continue
        cr = c.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2 and cr[0] <= current_ch <= cr[1]:
            return c
    return None


def expand_query_from_brief(project_root, current_ch: int, max_groups: int = 5) -> list[str]:
    """A6-1 query 扩展：cluster brief 实体×属性组合词组（3-5 组·纯确定性拼装·零 LLM）。

    实体 = characters_focus + storyboard characters/focal_character/location + anchor_props
    + hub_locations（保序去重）；属性 = scope_summary 的 CJK 词串关键词（截 4 字·剔除与
    实体重叠项）。每组 = 实体 + 2 个属性关键词轮转配对。
    无 brief / 无实体 / 无 scope 关键词 → []（调用方 query 零变化）。
    """
    brief = _find_brief_for_ch(project_root, current_ch)
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
        kw = run[:4]
        if kw in kws:
            continue
        if any(kw in e or e in kw for e in entities):
            continue
        kws.append(kw)
    if not kws:
        return []
    groups: list[str] = []
    for i, ent in enumerate(entities[:max_groups]):
        attrs = list(dict.fromkeys([kws[(2 * i) % len(kws)], kws[(2 * i + 1) % len(kws)]]))
        groups.append(" ".join([ent] + attrs))
    return groups


def _load_retrieval_corpus(project_root, current_ch: int):
    """章节正文 + 摘要 + 当前章 plan(query) 统一装载 —— TF-IDF / embedding 两种检索模式共用。

    返回 None（无可检索历史章 或 当前章无 plan 信号）或 (ch_nums, docs, chapters, summaries)：
      - ch_nums: 排序后的历史章号列表
      - docs: 与 ch_nums 一一对应的候选文本 + 末项是当前章 query
        （A6-1：brief 实体×属性扩展词组 + current_plan；无 brief 信号时纯 current_plan）
      - chapters/summaries: 供结果 snippet 回填用
    """
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
        return None

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
        return None

    # A6-1 query 扩展（2026-07-08）：brief 实体×属性组合词组前置拼入 query（entity-anchored
    # 检索更贴本块出场角色/道具/地点）。无 brief 信号 → 纯 current_plan（零变化）。
    expansion = expand_query_from_brief(project_root, current_ch)
    query_doc = ("\n".join(expansion) + "\n" + current_plan) if expansion else current_plan

    ch_nums = sorted(chapters.keys())
    docs = []
    for ch in ch_nums:
        text = summaries.get(ch, '') or chapters[ch][:500]
        docs.append(text)
    docs.append(query_doc)
    return ch_nums, docs, chapters, summaries


def retrieve_tfidf(project_root, current_ch: int, top_k: int = 3,
                   use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    corpus = _load_retrieval_corpus(project_root, current_ch)
    if corpus is None:
        return []
    ch_nums, docs, chapters, summaries = corpus

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
    # A6-2/3：块距防复读标签 + 用途标注 → usage_hint（advisory）
    return annotate_usage_hints(project_root, current_ch, results)


# 金标准校准 2026-07-04：content_embed_separability_20260704 报告——候选相关性下限。
# 检索排序类地板取宽松位：bge 值域下五类分布 p5 普遍落在 0.30-0.33（如
# neg_cross_book p5=0.324 / summary_vs_body_neg p5=0.3001），0.30 只滤掉最极端的不相关
# 尾部、把排序留给 MMR 重排，不在地板上压召回（原 0.01 在 bge 值域下等于不过滤）。
_EMBED_MIN_RELEVANCE = 0.30


def retrieve_embedding(project_root, current_ch: int, top_k: int = 3,
                       use_mmr: bool = True, mmr_alpha: float = 0.7) -> list[dict]:
    """v17.6 D2 → 2026-07-04 换轨内容语义嵌入 API：内容语义检索模式
    （SCORE 框架 hybrid retrieval 第二轮）。

    门控 = content_backend_available()（venv+infer 脚本+模型目录三者俱在·统一走
    embedding_store，不再按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测）。
    无内容后端 → 退 TF-IDF（与此前行为、fallback 标签一致·零回归）。
    真后端 → prefetch_content_embeddings 一次批量预热历史章文本 + 当前章 plan(query)，随后
    compute_content_embedding 逐条命中缓存，cosine_similarity 排序，MMR 重排复用 mmr_rerank
    （sim_fn 换成 embedding 余弦）。

    实测：SCORE 测试 TF-IDF + 语义 = 23.6% coherence 提升 vs 纯 TF-IDF。
    """
    def _fallback():
        results = retrieve_tfidf(project_root, current_ch, top_k, use_mmr, mmr_alpha)
        for r in results:
            r["mode"] = "tfidf_fallback (embedding not implemented yet)"
        return results

    if not _content_backend_ready():
        print("[INFO] 无内容语义后端（venv/模型目录未就绪），降级 TF-IDF 模式", file=sys.stderr)
        return _fallback()

    try:
        from embedding_store import (compute_content_embedding, cosine_similarity,
                                      prefetch_content_embeddings)
    except (ImportError, TypeError):
        print("[INFO] embedding_store 不可用，降级 TF-IDF 模式", file=sys.stderr)
        return _fallback()

    corpus = _load_retrieval_corpus(project_root, current_ch)
    if corpus is None:
        return []
    ch_nums, docs, chapters, summaries = corpus

    # 🔴 2026-07-03 Wave-4：docs=历史章语料+当前章 query，一次批量预热缓存（真后端子进程/API
    # 只付一次成本），随后逐条 compute_content_embedding 全部命中缓存。
    prefetch_content_embeddings(docs)
    embs = [compute_content_embedding(d) for d in docs]
    query_emb = embs[-1]
    # 维度一致性守卫：内容 API 编码失败返回 None（不像旧 compute_embedding 有 hash 兜底
    # 保证恒为某维度向量）——先判 None 再比长度，维度混用/编码失败直接跳过语义检索退 TF-IDF
    # （不冒充语义）。
    if not query_emb or any((e is None) or len(e) != len(query_emb) for e in embs):
        print("[INFO] embedding 编码失败或维度不一致，降级 TF-IDF 模式", file=sys.stderr)
        return _fallback()

    rel = {i: cosine_similarity(embs[i], query_emb) for i in range(len(ch_nums))}
    cand = [i for i in range(len(ch_nums)) if rel[i] >= _EMBED_MIN_RELEVANCE]
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
        ch = ch_nums[i]
        results.append({
            'chapter': ch,
            'score': round(rel[i], 4),
            'snippet': _snippet(summaries.get(ch, '') or chapters.get(ch, '')[:300]),
            'mode': 'embedding',
        })
    # A6-2/3：块距防复读标签 + 用途标注 → usage_hint（advisory）
    return annotate_usage_hints(project_root, current_ch, results)


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
