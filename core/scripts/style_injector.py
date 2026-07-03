"""style_injector.py

把蒸馏库（作者风格_FINAL.json）的 cross_chapter_diversity / golden_passages /
env_anchor_high_risk_elements / narrative_craft 提取为本章可执行的
style_directive，注入到 writer prompt。

修复 v17.3 之前的"蒸馏精细但写作粗糙"断层。

输入：
    python style_injector.py <项目路径> <章节号>

输出：
    _数据库/.style_directive/ch_{ch:03d}.json
    包含：
      - opening_type:  从分布按 (权重 × 反重复轮拿) 选定（连续 N 章避免重复）
      - opening_avoid: 前 N-1 章用过的 type 列表
      - opening_golden_samples: golden_passages.opening_passages 中同 type 的样本（≤2）
      - ending_type / ending_avoid / ending_golden_samples
      - transition_top3
      - anchor_strategy: env_anchor_high_risk_elements 的运行时清单
      - narrative_targets: 节奏指标（hooks/章, subtext/章, scene_pct）
      - applied_style_schema: writer 必须在 CHANGES 中报告的字段格式
"""

import sys
import json
import os
import random
import re
from pathlib import Path
from typing import Optional

# 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测），裸 .items() 会崩。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_lookup  # blueprint list 归一守卫
except Exception:
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_rule_window(rule_text: str, default_n: int = 3) -> int:
    """从 opening_rule/ending_rule 文本解析'连续 N 章'数字。"""
    if not rule_text:
        return default_n
    m = re.search(r"连续\s*(\d+)\s*章", rule_text)
    return int(m.group(1)) if m else default_n


def get_applied_type_history(summaries: list, key: str) -> list[Optional[str]]:
    """从 故事块摘要 chapters 列表抽取历史 applied_*_type。
    summaries 是 list of dict（已经 transform 过 dict→list 的格式）。
    """
    out = []
    for s in summaries:
        applied = s.get("applied_style", {}) or {}
        out.append(applied.get(key))
    return out


def pick_type_weighted_avoiding(
    distribution: dict, avoid: list[str], allow_fallback: bool = True
) -> tuple[Optional[str], str]:
    """从 distribution {type_name: {count, pct, note?}} 按 pct 权重抽样，
    强制避开 avoid 列表中的 type。

    Returns: (chosen_type, reason)
    """
    if not distribution:
        return None, "no_distribution"
    # 过滤掉 avoid
    candidates = [
        (name, info.get("pct", 0))
        for name, info in distribution.items()
        if name not in avoid and info.get("pct", 0) > 0
    ]
    if not candidates:
        if allow_fallback:
            # avoid 把所有 type 占满了，退回最少使用的（pct 最低）
            sorted_all = sorted(distribution.items(), key=lambda x: x[1].get("pct", 0))
            return sorted_all[0][0], "fallback_lowest_pct"
        return None, "no_candidate"
    # 加权抽样
    total = sum(p for _, p in candidates)
    r = random.random() * total
    acc = 0
    for name, p in candidates:
        acc += p
        if r <= acc:
            return name, f"weighted_pick({p:.3f}/{total:.3f})"
    return candidates[-1][0], "fallback_last"


# ── P2·MMR 覆盖式 golden 样本选取（2026-06-14 · 作者思维蒸馏 G6-MMR）──────────
# 旧逻辑「同 type 取前 ≤2」对高方差作者会拿到两段近重复笔法（同一种开场写两遍）。
# 改 MMR：相关性=tag 命中强度 · 多样性=正文文本相似度的补——让 2 个样本覆盖不同笔法。
# 零额外 API（纯字符 bigram 统计）· 确定性（去随机·不引入 temp1.0 外噪声）。

def _char_bigrams(text: str) -> dict:
    """中文正文 → 字符 bigram 计数向量（零依赖·近重复检测足够）。"""
    s = re.sub(r"\s+", "", text or "")
    if len(s) < 2:
        return {s: 1} if s else {}
    out: dict = {}
    for i in range(len(s) - 1):
        bg = s[i:i + 2]
        out[bg] = out.get(bg, 0) + 1
    return out


