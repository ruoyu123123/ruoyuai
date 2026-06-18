"""dimension_evolver.py — DEPRECATED (2026-06-18 SkillOpt 替代)

⚠️ DEPRECATED: 本脚本被 core/scripts/skill_opt/ 范式替代。
- 旧:蒸馏后扫 F 段提议→双门槛升维度→注入下次 B7 段
- 新:每 epoch rollout trajectory→optimizer 直接改 skill_FAST.md→validation gate
SkillOpt 业界源 arXiv:2605.23904 + microsoft/SkillOpt (推荐用 train.py)

E1 调研 (workspace/_temp_research/skillopt重构/E1_蒸馏系统现状.md) 实测:
本脚本 591 行,被任何 plan 模板调用 0 次,grep --include='*.json' 零命中。
保留入口仅为兼容性,实际功能已被 SkillOpt 优化器吸收。
迁移指南: python core/scripts/skill_opt/train.py --project ... --skill ...

----- 历史功能说明 (保留供参考) -----
v22.evolve 蒸馏维度自学习演化器

蒸馏 agent 已在 F_baseline_comparison / F_comparison 字段中主动提议新维度（实测BookC
前 50 章 64% 章都含「建议新增…」「建议建立…」「建议区分…」），但没有跨章统筹机制——
这些提议自生自灭，下次蒸馏 prompt 还是 v17 那 35+ 维度，新发现没被吸收。

本脚本做的事：
1. 扫所有单章 JSON 的 F 段 + 可选 G_dimension_proposals 结构化字段（双轨）
2. 用启发式（关键词 + 短语提取）从自由文本中提取「建议」核心
3. 跨章聚类相似建议（基于关键词重叠 + 主题词）
4. 满足双门槛（出现 ≥ N 章 + 提议密度 ≥ X%）→ 候选维度
5. 输出候选报告 + 可选 --promote 升级到 auto_evolved_dimensions.json

业界依据（Round 1 调研 .research_cache/inspiration_self_evolving_distill_*.md）：
- Voyager (NeurIPS 2023): skill library 自演化范式
- OpenAI Self-Evolving Cookbook 2025: meta-prompt 改进迭代
- 我们的实现：单章 agent 已有提议行为 → 加聚合统筹层

输入：
    python dimension_evolver.py --project workspace/styles/<书名> --scan
    python dimension_evolver.py --project workspace/styles/<书名> --promote <cand_id>
    python dimension_evolver.py --all-projects --scan          # 跨项目验证 universal
    python dimension_evolver.py --all-projects --promote-universal

输出：
    workspace/styles/<书名>/.dimension_evolution/candidates_<ts>.json
    core/claude-home/auto_evolved_dimensions.json (单项目升级)
    core/claude-home/universal_distill_dimensions.json (跨项目 universal)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# 提议触发关键词（agent 在 F 段用这些词表示新提议）
PROPOSAL_TRIGGERS = [
    "建议新增", "建议建立", "建议补入", "建议加", "建议扩展", "建议区分",
    "建议把", "建议在 skill 中", "建议加入", "建议作为",
    "新增", "应单独建立", "应区分", "应扩展", "建议",
    "新发现",
]

# 噪声词（"新发现"后面常跟无价值描述）
NOISE_PREFIXES = [
    "与基线一致", "符合 v2 规范", "符合 v3 规范", "无新发现",
]

# 维度名称关键词（提议中常出现这些词指代 dim 类型）
DIM_TYPE_KEYWORDS = [
    "基准", "规则", "规范", "标准", "指标", "比例", "密度", "频率",
    "占比", "强度", "档", "档位", "子类", "子型", "节奏", "曲线",
]

# 跨章聚类时的关键词（用作聚类键）
CLUSTER_STOPWORDS = set("的了在和与或是有为了对于本章这章应该需要可以".split())

# 双门槛
DEFAULT_MIN_CHAPTERS = 2           # bottom-up 阶段宽（Round 1 调研: discovery 阶段宽 + stabilization 阶段严）
DEFAULT_HIGH_VALUE_RATIO = 0.2      # 20% valid 即够（agent 提议措辞偏柔）
DEFAULT_MIN_PROPOSAL_LEN = 10       # 提议文本至少 10 字
DEFAULT_MAX_PROMOTE_PER_RUN = 3     # 一次最多升 3 维度（防 schema 膨胀）


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def extract_proposals_from_f_text(f_text: str, chapter: int) -> list[dict]:
    """从 F 段自由文本中提取所有提议片段。

    每个提议片段含：trigger 关键词 / 完整建议句 / 含 dim 关键词标记
    """
    if not isinstance(f_text, str) or not f_text:
        return []
    # 早返回：完全没触发词的章直接跳
    if not any(t in f_text for t in PROPOSAL_TRIGGERS):
        return []

    proposals = []
    # 按句号 / 分号 / ① 分句
    sentences = re.split(r"[。；①②③④⑤⑥⑦⑧⑨⑩]|——", f_text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < DEFAULT_MIN_PROPOSAL_LEN:
            continue
        # 找触发词
        trigger = None
        for t in PROPOSAL_TRIGGERS:
            if t in sent:
                trigger = t
                break
        if not trigger:
            continue
        # 判断是否含 dim 类型关键词（结构化建议而非情绪描述）
        has_dim_kw = any(kw in sent for kw in DIM_TYPE_KEYWORDS)
        # 价值评估：
        # high = 明确建议新维度/规则 + 有 dim 关键词
        # mid = 建议补充/扩展/区分（迭代式改进）
        # low = 纯"新发现"描述但没有建议动作
        if any(t in sent for t in ("建议新增", "建议建立", "应单独建立", "新增", "建议在 skill", "建议作为", "建议加入")):
            value = "high" if has_dim_kw else "mid"
        elif any(t in sent for t in ("建议补入", "建议扩展", "建议加", "建议区分", "建议把", "应区分", "应扩展")):
            value = "mid"
        elif "建议" in sent:
            value = "mid"
        else:
            value = "low"
        proposals.append({
            "chapter": chapter,
            "trigger": trigger,
            "text": sent[:200],
            "has_dim_keyword": has_dim_kw,
            "value": value,
        })
    return proposals


def extract_proposals_from_g_field(g_proposals: list, chapter: int) -> list[dict]:
    """从 G_dimension_proposals 结构化字段读（新蒸馏书才有）。"""
    if not isinstance(g_proposals, list):
        return []
    out = []
    for p in g_proposals:
        if not isinstance(p, dict):
            continue
        out.append({
            "chapter": chapter,
            "trigger": "G_structured",
            "text": p.get("observation", "")[:200],
            "proposed_dim_name": p.get("proposed_dim_name", ""),
            "value": p.get("value_assessment", "low"),
            "has_dim_keyword": True,
            "current_dims_missing": p.get("current_dims_missing", ""),
            "extraction_method": p.get("suggested_extraction_method", ""),
        })
    return out


def collect_all_proposals(project: Path) -> list[dict]:
    """扫所有单章 JSON 收集提议（F 段 + G 段双轨）。"""
    out = []
    chapter_dir = project / "蒸馏进度"
    if not chapter_dir.exists():
        return out

    candidates = list(chapter_dir.glob("第*.json")) + [
        f for f in chapter_dir.glob("ch*.json")
        if not f.stem.endswith("_metrics") and not f.stem.endswith("_continuity")
    ]
    seen_chapters: set[int] = set()
    for f in sorted(candidates):
        d = load_json(f, {})
        if not isinstance(d, dict):
            continue
        ch = d.get("chapter")
        if not isinstance(ch, int) or ch in seen_chapters:
            continue
        seen_chapters.add(ch)
        # F 段（兼容多个字段名）
        for f_key in ("F_baseline_comparison", "F_comparison", "F_vs_ai_default"):
            if f_key in d:
                f_val = d[f_key]
                if isinstance(f_val, str):
                    out.extend(extract_proposals_from_f_text(f_val, ch))
                elif isinstance(f_val, dict):
                    # F 段是 dict 时取所有 str values
                    for v in f_val.values():
                        if isinstance(v, str):
                            out.extend(extract_proposals_from_f_text(v, ch))
        # G 段（v22.evolve 新结构化字段）
        g_field = d.get("G_dimension_proposals") or d.get("G_dim_proposals")
        if g_field:
            out.extend(extract_proposals_from_g_field(g_field, ch))
    return out


def extract_keywords(text: str, top_k: int = 5) -> list[str]:
    """简单关键词提取（用于聚类）。"""
    # 抽出 2-4 字中文短语
    tokens = re.findall(r"[一-鿿]{2,5}", text)
    # 过滤 stopwords
    filtered = [t for t in tokens if t not in CLUSTER_STOPWORDS and len(t) >= 2]
    # 去重并保留前 top_k
    seen = set()
    out = []
    for t in filtered:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= top_k:
            break
    return out


def extract_dim_theme(text: str) -> str | None:
    """从提议文本提取核心 dim 主题词（如「比喻密度」「感叹号基准」「章型子类」）。

    v22.evolve.3：先按 DIM_TYPE_KEYWORDS 命中找主题词，再取该词前 2-3 字组合。
    """
    for kw in DIM_TYPE_KEYWORDS:
        idx = text.find(kw)
        if idx >= 0:
            # 取前 2-3 字 + 该词
            start = max(0, idx - 3)
            prefix = text[start:idx]
            # 找 prefix 的最右 2-3 个中文字
            cn = re.findall(r"[一-鿿]", prefix)
            if cn:
                prefix_str = "".join(cn[-3:])
                return f"{prefix_str}{kw}"
            return kw
    return None


def cluster_proposals(proposals: list[dict], min_keyword_overlap: int = 2,
                       max_cluster_size: int = 30) -> list[list[dict]]:
    """v22.evolve.3 聚类策略：

    1) 先按 dim 主题词聚（如「比喻密度」「感叹号基准」 → 同主题词进同 cluster）
    2) 没有 dim 主题词的提议按关键词重叠（fallback）
    3) max_cluster_size 防巨型异质 cluster
    """
    # 给每个 proposal 加 keywords + dim_theme
    for p in proposals:
        p["_keywords"] = set(extract_keywords(p["text"]))
        p["_dim_theme"] = extract_dim_theme(p["text"])

    # 第 1 阶段：按 dim_theme 聚（主键）
    theme_groups: dict[str, list[dict]] = {}
    unthemed: list[dict] = []
    for p in proposals:
        if p["_dim_theme"]:
            theme_groups.setdefault(p["_dim_theme"], []).append(p)
        else:
            unthemed.append(p)

    # 第 1.5 阶段：相似主题词二次合并（如「省略号密度/感叹号基准」→「标点密度子类」族）
    SIMILAR_SUFFIX_GROUPS = {
        "标点密度": ["省略号", "感叹号", "问号", "破折号", "标点"],
        "章型子类": ["章子类", "章型", "子类基准", "子基线"],
        "对话指标": ["对话比例", "对话密度", "真实对话", "对话率"],
        "比喻指标": ["比喻密度", "比喻数", "比喻"],
        "字数指标": ["字数", "字句", "句长"],
        "情绪指标": ["情绪节拍", "情绪强度", "情绪曲线"],
    }
    merged_themes: dict[str, list[dict]] = {}
    consumed_themes: set[str] = set()
    for super_name, suffixes in SIMILAR_SUFFIX_GROUPS.items():
        merged_group = []
        for theme, group in theme_groups.items():
            if theme in consumed_themes:
                continue
            if any(suf in theme for suf in suffixes):
                merged_group.extend(group)
                consumed_themes.add(theme)
        if merged_group:
            merged_themes[super_name] = merged_group
    # 剩余 theme groups
    for theme, group in theme_groups.items():
        if theme not in consumed_themes:
            merged_themes[theme] = group

    clusters = list(merged_themes.values())

    # 第 2 阶段：unthemed 按关键词重叠合并到现有 cluster 或新建
    for p in unthemed:
        added = False
        for cluster in clusters:
            if len(cluster) >= max_cluster_size:
                continue
            rep_kws = set()
            for c_p in cluster[:5]:
                rep_kws |= c_p["_keywords"]
            if len(p["_keywords"] & rep_kws) >= min_keyword_overlap:
                cluster.append(p)
                added = True
                break
        if not added:
            clusters.append([p])
    return clusters


def evaluate_cluster_as_candidate(cluster: list[dict], min_chapters: int, high_value_ratio: float) -> dict | None:
    """判断聚类是否够格成为候选维度。

    v22.evolve.2：valid = high + mid（mid 是「建议补充」级别也算有效提议）。
    Round 1 调研 (LLM-FS-Agent 多 agent 辩论思想)：候选生成阶段宽，promote 阶段严。
    """
    chapters = sorted(set(p["chapter"] for p in cluster))
    n_chapters = len(chapters)
    if n_chapters < min_chapters:
        return None

    high_count = sum(1 for p in cluster if p.get("value") == "high")
    mid_count = sum(1 for p in cluster if p.get("value") == "mid")
    valid_count = high_count + mid_count
    valid_ratio = valid_count / len(cluster)
    high_ratio = high_count / len(cluster)
    if valid_ratio < high_value_ratio:    # 实际是 valid_ratio 阈值，名字保留兼容
        return None

    # v22.evolve.3 命名优先级：
    # 1) G 段 proposed_dim_name（结构化字段，最准）
    # 2) cluster 共同 dim_theme（如「比喻密度」）
    # 3) 关键词组合（兜底）
    proposed_names = [p.get("proposed_dim_name") for p in cluster if p.get("proposed_dim_name")]
    themes = [p.get("_dim_theme") for p in cluster if p.get("_dim_theme")]
    if proposed_names:
        dim_name = Counter(proposed_names).most_common(1)[0][0]
    elif themes:
        # 优先选最频繁主题；如果多个主题（二次合并的 super name）则取 unique
        most_theme = Counter(themes).most_common(1)[0][0]
        unique_themes = sorted(set(themes))
        if len(unique_themes) > 1:
            dim_name = f"auto_{most_theme}_族（{','.join(unique_themes[:3])}）"
        else:
            dim_name = f"auto_{most_theme}"
    else:
        all_kws = Counter(kw for p in cluster for kw in p["_keywords"])
        top_kws = [k for k, _ in all_kws.most_common(3)]
        dim_name = "auto_" + "_".join(top_kws[:3])

    # 提议代表样本（取 high value 的前 3 条）
    sample_proposals = sorted(cluster, key=lambda x: (x["value"] != "high", x["chapter"]))[:3]

    return {
        "candidate_dim_name": dim_name,
        "appearance_count": len(cluster),
        "appearance_chapters": chapters,
        "chapter_span": f"ch{chapters[0]}-{chapters[-1]}",
        "high_value_ratio": round(high_ratio, 3),
        "valid_value_ratio": round(valid_ratio, 3),       # v22.evolve.2 新加
        "value_distribution": {"high": high_count, "mid": mid_count, "low": len(cluster) - valid_count},
        "has_dim_keyword_ratio": round(sum(1 for p in cluster if p["has_dim_keyword"]) / len(cluster), 3),
        "top_keywords": [kw for kw, _ in Counter(kw for p in cluster for kw in p["_keywords"]).most_common(5)],
        "sample_proposals": [
            {"chapter": p["chapter"], "value": p["value"], "text": p["text"]}
            for p in sample_proposals
        ],
        "source_trigger_distribution": dict(Counter(p["trigger"] for p in cluster)),
    }


def scan_project(project: Path, min_chapters: int, high_value_ratio: float) -> dict:
    """单项目扫描 → 候选维度报告。"""
    proposals = collect_all_proposals(project)
    if not proposals:
        return {"error": "no proposals found in F/G fields"}

    clusters = cluster_proposals(proposals)
    candidates = []
    for c in clusters:
        cand = evaluate_cluster_as_candidate(c, min_chapters, high_value_ratio)
        if cand:
            candidates.append(cand)

    # 按 appearance_count + high_value_ratio 排序
    candidates.sort(key=lambda x: (-x["appearance_count"], -x["high_value_ratio"]))

    # 给每个 candidate 加 id
    for i, c in enumerate(candidates):
        c["candidate_id"] = f"cand_{i+1:03d}"

    return {
        "schema_version": "v22.evolve.1",
        "project": project.name,
        "total_proposals_extracted": len(proposals),
        "total_clusters_formed": len(clusters),
        "candidates_above_threshold": len(candidates),
        "thresholds": {
            "min_chapters": min_chapters,
            "high_value_ratio": high_value_ratio,
        },
        "candidates": candidates,
        "_metadata": {
            "scan_date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evolver_version": "v22.evolve.1",
        },
    }


def scan_all_projects(styles_root: Path, min_chapters: int, high_value_ratio: float) -> dict:
    """跨项目扫描 → universal 候选（在 ≥ 2 个项目都验证的维度）。"""
    per_project_candidates: dict[str, list[dict]] = {}
    for project in styles_root.iterdir():
        if not project.is_dir() or project.name.startswith("."):
            continue
        result = scan_project(project, min_chapters, high_value_ratio)
        if "error" in result:
            continue
        per_project_candidates[project.name] = result.get("candidates", [])

    # 跨项目聚类（按 top_keywords 重叠）
    universal: list[dict] = []
    all_with_origin = []
    for proj, cands in per_project_candidates.items():
        for c in cands:
            all_with_origin.append((proj, c))

    # 聚类（每个候选用 top_keywords 集合做 fingerprint）
    universal_groups: list[list[tuple[str, dict]]] = []
    for proj, c in all_with_origin:
        kws = set(c["top_keywords"])
        added = False
        for g in universal_groups:
            rep_kws = set(g[0][1]["top_keywords"])
            if len(kws & rep_kws) >= 2:
                g.append((proj, c))
                added = True
                break
        if not added:
            universal_groups.append([(proj, c)])

    # 跨 ≥ 2 个项目验证的组才升 universal
    for g in universal_groups:
        projs = set(p for p, _ in g)
        if len(projs) >= 2:
            total_appearance = sum(c["appearance_count"] for _, c in g)
            universal.append({
                "universal_dim_name": g[0][1]["candidate_dim_name"],
                "verified_in_projects": sorted(projs),
                "total_appearance_across_projects": total_appearance,
                "top_keywords_intersection": list(
                    set.intersection(*(set(c["top_keywords"]) for _, c in g))
                ),
                "sample_per_project": [
                    {"project": p, "sample": c["sample_proposals"][0] if c["sample_proposals"] else None}
                    for p, c in g[:3]
                ],
            })

    universal.sort(key=lambda x: -x["total_appearance_across_projects"])

    return {
        "schema_version": "v22.evolve.universal.1",
        "scanned_projects": list(per_project_candidates.keys()),
        "per_project_candidate_counts": {p: len(c) for p, c in per_project_candidates.items()},
        "universal_candidates": universal,
        "_metadata": {
            "scan_date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evolver_version": "v22.evolve.1",
        },
    }


def promote_candidate(candidate: dict, project: Path | None, repo_root: Path,
                       max_per_run: int = DEFAULT_MAX_PROMOTE_PER_RUN) -> dict:
    """升级候选维度到 auto_evolved_dimensions.json 注册表。"""
    registry_file = repo_root / "core" / "claude-home" / "auto_evolved_dimensions.json"
    if registry_file.exists():
        registry = load_json(registry_file, {"dimensions": []})
    else:
        registry = {
            "schema_version": "v22.evolve.1",
            "_doc": "v22.evolve 自学习升级的维度池。蒸馏 agent 在 F/G 段提议 → dimension_evolver 聚合 → 通过双门槛升入本池 → 下次蒸馏自动注入 prompt B7 段",
            "dimensions": [],
        }

    # 重名检测
    existing_names = {d["dim_name"] for d in registry.get("dimensions", [])}
    if candidate["candidate_dim_name"] in existing_names:
        return {"promoted": False, "reason": "已在注册表中"}

    # 一次升级上限
    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_promoted = sum(1 for d in registry["dimensions"]
                         if d.get("promoted_at", "").startswith(today))
    if today_promoted >= max_per_run:
        return {"promoted": False, "reason": f"今日已升 {today_promoted} 维度（上限 {max_per_run}）"}

    entry = {
        "dim_name": candidate["candidate_dim_name"],
        "promoted_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "promoted_from_project": project.name if project else "universal",
        "appearance_count": candidate.get("appearance_count")
                            or candidate.get("total_appearance_across_projects"),
        "appearance_chapters": candidate.get("appearance_chapters", []),
        "top_keywords": candidate.get("top_keywords") or candidate.get("top_keywords_intersection", []),
        "sample_proposals": candidate.get("sample_proposals", []),
        "status": "active",
        "version": 1,
        "auto_inject_to_distill_prompt": True,
    }
    registry["dimensions"].append(entry)
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"promoted": True, "dim_name": entry["dim_name"], "registry": str(registry_file)}


def main():
    import sys as _sys
    print(
        "⚠️  [DEPRECATED] dimension_evolver.py 已被 SkillOpt 范式 (core/scripts/skill_opt/train.py) 替代。\n"
        "   业界源 arXiv:2605.23904 + microsoft/SkillOpt\n"
        "   迁移: python core/scripts/skill_opt/train.py --project <path> --skill <path>\n"
        "   旧入口仍可用 (本警告无 exit),将在后续 release 移除。\n",
        file=_sys.stderr,
    )
    parser = argparse.ArgumentParser(description="dimension_evolver v22.evolve · 蒸馏维度自学习 (DEPRECATED)")
    parser.add_argument("--project", help="单项目路径（如 workspace/styles/<书名>）")
    parser.add_argument("--all-projects", action="store_true", help="扫所有 workspace/styles/* 找 universal")
    parser.add_argument("--scan", action="store_true", help="扫描出候选维度")
    parser.add_argument("--promote", help="升级指定 candidate_id 到注册表（需配合 --project）")
    parser.add_argument("--promote-universal", action="store_true",
                        help="批量升级跨项目验证的 universal 候选")
    parser.add_argument("--min-chapters", type=int, default=DEFAULT_MIN_CHAPTERS)
    parser.add_argument("--high-value-ratio", type=float, default=DEFAULT_HIGH_VALUE_RATIO)
    parser.add_argument("--repo-root", default=".", help="项目根目录（默认当前）")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    # 模式 1: 单项目扫描
    if args.project and args.scan:
        project = Path(args.project).resolve()
        result = scan_project(project, args.min_chapters, args.high_value_ratio)
        out_dir = project / ".dimension_evolution"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"candidates_{ts}.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] {out_file}")
        if "error" not in result:
            print(f"     提议总数: {result['total_proposals_extracted']}")
            print(f"     聚类数:   {result['total_clusters_formed']}")
            print(f"     候选维度: {result['candidates_above_threshold']}")
            for c in result["candidates"][:5]:
                print(f"     - {c['candidate_id']} {c['candidate_dim_name']}: "
                      f"{c['appearance_count']} 次/{c['chapter_span']}/high={c['high_value_ratio']}")
        return

    # 模式 2: 跨项目 universal 扫描
    if args.all_projects and args.scan:
        styles_root = repo_root / "workspace" / "styles"
        if not styles_root.exists():
            print(f"[error] {styles_root} not found", file=sys.stderr)
            sys.exit(2)
        result = scan_all_projects(styles_root, args.min_chapters, args.high_value_ratio)
        out_dir = repo_root / "core" / "claude-home"
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"universal_candidates_{ts}.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] {out_file}")
        print(f"     扫描项目: {result['scanned_projects']}")
        print(f"     per-project candidates: {result['per_project_candidate_counts']}")
        print(f"     universal candidates (≥2 项目验证): {len(result['universal_candidates'])}")
        for c in result["universal_candidates"][:5]:
            print(f"     - {c['universal_dim_name']}: 验证于 {c['verified_in_projects']} ({c['total_appearance_across_projects']} 总出现)")
        return

    # 模式 3: 升级单候选
    if args.project and args.promote:
        project = Path(args.project).resolve()
        result = scan_project(project, args.min_chapters, args.high_value_ratio)
        target = next((c for c in result.get("candidates", []) if c["candidate_id"] == args.promote), None)
        if not target:
            print(f"[error] candidate {args.promote} not found in {project}", file=sys.stderr)
            sys.exit(2)
        promo = promote_candidate(target, project, repo_root)
        print(json.dumps(promo, ensure_ascii=False, indent=2))
        return

    # 模式 4: 批量升 universal
    if args.all_projects and args.promote_universal:
        styles_root = repo_root / "workspace" / "styles"
        result = scan_all_projects(styles_root, args.min_chapters, args.high_value_ratio)
        promoted = []
        for cand in result["universal_candidates"][:DEFAULT_MAX_PROMOTE_PER_RUN]:
            # 把 universal cand 包成符合 promote_candidate schema
            promo_input = {
                "candidate_dim_name": cand["universal_dim_name"],
                "appearance_count": cand["total_appearance_across_projects"],
                "appearance_chapters": [],
                "top_keywords": cand["top_keywords_intersection"],
                "sample_proposals": [s["sample"] for s in cand["sample_per_project"] if s["sample"]],
            }
            promo = promote_candidate(promo_input, None, repo_root)
            promoted.append({"dim_name": cand["universal_dim_name"], "result": promo})
        print(json.dumps({"promoted_universal": promoted}, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
