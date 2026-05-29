"""counterfactual_judge_diff.py — Self-Protection 曝光器（v23 Layer 2+3）

业界依据：
- Counterfactual Debating (arxiv 2406.11514)：preset stance 实测降低 self-bias 13-22%
- SPC Self-Play Critic (arxiv 2504.19162)：异质 critic 加入 ensemble 提升 4.8 pp
- Self-Correction Bench (arxiv 2507.02778)：64.5% 盲点率，需异质 sibling 破局

【它做的事】
读两份评分报告做 diff：
  ch_NNN_consensus.json      （内部 judge ensemble，知道是自己写的）
  ch_NNN_counterfactual.json （counterfactual judge，被骗以为是匿名投稿）

【3 类 self-protection 信号】

1. SCORE_PROTECTION_GAP  : 两份 score 差距 ≥ THRESHOLD（默认 1.5）
2. GRADE_PROTECTION_GAP  : grade 差 ≥ 2 档（如内部 A 但外审 C）
3. FATAL_BLINDSPOT       : counterfactual 报 fatal finding 但内部全没抓

任一命中 → self-protection 曝光，喂给 learning_hub 调用 judge_consensus merge
触发 persona_dissent_severity 报警（Layer 3 异质共识）

【关键设计】
- 不调 LLM —— 纯结构化 diff，避免被同源 prompt 污染
- 不评谁对：counterfactual 不一定永远对，但分歧本身就是信号
- diff 持续累积 → 跨章趋势分析（counterfactual 长期低 1+ 分 → 内部 ensemble 系统性宽松）

【输出】
<project>/_数据库/.learning/counterfactual_judge_diff_<ts>.json

【CLI】
  python counterfactual_judge_diff.py <project>              # 扫所有有 counterfactual 报告的章
  python counterfactual_judge_diff.py <project> --ch N       # 指定章
  python counterfactual_judge_diff.py <project> --cluster K  # 指定 cluster（2026-05-29）
  python counterfactual_judge_diff.py <project> --trend      # 跨 cluster 趋势分析

退出码：0 健康 / 1 advisory（有分歧但不严重）/ 2 严重 self-protection 曝光

2026-05-29 cluster 化：
  cluster 才是 v2 检测层。counterfactual 报告物理上仍逐章产出（ch_NNN_counterfactual.json），
  但 diff/trend 的**分析单位**升到 cluster：把每章 diff 按所属 cluster 聚合成
  cluster diff（cluster 内 self−cf 均值差 + fatal 汇总），trend 跨 cluster 看系统性宽松。
  逐章 `--ch` + 逐章 diff 函数完整保留（cluster 聚合复用之）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_summary_reader as csr  # 2026-05-29 cluster 化
    import cluster_lookup  # 2026-05-29 cluster 化：章→cluster_id
except Exception:  # 防御：缺模块退回逐章趋势
    csr = None
    cluster_lookup = None


# ============================================================
# 阈值（业界 +/- 网文场景调校）
# ============================================================
SCORE_GAP_ADVISORY = 1.0      # score 差 ≥ 1.0 = 提醒
SCORE_GAP_WARNING = 1.5       # score 差 ≥ 1.5 = self-protection 嫌疑
SCORE_GAP_FATAL = 2.5         # score 差 ≥ 2.5 = 严重曝光
GRADE_GAP_FATAL = 2           # grade 差 2 档（A→C, B→D 等）
GRADE_NUM = {"A": 4, "B": 3, "C": 2, "D": 1}

TREND_MIN_CHAPTERS = 5        # 趋势分析最少 N 章
TREND_PERSISTENT_GAP = 1.0    # 长期均值差 ≥ 1.0 = 系统性宽松


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_score(report: dict) -> float | None:
    """从 judge 报告里提取主分数，兼容多种 schema。"""
    for key in ("score", "overall_score", "consensus_score"):
        v = report.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    agg = report.get("aggregated") or {}
    for key in ("score", "overall_score"):
        v = agg.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    scores = report.get("scores") or {}
    if isinstance(scores, dict):
        v = scores.get("overall")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def extract_grade(report: dict) -> str | None:
    """提取 grade（A/B/C/D）。"""
    for key in ("grade", "consensus_grade", "overall_grade"):
        v = report.get(key)
        if v in GRADE_NUM:
            return v
    return None


def extract_fatal_codes(report: dict) -> set:
    """提取 severity=fatal 的 finding code 集合。"""
    codes = set()
    for finding in (report.get("findings") or report.get("issues") or []):
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") in ("fatal", "critical"):
            code = finding.get("code") or finding.get("dimension")
            if code:
                codes.add(code)
    return codes


# ============================================================
# 单章 diff
# ============================================================

def diff_chapter(project_root: Path, ch: int) -> dict | None:
    """对一章做 self vs counterfactual 的 diff。"""
    judge_dir = project_root / "_数据库" / ".judge_reports"
    cf_path = judge_dir / f"ch_{ch:03d}_counterfactual.json"
    if not cf_path.exists():
        return None
    cf = load_json(cf_path, {})

    # 找 self judge 报告（多个 schema 兼容）
    self_path = None
    for name in (f"ch_{ch:03d}_consensus.json",
                 f"ch_{ch:03d}_audit-hub.json",
                 f"ch_{ch:03d}_judge.json"):
        p = judge_dir / name
        if p.exists():
            self_path = p
            break
    if self_path is None:
        return {
            "ch": ch,
            "status": "no_self_judge",
            "reason": "counterfactual 报告存在但无内部 judge 可对比，先跑 judge_consensus",
        }
    self_judge = load_json(self_path, {})

    if cf.get("context_contamination"):
        return {
            "ch": ch,
            "status": "cf_contaminated",
            "reason": "counterfactual judge 报告了 context 污染，diff 不可信",
        }

    self_score = extract_score(self_judge)
    cf_score = extract_score(cf)
    self_grade = extract_grade(self_judge)
    cf_grade = extract_grade(cf)

    findings = []
    severity = "ok"

    # 信号 1: SCORE_PROTECTION_GAP
    if self_score is not None and cf_score is not None:
        gap = self_score - cf_score
        abs_gap = abs(gap)
        if abs_gap >= SCORE_GAP_FATAL:
            findings.append({
                "signal": "SCORE_PROTECTION_GAP",
                "severity": "fatal",
                "self_score": self_score,
                "cf_score": cf_score,
                "gap": round(gap, 2),
                "direction": "self_lenient" if gap > 0 else "self_harsh",
                "explanation": (
                    f"内部评 {self_score} vs 外审评 {cf_score} (差 {gap:+.1f}) — "
                    f"{'self-protection 严重曝光' if gap > 0 else '内部反而比外审严'} = "
                    f"{'内部 ensemble 一致地宽松' if gap > 0 else '可能内部 ensemble 太苛刻或 counterfactual 太松'}"
                ),
            })
            severity = "fatal"
        elif abs_gap >= SCORE_GAP_WARNING:
            findings.append({
                "signal": "SCORE_PROTECTION_GAP",
                "severity": "warning",
                "self_score": self_score,
                "cf_score": cf_score,
                "gap": round(gap, 2),
                "direction": "self_lenient" if gap > 0 else "self_harsh",
                "explanation": f"内部评 {self_score} vs 外审评 {cf_score} (差 {gap:+.1f}) — self-protection 嫌疑",
            })
            if severity == "ok":
                severity = "warning"
        elif abs_gap >= SCORE_GAP_ADVISORY:
            findings.append({
                "signal": "SCORE_PROTECTION_GAP",
                "severity": "advisory",
                "self_score": self_score,
                "cf_score": cf_score,
                "gap": round(gap, 2),
                "explanation": f"内部评 {self_score} vs 外审评 {cf_score} (差 {gap:+.1f}) — 轻度分歧",
            })
            if severity == "ok":
                severity = "advisory"

    # 信号 2: GRADE_PROTECTION_GAP
    if self_grade and cf_grade:
        grade_diff = abs(GRADE_NUM[self_grade] - GRADE_NUM[cf_grade])
        if grade_diff >= GRADE_GAP_FATAL:
            findings.append({
                "signal": "GRADE_PROTECTION_GAP",
                "severity": "fatal",
                "self_grade": self_grade,
                "cf_grade": cf_grade,
                "grade_diff_levels": grade_diff,
                "explanation": f"内部 {self_grade} vs 外审 {cf_grade} (差 {grade_diff} 档) — Grade 重大分歧",
            })
            severity = "fatal"

    # 信号 3: FATAL_BLINDSPOT
    self_fatals = extract_fatal_codes(self_judge)
    cf_fatals = extract_fatal_codes(cf)
    cf_only_fatals = cf_fatals - self_fatals
    if cf_only_fatals:
        findings.append({
            "signal": "FATAL_BLINDSPOT",
            "severity": "fatal",
            "cf_only_fatal_codes": sorted(cf_only_fatals),
            "self_fatals": sorted(self_fatals),
            "explanation": (
                f"Counterfactual 报了 {len(cf_only_fatals)} 个 fatal finding "
                f"({','.join(sorted(cf_only_fatals))})，内部 ensemble 全没抓 — 集体盲点曝光"
            ),
        })
        severity = "fatal"

    cf_kill_shot = cf.get("one_sentence_kill_shot", "")
    cf_buy = cf.get("buy_or_reject", "")

    return {
        "ch": ch,
        "status": "analyzed",
        "severity": severity,
        "self_score": self_score,
        "cf_score": cf_score,
        "self_grade": self_grade,
        "cf_grade": cf_grade,
        "cf_one_sentence_kill_shot": cf_kill_shot,
        "cf_buy_or_reject": cf_buy,
        "findings": findings,
        "evidence_files": [str(self_path.relative_to(project_root)),
                           str(cf_path.relative_to(project_root))],
    }


# ============================================================
# 跨章趋势
# ============================================================

def analyze_trend(per_chapter: list[dict]) -> dict:
    """跨章趋势：内部 ensemble 是否系统性宽松。"""
    valid = [r for r in per_chapter
             if r.get("status") == "analyzed"
             and r.get("self_score") is not None
             and r.get("cf_score") is not None]
    if len(valid) < TREND_MIN_CHAPTERS:
        return {"status": "insufficient_data", "n": len(valid), "min_required": TREND_MIN_CHAPTERS}

    gaps = [r["self_score"] - r["cf_score"] for r in valid]
    mean_gap = sum(gaps) / len(gaps)
    chapters_self_lenient = sum(1 for g in gaps if g >= TREND_PERSISTENT_GAP)
    lenient_ratio = chapters_self_lenient / len(gaps)

    persistent = lenient_ratio >= 0.6 and mean_gap >= TREND_PERSISTENT_GAP
    return {
        "status": "analyzed",
        "n_chapters": len(valid),
        "mean_self_minus_cf": round(mean_gap, 2),
        "chapters_self_lenient_count": chapters_self_lenient,
        "lenient_ratio": round(lenient_ratio, 2),
        "persistent_self_protection": persistent,
        "interpretation": (
            f"跨 {len(valid)} 章，内部 ensemble 平均比外审高 {mean_gap:+.2f} 分；"
            f"{chapters_self_lenient}/{len(valid)} 章 ({lenient_ratio:.0%}) "
            f"内部宽松 ≥ {TREND_PERSISTENT_GAP} 分 — "
            f"{'系统性宽松，建议给 judge prompt 加严标' if persistent else '尚在正常波动'}"
        ),
    }


def find_chapters_with_counterfactual(project_root: Path) -> list[int]:
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.exists():
        return []
    chs = []
    for f in judge_dir.glob("ch_*_counterfactual.json"):
        m = re.match(r"ch_(\d+)_counterfactual\.json", f.name)
        if m:
            chs.append(int(m.group(1)))
    return sorted(chs)


# ============================================================
# 2026-05-29 cluster 化：把逐章 diff 聚合成 cluster diff + 跨 cluster trend
# ============================================================

_SEV_RANK = {"ok": 0, "advisory": 1, "warning": 2, "fatal": 3}


def aggregate_per_chapter_to_clusters(project_root: Path, per_chapter: list[dict]) -> list[dict]:
    """把逐章 diff 按所属 cluster 聚合。cluster_lookup 缺失/查不到归到 _unknown_ 桶。"""
    buckets: dict = defaultdict(list)
    for r in per_chapter:
        ch = r.get("ch")
        cid = None
        if cluster_lookup is not None and isinstance(ch, int):
            cid = cluster_lookup.ch_to_cluster_id(project_root, ch)
        buckets[cid or "_unknown_"].append(r)

    cluster_diffs = []
    for cid, rows in buckets.items():
        analyzed = [r for r in rows if r.get("status") == "analyzed"]
        gaps = [r["self_score"] - r["cf_score"] for r in analyzed
                if r.get("self_score") is not None and r.get("cf_score") is not None]
        mean_gap = round(sum(gaps) / len(gaps), 2) if gaps else None
        worst = max((r.get("severity", "ok") for r in rows),
                    key=lambda s: _SEV_RANK.get(s, 0), default="ok")
        fatal_signals = sorted({
            f["signal"] for r in rows for f in r.get("findings", [])
            if f.get("severity") == "fatal"
        })
        cluster_diffs.append({
            "cluster_id": cid,
            "chapters": sorted(r.get("ch") for r in rows if isinstance(r.get("ch"), int)),
            "chapters_analyzed": len(analyzed),
            "mean_self_minus_cf": mean_gap,
            "severity": worst,
            "fatal_signals": fatal_signals,
        })
    cluster_diffs.sort(key=lambda d: (d["cluster_id"] is None, str(d["cluster_id"])))
    return cluster_diffs


def analyze_trend_cluster(cluster_diffs: list[dict]) -> dict:
    """跨 cluster 趋势：内部 ensemble 是否系统性宽松（cluster 为单位）。"""
    valid = [d for d in cluster_diffs if d.get("mean_self_minus_cf") is not None]
    if len(valid) < 3:
        return {"status": "insufficient_data", "unit": "cluster",
                "n": len(valid), "min_required": 3}
    gaps = [d["mean_self_minus_cf"] for d in valid]
    mean_gap = sum(gaps) / len(gaps)
    lenient = sum(1 for g in gaps if g >= TREND_PERSISTENT_GAP)
    ratio = lenient / len(gaps)
    persistent = ratio >= 0.6 and mean_gap >= TREND_PERSISTENT_GAP
    return {
        "status": "analyzed",
        "unit": "cluster",  # 2026-05-29 cluster 化
        "n_clusters": len(valid),
        "mean_self_minus_cf": round(mean_gap, 2),
        "clusters_self_lenient_count": lenient,
        "lenient_ratio": round(ratio, 2),
        "persistent_self_protection": persistent,
        "interpretation": (
            f"跨 {len(valid)} 个 cluster，内部 ensemble 平均比外审高 {mean_gap:+.2f} 分；"
            f"{lenient}/{len(valid)} 个 cluster ({ratio:.0%}) 内部宽松 ≥ {TREND_PERSISTENT_GAP} 分 — "
            f"{'系统性宽松，建议给 judge prompt 加严标' if persistent else '尚在正常波动'}"
        ),
    }


# ============================================================
# 主流程
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Self-Protection 曝光器 v23 Layer 2+3")
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, help="只分析指定章")
    ap.add_argument("--cluster", help="只分析指定 cluster（如 cluster_002 / 2 · 2026-05-29）")
    ap.add_argument("--trend", action="store_true", help="只跑跨 cluster 趋势")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[counterfactual_diff] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)

    # 2026-05-29 cluster 化：--cluster 把章范围解析为该 cluster 的物理章
    cluster_filter_chs = None
    if args.cluster is not None and cluster_lookup is not None:
        rng = cluster_lookup.cluster_id_to_range(project_root, args.cluster)
        if rng and len(rng) == 2:
            cluster_filter_chs = set(range(rng[0], rng[1] + 1))

    if args.ch is not None:
        chs = [args.ch]
    else:
        chs = find_chapters_with_counterfactual(project_root)
        if cluster_filter_chs is not None:
            chs = [c for c in chs if c in cluster_filter_chs]
    if not chs:
        print("[counterfactual_diff] 未发现匹配的 ch_*_counterfactual.json — 先 spawn novel-counterfactual-judge")
        sys.exit(0)

    per_chapter = []
    for ch in chs:
        r = diff_chapter(project_root, ch)
        if r:
            per_chapter.append(r)

    # 2026-05-29 cluster 化：逐章 diff 聚合成 cluster diff
    cluster_diffs = aggregate_per_chapter_to_clusters(project_root, per_chapter)
    # 趋势优先 cluster 单位；cluster 不足回退逐章趋势（向后兼容）
    single = args.ch is not None or args.cluster is not None
    trend = None
    if not single:
        trend = analyze_trend_cluster(cluster_diffs)
        if trend.get("status") != "analyzed":
            trend = analyze_trend(per_chapter)

    fatals = [r for r in per_chapter if r.get("severity") == "fatal"]
    warnings = [r for r in per_chapter if r.get("severity") == "warning"]
    advisories = [r for r in per_chapter if r.get("severity") == "advisory"]

    summary = {
        "chapters_analyzed": len(per_chapter),
        "fatal_self_protection": len(fatals),
        "warning": len(warnings),
        "advisory": len(advisories),
        "persistent_self_protection": (trend or {}).get("persistent_self_protection", False),
    }

    out = {
        "scan_type": "counterfactual_judge_diff",
        "analysis_unit": "cluster",  # 2026-05-29 cluster 化：trend/聚合按 cluster
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "per_chapter": per_chapter,
        "cluster_diffs": cluster_diffs,  # 2026-05-29 cluster 化
        "trend": trend,
        "_note": (
            "Counterfactual Debating (arxiv 2406.11514) — counterfactual judge 被骗以为是匿名"
            "投稿，跟内部 ensemble 分歧 = self-protection 偏见曝光"
        ),
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"counterfactual_judge_diff_{ts}.json"
    save_json(out_path, out)

    print(f"[counterfactual_diff] 分析 {summary['chapters_analyzed']} 章: "
          f"{summary['fatal_self_protection']}🚨 / {summary['warning']}⚠️ / {summary['advisory']}ℹ️")
    if trend and trend.get("status") == "analyzed":
        print(f"  趋势: {trend['interpretation']}")
    for r in fatals[:4]:
        ch = r["ch"]
        for f in r.get("findings", []):
            if f["severity"] == "fatal":
                print(f"  🚨 ch{ch} {f['signal']}: {f.get('explanation', '')[:100]}")
                break
    for r in warnings[:3]:
        ch = r["ch"]
        for f in r.get("findings", []):
            if f["severity"] == "warning":
                print(f"  ⚠️ ch{ch} {f['signal']}: gap={f.get('gap')}")
                break
    print(f"  报告: {out_path}")

    if fatals or summary["persistent_self_protection"]:
        sys.exit(2)
    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
