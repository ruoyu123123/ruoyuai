"""cluster_segmenter.py — v22.cluster 已蒸馏书 retroactive cluster 切分

把已蒸馏完成的风格库（无 cluster 信息，因为作者写时没按 cluster）
按情节单元自动切成 cluster（与写作端 ECAS 颗粒度对齐）。

切割决策：
1. 基于 衔接分析/*.json 的 connection_type 字段（强切类型 vs 弱切类型）
2. 叠加硬约束：单 cluster 2-6 章 + 4000-20000 字（对齐 ECAS schema 范围）

业界依据：
- Zehe 2021 (EACL) — 场景边界检测 F1 仅 24%，业界共识不要追求完美切分
- 我们用「作者已标注的衔接类型」作启发式 → 比 BERT 切分更可靠
- ECAS v23 schema (event_cluster_schema.json) — cluster 字数/章数硬约束

输入：
    python cluster_segmenter.py --project workspace/styles/<书名>

输出：
    workspace/styles/<书名>/cluster_index.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# 强切类型 — 这种衔接 = cluster 边界（情节单元切换）
STRONG_BOUNDARY_PATTERNS = [
    "时间跳跃",
    "空间跳转",
    "POV切换",
    "POV跳切",
    "情绪落差",
    "悬念承接新视角",
    "悬念新视角",
    "视角切换",
    "新POV",
]

# 弱切类型 — 这种衔接 = 同 cluster 内场景切换
WEAK_BOUNDARY_PATTERNS = [
    "直接承接",
    "拟声定格",
    "钩子回音",
    "信息炸弹回响",
    "对话承接",
    "对话接续",
    "对话引导",
    "POV延续",
    "拟声切场",
    "POV内心",
    "插页",
    "思辨延续",
]

# ECAS schema 约束（参考 event_cluster_schema.json）
MIN_CHAPTERS_PER_CLUSTER = 2
MAX_CHAPTERS_PER_CLUSTER = 6
MIN_WORDS_PER_CLUSTER = 4000
MAX_WORDS_PER_CLUSTER = 20000
DEFAULT_CHAPTER_WORDS = 3000  # 单章 metrics 缺失时的兜底字数


def classify_boundary(connection_type: str) -> str:
    """返回 'strong' / 'weak' / 'unknown'。"""
    if not connection_type:
        return "unknown"
    for p in STRONG_BOUNDARY_PATTERNS:
        if p in connection_type:
            # 排除「直接承接（情绪落差子类）」这种以承接为主的
            if "直接承接" in connection_type:
                continue
            return "strong"
    for p in WEAK_BOUNDARY_PATTERNS:
        if p in connection_type:
            return "weak"
    return "unknown"


def load_chapter_wordcounts(project: Path, total_chapters: int) -> dict[int, int]:
    """从 蒸馏进度/ 读每章字数。"""
    out: dict[int, int] = {}
    metrics_dir = project / "蒸馏进度"
    if not metrics_dir.exists():
        return out
    for ch in range(1, total_chapters + 1):
        # 兼容 chN_metrics.json / 第N章.json 多种 schema
        for pattern in (f"ch{ch}_metrics.json", f"第{ch}章.json", f"ch{ch}.json"):
            f = metrics_dir / pattern
            if not f.exists():
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            wc = None
            for key in ("word_count", "total_chars", "chapter_words", "字数", "cjk_chars"):
                v = d.get(key)
                if isinstance(v, (int, float)):
                    wc = int(v)
                    break
                if isinstance(v, dict):
                    for k2 in ("mean", "total", "value"):
                        if isinstance(v.get(k2), (int, float)):
                            wc = int(v[k2])
                            break
                    if wc:
                        break
            if wc is None:
                # 兼容 nested quantitative_analysis.chapter_words.total
                qa = d.get("quantitative_analysis", {})
                if isinstance(qa, dict):
                    cw = qa.get("chapter_words")
                    if isinstance(cw, dict):
                        wc = cw.get("total") or cw.get("mean")
                    elif isinstance(cw, (int, float)):
                        wc = int(cw)
            if wc and wc > 0:
                out[ch] = wc
                break
    return out


def collect_transitions(project: Path) -> list[dict]:
    """从 衔接分析/*.json 收集所有 transition（from_ch, to_ch, connection_type, classify）。

    transition.from_ch + 1 == transition.to_ch 时才作为决策依据（相邻章）。
    跨章 transition（如 from=10 to=15）忽略。
    """
    out: list[dict] = []
    continuity_dir = project / "衔接分析"
    if not continuity_dir.exists():
        return out
    for f in sorted(continuity_dir.glob("*continuity.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for t in d.get("transitions", []) or []:
            # 兼容 t 是 dict 或 str（某些蒸馏 schema 简化）
            if not isinstance(t, dict):
                continue
            from_ch = t.get("from")
            to_ch = t.get("to")
            if not isinstance(from_ch, int) or not isinstance(to_ch, int):
                continue
            if to_ch != from_ch + 1:
                continue
            ct = t.get("connection_type", "")
            out.append({
                "from_ch": from_ch,
                "to_ch": to_ch,
                "connection_type": ct,
                "boundary_class": classify_boundary(ct),
                "method_detail": (t.get("method_detail", "") or "")[:120],
            })
    out.sort(key=lambda x: x["from_ch"])
    # 去重（同一对 ch 可能在多个 continuity 出现）
    seen = set()
    uniq = []
    for t in out:
        key = (t["from_ch"], t["to_ch"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq


def _infer_genre_from_naming(project: Path, work: str) -> str:
    """v22.4dim Round 3：从 naming_convention.json + 书名特征推断题材。

    业界依据（Round 3 调研）：阅文妙笔大模型按题材切 prompt 模板；
    不同题材在「章节命名 / 角色起名 / 节奏 / 情感弧」4 维上差异显著。

    返回：xuanhuan / xianxia / urban_supernatural / scifi_meta / horror_game /
    historical / romance / unknown
    """
    # 优先用蒸馏的 naming_convention 推
    nc_file = project / "naming_convention.json"
    if nc_file.exists():
        try:
            nc = json.loads(nc_file.read_text(encoding="utf-8"))
            primary = nc.get("primary_culture", "")
            wn_idx = (nc.get("web_novel_indicators_round1d_applied", {}) or {}).get("web_novel_index", 0)
            if primary == "western_translit" and wn_idx < 0.1:
                return "western_isekai"   # 西式异世界（如BookC）
            if primary == "fantasy" or wn_idx > 0.5:
                return "xuanhuan"          # 玄幻
            if primary == "scifi":
                return "scifi_meta"        # 科幻/元宇宙
        except (json.JSONDecodeError, OSError):
            pass

    # fallback：从书名关键字推
    name_keywords = {
        "xianxia": ["仙", "修真", "渡劫", "金丹", "元婴"],
        "xuanhuan": ["神", "魔", "鸿蒙", "天道", "斗罗", "封神"],
        "urban_supernatural": ["都市", "学院", "校园", "精神病", "斩神"],
        "horror_game": ["惊悚", "副本", "游戏", "无限", "诡秘"],
        "scifi_meta": ["地球", "饲养", "全人类", "黑暗森林"],
        "historical": ["大唐", "穿越古代", "宋朝", "明朝"],
        "romance": ["总裁", "甜宠", "豪门"],
    }
    for genre, kws in name_keywords.items():
        for kw in kws:
            if kw in work:
                return genre
    return "unknown"


def detect_total_chapters(project: Path) -> int:
    """从 蒸馏进度/ 推断总章数。"""
    metrics_dir = project / "蒸馏进度"
    if not metrics_dir.exists():
        return 0
    max_ch = 0
    for f in metrics_dir.glob("*.json"):
        for pat in (r"ch(\d+)_metrics", r"第(\d+)章", r"ch(\d+)"):
            m = re.search(pat, f.name)
            if m:
                max_ch = max(max_ch, int(m.group(1)))
                break
    return max_ch


def segment_clusters(
    total_chapters: int,
    transitions: list[dict],
    wordcounts: dict[int, int],
) -> list[dict]:
    """按规则切 cluster。返回 cluster 列表。

    决策树（章 N 是否本 cluster 末章 / N+1 是否新 cluster 首章）：
      - 当前 cluster < MIN_CHAPTERS_PER_CLUSTER 章 → **继续累积**（哪怕遇强切也忽略，避免单章 cluster）
      - 当前 cluster ≥ MAX_CHAPTERS_PER_CLUSTER 章 → **强制切**
      - 当前 cluster 字数 ≥ MAX_WORDS_PER_CLUSTER → **强制切**
      - 当前 cluster 字数 < MIN_WORDS_PER_CLUSTER → **继续累积**（避免字数过短）
      - 遇 strong boundary 且 ≥ MIN_CHAPTERS 且 ≥ MIN_WORDS → **切**
      - 遇 weak boundary → **不切**（同 cluster）
      - 遇 unknown → 视字数决定（接近 max 就切）
    """
    transition_map = {t["from_ch"]: t for t in transitions}
    clusters: list[dict] = []
    current_start = 1
    current_words = wordcounts.get(1, DEFAULT_CHAPTER_WORDS)
    current_boundary_reason = ""

    for ch in range(1, total_chapters + 1):
        # 决策：本章 ch 是否本 cluster 末章？
        current_len = ch - current_start + 1
        next_ch = ch + 1

        if next_ch > total_chapters:
            # 最后一章，强制结束 cluster
            clusters.append({
                "chapter_range": [current_start, ch],
                "chapters_count": current_len,
                "estimated_words": current_words,
                "boundary_reason": "end_of_book",
            })
            break

        t = transition_map.get(ch)
        bc = t["boundary_class"] if t else "unknown"
        ct = t["connection_type"] if t else ""

        # 硬约束 1：max 章数
        if current_len >= MAX_CHAPTERS_PER_CLUSTER:
            clusters.append({
                "chapter_range": [current_start, ch],
                "chapters_count": current_len,
                "estimated_words": current_words,
                "boundary_reason": f"max_chapters({current_len}≥{MAX_CHAPTERS_PER_CLUSTER})",
            })
            current_start = next_ch
            current_words = wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 硬约束 2：max 字数
        if current_words >= MAX_WORDS_PER_CLUSTER:
            clusters.append({
                "chapter_range": [current_start, ch],
                "chapters_count": current_len,
                "estimated_words": current_words,
                "boundary_reason": f"max_words({current_words}≥{MAX_WORDS_PER_CLUSTER})",
            })
            current_start = next_ch
            current_words = wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 硬约束 3：min 章数 — 太短就继续累积，不管 boundary type
        if current_len < MIN_CHAPTERS_PER_CLUSTER:
            current_words += wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 硬约束 4：min 字数 — 字数还没到也继续累积
        if current_words < MIN_WORDS_PER_CLUSTER:
            current_words += wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 启发式：strong boundary → 切
        if bc == "strong":
            clusters.append({
                "chapter_range": [current_start, ch],
                "chapters_count": current_len,
                "estimated_words": current_words,
                "boundary_reason": f"strong:{ct}",
            })
            current_start = next_ch
            current_words = wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 启发式：weak boundary → 不切
        # unknown boundary：接近 max 字数才切
        if bc == "unknown" and current_words >= MAX_WORDS_PER_CLUSTER * 0.85:
            clusters.append({
                "chapter_range": [current_start, ch],
                "chapters_count": current_len,
                "estimated_words": current_words,
                "boundary_reason": f"unknown_near_max:{ct or 'no_data'}",
            })
            current_start = next_ch
            current_words = wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)
            continue

        # 否则不切，累积下一章
        current_words += wordcounts.get(next_ch, DEFAULT_CHAPTER_WORDS)

    # v22.cluster 修正：尾 cluster 字数 < MIN 时并入前 cluster
    if len(clusters) >= 2 and clusters[-1]["estimated_words"] < MIN_WORDS_PER_CLUSTER:
        last = clusters.pop()
        clusters[-1]["chapter_range"][1] = last["chapter_range"][1]
        clusters[-1]["chapters_count"] += last["chapters_count"]
        clusters[-1]["estimated_words"] += last["estimated_words"]
        clusters[-1]["boundary_reason"] = "end_of_book(merged_short_tail)"

    # 加 cluster_id
    for i, c in enumerate(clusters, 1):
        c["cluster_id"] = f"auto_{i:03d}"
        c["scenes_estimated"] = max(2, round(c["chapters_count"] * 1.3))
    return clusters


def main():
    parser = argparse.ArgumentParser(description="cluster_segmenter v22.cluster · 已蒸馏书 retroactive 切分")
    parser.add_argument("--project", required=True, help="风格库项目路径")
    parser.add_argument("--total-chapters", type=int, help="总章数（默认自动检测）")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[error] project not found: {project}", file=sys.stderr)
        sys.exit(2)

    total = args.total_chapters or detect_total_chapters(project)
    if total < 2:
        print(f"[error] 检测到总章数 = {total}，太少", file=sys.stderr)
        sys.exit(2)

    work = project.name
    transitions = collect_transitions(project)
    wordcounts = load_chapter_wordcounts(project, total)
    print(f"[info] {work}: 总 {total} 章，{len(transitions)} 个相邻衔接点，{len(wordcounts)} 章有字数数据")

    clusters = segment_clusters(total, transitions, wordcounts)

    # 统计
    boundary_reasons: dict[str, int] = {}
    for c in clusters:
        key = c["boundary_reason"].split(":")[0].split("(")[0]
        boundary_reasons[key] = boundary_reasons.get(key, 0) + 1
    avg_chapters = sum(c["chapters_count"] for c in clusters) / len(clusters) if clusters else 0
    avg_words = sum(c["estimated_words"] for c in clusters) / len(clusters) if clusters else 0

    # v22.4dim Round 3 应用：题材 override 字段（阅文妙笔产业级验证 · 不同题材应有不同 cluster 节奏）
    genre = _infer_genre_from_naming(project, work)

    out = {
        "schema_version": "v22.cluster.2",
        "work": work,
        "genre": genre,
        "total_chapters": total,
        "total_clusters": len(clusters),
        "average_chapters_per_cluster": round(avg_chapters, 2),
        "average_words_per_cluster": round(avg_words),
        "boundary_reason_distribution": boundary_reasons,
        "clusters": clusters,
        "_segmenter_config": {
            "min_chapters": MIN_CHAPTERS_PER_CLUSTER,
            "max_chapters": MAX_CHAPTERS_PER_CLUSTER,
            "min_words": MIN_WORDS_PER_CLUSTER,
            "max_words": MAX_WORDS_PER_CLUSTER,
            "strong_boundary_patterns": STRONG_BOUNDARY_PATTERNS,
            "weak_boundary_patterns": WEAK_BOUNDARY_PATTERNS,
        },
        "_metadata": {
            "segmented_at": datetime.utcnow().strftime("%Y-%m-%d"),
            "segmenter_version": "v22.cluster.1",
            "notes": "已蒸馏书 retroactive 切分（作者写时无 cluster 概念）。新书写时直接用 ECAS schema 的 cluster_id。",
        },
    }

    out_path = project / "cluster_index.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {out_path}")
    print(f"     总 cluster: {len(clusters)} | 均长: {avg_chapters:.2f} 章 / {avg_words:.0f} 字")
    print(f"     切割原因分布: {boundary_reasons}")


if __name__ == "__main__":
    main()