# ── 🔴 2026-07-02 真语义 embedding 可选路径（照抄 topic_drift_scanner 已验证的模式）───────
def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    与 topic_drift_scanner._has_real_embedding_backend 同口径（本仓约定：每个消费
    embedding 的文件自带一份，不互相 import）。也检查 .env 的 GEN_EMBED__* API 配置。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def _semantic_text_cosine(a: str, b: str) -> "float | None":
    """真语义 embedding 余弦（能捕捉"同一笔法换说法"·字面 bigram 抓不到的改写近似）。

    embedding_store 不可用 / 任一文本编码失败 / 维度不一致 → None（调用方回退字符 bigram）。
    """
    if not a or not b:
        return None
    try:
        from embedding_store import compute_embedding, cosine_similarity
        ea, eb_ = compute_embedding(a), compute_embedding(b)
    except Exception:
        return None
    if not ea or not eb_ or len(ea) != len(eb_):
        return None
    return cosine_similarity(ea, eb_)


def _text_cosine_bigram(a: str, b: str) -> float:
    """两段正文的字符 bigram 余弦相似度（0=完全不同·1=相同）。原逻辑原样抽出（零回归）。"""
    va, vb = _char_bigrams(a), _char_bigrams(b)
    keys = set(va) & set(vb)
    if not keys:
        return 0.0
    import math
    dot = sum(va[k] * vb[k] for k in keys)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _text_cosine(a: str, b: str) -> float:
    """两段正文的相似度（0=完全不同·1=相同）。

    真 embedding 后端就绪（_has_real_embedding_backend()）→ 语义余弦（同一笔法换说法
    也能识别为高相似）；否则/失败 → 退化字符 bigram 余弦（原逻辑·零回归）。
    """
    if _has_real_embedding_backend():
        sim = _semantic_text_cosine(a, b)
        if sim is not None:
            return sim
    return _text_cosine_bigram(a, b)


def _tag_relevance(tag: str, target_type: str) -> float:
    """tag 对 target_type 的命中强度（1.0=整串命中·0.6=分词命中·0=不沾）。"""
    if not target_type:
        return 0.0
    if target_type in tag:
        return 1.0
    if any(t and t in tag for t in target_type.split()):
        return 0.6
    return 0.0


def _passage_text(p: dict) -> str:
    """从 golden passage 取用于相似度比对的正文（兼容多字段命名）。"""
    return str(
        p.get("text") or p.get("passage") or p.get("excerpt")
        or p.get("content") or p.get("sample") or p.get("example") or ""
    )


def mmr_select_passages(
    candidates: list[dict], target_type: str, max_samples: int = 2, alpha: float = 0.7
) -> list[dict]:
    """对候选 golden 段做确定性贪心 MMR 选取（覆盖不同笔法·去近重复）。

    相关性 = _tag_relevance；多样性惩罚 = 与已选段的最大文本相似度。
    每步选 argmax( alpha*rel − (1-alpha)*max_sim_to_selected )；并列按原序下标小优先（去随机）。
    """
    pool = list(enumerate(candidates))
    if not pool or max_samples <= 0:
        return []
    rel = {idx: _tag_relevance(p.get("tag", ""), target_type) for idx, p in pool}
    # 起点：相关性最高（并列取原序最前·确定性）
    pool.sort(key=lambda ip: (-rel[ip[0]], ip[0]))
    selected = [pool.pop(0)]
    while pool and len(selected) < max_samples:
        best = None
        best_key = None
        for idx, p in pool:
            max_sim = max(
                (_text_cosine(_passage_text(p), _passage_text(sp)) for _, sp in selected),
                default=0.0,
            )
            mmr = alpha * rel[idx] - (1.0 - alpha) * max_sim
            key = (-mmr, idx)   # score 高优先·并列原序小优先
            if best_key is None or key < best_key:
                best_key = key
                best = (idx, p)
        selected.append(best)
        pool.remove(best)
    return [p for _, p in selected]


def get_golden_samples_for_type(
    passages: list, target_type: str, max_samples: int = 2
) -> list[dict]:
    """从 golden_passages.opening_passages/ending_passages 中筛 tag 含 target_type 的样本。

    P2：命中候选 > max_samples 时用 MMR 覆盖式选取（不同笔法）取代「取前 N」近重复。
    """
    if not passages or not target_type:
        return []
    hits = [
        p for p in passages
        if target_type in p.get("tag", "")
        or any(t in p.get("tag", "") for t in target_type.split())
    ]
    if not hits and passages:
        # 没匹配上时退回前 1 个（与历史行为一致）
        return passages[:1]
    if len(hits) <= max_samples:
        return hits   # 候选不多于配额 → 不强上 diversity（避免过度工程）
    return mmr_select_passages(hits, target_type, max_samples=max_samples)


def build_directive(project_root: Path, chapter: int) -> dict:
    # v17.5 P1.2 修复：用 hashlib 而非 hash()（后者受 PYTHONHASHSEED 影响）
    import hashlib
    seed_str = f"{project_root.name}|{chapter}"
    seed_int = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
    random.seed(seed_int)


    db = project_root / "_数据库"
    style = load_json(db / "作者风格.json", {})
    # 2026-05-30 北极星复审：v2 账本 clusters[].chapters{} 拍平 + 兼容旧顶层/dict（原读恒空、反重复历史失效）
    _ss_doc = load_json(db / "故事块摘要.json", {})
    summaries = [c for c in (_ss_doc.get("chapters") or []) if isinstance(c, dict)]
    for _c in _ss_doc.get("clusters", []) or []:
        if isinstance(_c, dict):
            for _k, _r in (_c.get("chapters") or {}).items():
                if isinstance(_r, dict):
                    summaries.append({**_r, "ch": int(_k) if str(_k).isdigit() else _r.get("ch", 0)})
    _progress = load_json(db / "进度.json", {})
    cluster_blueprints = []
    # 2026-05-29 复审复修 SC-1：blueprint 可能是 list（城南实测），先归一成 dict 再迭代。
    if cluster_lookup is not None:
        _bp = cluster_lookup.normalize_blueprint(_progress)
    else:
        _bp = _progress.get("cluster_blueprint") or {}
        if not isinstance(_bp, dict):
            _bp = {}
    for cid, cdata in _bp.items():
        if not isinstance(cdata, dict):
            continue
        cluster_blueprints.extend(cdata.get("scene_storyboard", []))

    # 1) 取 cross_chapter_diversity
    diversity = style.get("cross_chapter_diversity", {})
    opening_dist = (
        diversity.get("opening_type_distribution_300ch")
        or diversity.get("opening_type_distribution_60ch")
        or {}
    )
    ending_dist = (
        diversity.get("ending_type_distribution_300ch")
        or diversity.get("ending_type_distribution_60ch")
        or {}
    )
    transition_dist = (
        diversity.get("transition_type_distribution_100seg")
        or diversity.get("transition_type_distribution_60ch")
        or {}
    )
    opening_rule = diversity.get("opening_rule", "")
    ending_rule = diversity.get("ending_rule", "")

    # 2) 解析反重复窗口
    opening_window = parse_rule_window(opening_rule, 3)
    ending_window = parse_rule_window(ending_rule, 3)

    # 3) 取历史 applied_style（前 N-1 章）
    recent_summaries = summaries[-max(opening_window, ending_window):] if summaries else []
    recent_opening = [
        s.get("applied_style", {}).get("opening_type") for s in recent_summaries
    ]
    recent_ending = [
        s.get("applied_style", {}).get("ending_type") for s in recent_summaries
    ]
    # 同时把 故事块摘要 里历史的 tone/hooks 当弱信号也参考
    recent_opening_clean = [x for x in recent_opening if x]
    recent_ending_clean = [x for x in recent_ending if x]

    # 4) 选 opening_type / ending_type
    chosen_opening, op_reason = pick_type_weighted_avoiding(
        opening_dist, avoid=recent_opening_clean[-(opening_window - 1):]
    )
    chosen_ending, en_reason = pick_type_weighted_avoiding(
        ending_dist, avoid=recent_ending_clean[-(ending_window - 1):]
    )

    # 5) 取 top-3 过渡
    transition_sorted = sorted(
        [(k, v.get("pct", 0)) for k, v in transition_dist.items()],
        key=lambda x: -x[1],
    )
    transition_top3 = [k for k, _ in transition_sorted[:3]]

    # 6) golden samples
    gp = style.get("golden_passages", {})
    opening_samples = get_golden_samples_for_type(
        gp.get("opening_passages", []), chosen_opening or "", max_samples=2
    )
    ending_samples = get_golden_samples_for_type(
        gp.get("ending_passages", []), chosen_ending or "", max_samples=2
    )
    transition_samples = gp.get("transition_passages", [])[:2]

    # 7) anchor strategy
    anchor_strategy = diversity.get("env_anchor_high_risk_elements", [])

    # 8) narrative craft targets
    nc = diversity.get("narrative_craft", {})
    narrative_targets = {
        "hooks_per_chapter_target": nc.get("hooks_per_chapter_avg", 3),
        "subtext_per_chapter_target": nc.get("subtext_instances_per_chapter_avg", 2),
        "scene_pct_range": nc.get("scene_vs_summary", {}).get(
            "range", "0.60-0.92"
        ),
        "scene_pct_avg": nc.get("scene_vs_summary", {}).get(
            "scene_pct_avg_300ch", 0.7
        ),
        "hook_positions": nc.get("hook_positions", {}),
        "expectation_methods": nc.get("expectation_methods", []),
        "subtext_types": nc.get("subtext_types", []),
    }

    # 9) anti_patterns runtime
    ap = style.get("anti_patterns", {})
    anti_patterns_runtime = {
        "never_words": ap.get("never_words", []),
        "never_dialogue_tags": ap.get("never_dialogue_tags", []),
        "never_sentence_patterns": ap.get("never_sentence_patterns", []),
        "never_transitions": ap.get("never_transitions", []),
    }

    # 10) core_techniques 可选执行清单（每章至少用 N 个）
    core_techs = (
        diversity.get("author_distinctiveness_indicators", {}).get(
            "core_techniques", []
        )
    )

    # 11) 本章 cluster_blueprint 摘要
    this_plan = next((p for p in cluster_blueprints if p.get("ch") == chapter), {})

    # 12) 拼装 directive
    directive = {
        "chapter": chapter,
        "title": this_plan.get("title", ""),
        "scene_type": this_plan.get("scene_type", []),
        "inherits_opening_from_prev_dcas": False,
        "pre_opening_path": None,
        "opening_type_enforcement": "strict",
        "opening_type": chosen_opening,
        "opening_pick_reason": op_reason,
        "opening_avoid": recent_opening_clean[-(opening_window - 1):],
        "opening_rule": opening_rule,
        "opening_golden_samples": opening_samples,
        # === 结尾强制约束 ===
        "ending_type": chosen_ending,
        "ending_pick_reason": en_reason,
        "ending_avoid": recent_ending_clean[-(ending_window - 1):],
        "ending_rule": ending_rule,
        "ending_golden_samples": ending_samples,
        # === 过渡推荐 ===
        "transition_top3": transition_top3,
        "transition_golden_samples": transition_samples,
        # === 环境锚点策略 ===
        "anchor_strategy": anchor_strategy,
        # === 节奏指标 ===
        "narrative_targets": narrative_targets,
        # === 反 AI 模式（运行时强制）===
        "anti_patterns_runtime": anti_patterns_runtime,
        # === 核心技法（每章至少 1 个）===
        "core_techniques_pool": core_techs,
        "core_techniques_min_per_chapter": 1,
        # === writer 必报字段 schema ===
        "applied_style_schema": {
            "_doc": "writer 必须在 CHANGES JSON 中输出此对象，validator 会复核",
            "opening_type": "string (从 opening_type_distribution 中选一个)",
            "opening_line": "string (本章首句正文)",
            "opening_justification": "string (≤30 字解释为什么这首句符合该 type)",
            "ending_type": "string",
            "ending_line": "string (本章末句正文)",
            "ending_justification": "string (≤30 字)",
            "transitions_used": "list of {position, style}",
            "anchors_hit": "list of string (从 anchor_strategy 中实际命中的锚点)",
            "core_techniques_applied": "list of string (从 core_techniques_pool 选用的)",
            "subtext_count": "int",
            "hooks_count": "int",
        },
    }
    return directive


def main():
    if len(sys.argv) != 3:
        print("用法: python style_injector.py <项目路径> <章节号>")
        sys.exit(1)
    project_root = Path(sys.argv[1])
    chapter = int(sys.argv[2])
    if not project_root.exists():
        print(f"[FATAL] 项目路径不存在: {project_root}")
        sys.exit(1)
    style_path = project_root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        print(f"[FATAL] 找不到作者风格.json: {style_path}")
        print(f"  提示：自由模式无需 style_directive。仅在加载了风格库的项目使用本工具。")
        sys.exit(1)
    directive = build_directive(project_root, chapter)
    out_dir = project_root / "_数据库" / ".style_directive"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ch_{chapter:03d}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(directive, f, ensure_ascii=False, indent=2)
    print(f"[OK] style_directive 已生成: {out_path}")
    print(
        f"  opening_type = {directive['opening_type']} ({directive['opening_pick_reason']}) "
        f"avoid={directive['opening_avoid']}"
    )
    print(
        f"  ending_type  = {directive['ending_type']} ({directive['ending_pick_reason']}) "
        f"avoid={directive['ending_avoid']}"
    )
    print(f"  transitions  = {directive['transition_top3']}")


if __name__ == "__main__":
    main()
