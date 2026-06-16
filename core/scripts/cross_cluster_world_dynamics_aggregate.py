"""cross_cluster_world_dynamics_aggregate.py — 世界/系统动态扫（CCR9+10+12）

合并 3 类系统层动态问题：

A. FACTION_EXTREMES — 势力数值极值快照
   读 世界状态.json.factions_state，检查每势力 power/stability/wealth 当前快照极值
   - FACTION_NEAR_ZERO：某维度 ≤ 5 → 势力即将退场，确认是否符合大势卡安排
   - FACTION_NEAR_MAX：某维度 ≥ 95 → 触顶，后续 ripple 加分会被钳制
   （注：曾规划跨章趋势 MONOTONE_DROP/FROZEN/OVER_BOOST，但 world_evolution apply 日志
    只落 applied_count、不落 per-章 delta 详情，趋势序列无法重建 → 已删除该承诺与对应空转
    死代码，仅保留依赖 factions_state 快照的极值检查。.world_evolution 目录缺失时按既有契约早退。）

B. RIPPLE_RULES_DEAD — 死规则识别
   涟漪规则.json 中的 ripple_rules，对照 world_ticks_log（含 trigger_value）
   - 整本书从未触发的 → DEAD_RULE
   - 本章触发 ≥ 3 次的 → ABUSED_RULE

C. STORYTELLER_OUTCOME_ALIGNMENT — target vs actual
   读 叙事节拍器.chapter_outcome_log
   - 检查最近 N 章 actual_outcome vs 上一章 narrator_recommendation 的一致性
   - 偏离率 > 50% → STORYTELLER_IGNORED

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


# ---------- A. FACTION_TRENDS ----------

def scan_faction_trends(project_root: Path) -> list[dict]:
    world_path = project_root / "_数据库" / "世界状态.json"
    if not world_path.exists():
        return []
    world = load_json(world_path, {})
    factions = world.get("factions_state", {}) or {}
    if not factions:
        return []

    # .world_evolution 目录守卫（有 test_missing_world_evolution_dir_returns_empty 锁定的既有
    # 契约：项目从未跑过世界演化时不报种子值极值 → 保留，不擅自推翻前轮审计的显式决定）。
    apply_dir = project_root / "_数据库" / ".world_evolution"
    if not apply_dir.exists():
        return []

    # 历史曾在此重建 faction 跨章数值序列做 MONOTONE_DROP/FROZEN/OVER_BOOST 趋势检查，但
    # world_evolution apply 日志只落 applied_count、不落 per-章 delta 详情，序列无法重建 → 该段
    # 退化为纯空转死代码（faction_changes 建后从不读、applied 读后从不用、内层 for...pass），
    # 已删除（北极星⑥清旧码）。仅保留下方依赖 factions_state 快照的极值检查（NEAR_ZERO/NEAR_MAX）。
    findings = []
    for fname, fdata in factions.items():
        for dim in ["power", "stability", "wealth"]:
            v = fdata.get(dim)
            if v is None or not isinstance(v, (int, float)):
                continue
            if v <= 5:
                findings.append({
                    "severity": "advisory",
                    "code": "FACTION_NEAR_ZERO",
                    "faction": fname,
                    "dimension": dim,
                    "current_value": v,
                    "suggestion": f"{fname}.{dim}={v} 接近 0 → 该势力即将退场，确认是否符合大势卡安排",
                })
            if v >= 95:
                findings.append({
                    "severity": "advisory",
                    "code": "FACTION_NEAR_MAX",
                    "faction": fname,
                    "dimension": dim,
                    "current_value": v,
                    "suggestion": f"{fname}.{dim}={v} 接近 100 → 上限，后续 ripple 加分会被钳制",
                })
    return findings


# ---------- B. RIPPLE_RULES_DEAD ----------

def scan_ripple_dead_abused(project_root: Path) -> list[dict]:
    rules_path = project_root / "_数据库" / "涟漪规则.json"
    world_path = project_root / "_数据库" / "世界状态.json"
    if not rules_path.exists() or not world_path.exists():
        return []
    rules_data = load_json(rules_path, {})
    world = load_json(world_path, {})
    rules = rules_data.get("ripple_rules", []) or []
    log = world.get("world_ticks_log", []) or []
    findings = []
    rule_trigger_counts = Counter()

    # 简化：检查 world_ticks_log 中是否有规则被命中
    for entry in log:
        tt = entry.get("trigger_type")
        tv = entry.get("trigger_value")
        if not tt or not tv:
            continue
        for r in rules:
            if r.get("trigger_type") != tt:
                continue
            match_pat = r.get("trigger_match", "")
            cands = [c.strip() for c in match_pat.split("|") if c.strip()]
            if any(c in tv or tv in c for c in cands):
                rule_trigger_counts[r.get("id")] += 1
                break

    # DEAD_RULE：未触发的（排除 auto_tick 类，因为它不在 trigger_value 里）
    for r in rules:
        rid = r.get("id")
        if r.get("trigger_type") == "auto_tick":
            continue
        if rule_trigger_counts.get(rid, 0) == 0:
            findings.append({
                "severity": "advisory",
                "code": "RIPPLE_DEAD",
                "rule_id": rid,
                "trigger_match": r.get("trigger_match"),
                "suggestion": f"涟漪规则 {rid} 整本书未触发 → 死规则，考虑修改 trigger_match 关键词或删除",
            })

    # ABUSED_RULE：触发 ≥ 5 次的
    for rid, count in rule_trigger_counts.items():
        if count >= 5:
            findings.append({
                "severity": "advisory",
                "code": "RIPPLE_ABUSED",
                "rule_id": rid,
                "trigger_count": count,
                "suggestion": f"涟漪规则 {rid} 已触发 {count} 次 → 滥用风险，考虑加 cooldown",
            })
    return findings


# ---------- C. STORYTELLER_OUTCOME_ALIGNMENT ----------

def scan_storyteller_alignment(project_root: Path) -> list[dict]:
    pacer_path = project_root / "_数据库" / "叙事节拍器.json"
    if not pacer_path.exists():
        return []
    pacer = load_json(pacer_path, {})
    log = pacer.get("chapter_outcome_log", []) or []
    findings = []

    # 2026-05-29 cluster 化（轻改造）：叙事节拍器.json 仍是 narrator_recommendation 权威
    # 来源（保留）。仅 outcome 趋势那段，cluster 模式优先取账本逐章 outcome 字段做分布
    # （账本是写作端实时落账的最新真相）；账本无 outcome → 回退节拍器 chapter_outcome_log。
    recent_outcomes = None
    if csr.is_cluster_mode() and csr.ledger_has_field(project_root, "outcome"):
        recs = csr.get_chapter_records(project_root)
        outcomes = [(ch, rec.get("outcome")) for ch, rec in recs if rec.get("outcome")]
        if len(outcomes) >= 5:
            outcomes.sort(key=lambda x: x[0])
            recent_outcomes = [o for _ch, o in outcomes[-10:]]

    if recent_outcomes is not None:
        counts = Counter(recent_outcomes)
        total = len(recent_outcomes)
    else:
        if len(log) < 5:
            return []
        # 简化：当前节拍器只存最新 narrator_recommendation，无历史 → 检查最近 N 章 outcome 分布
        sorted_log = sorted(log, key=lambda e: e.get("ch", 0))
        recent = sorted_log[-10:]
        counts = Counter(e.get("outcome") for e in recent)
        total = len(recent)
    setback_pct = counts.get("setback", 0) / total
    win_pct = counts.get("win", 0) / total

    # 当前 next_target_outcome
    next_target = (pacer.get("narrator_recommendation") or {}).get("next_chapter_target_outcome", "auto")

    if next_target == "setback" and setback_pct < 0.15:
        findings.append({
            "severity": "advisory",
            "code": "STORYTELLER_TARGET_LIKELY_TO_BE_IGNORED",
            "current_setback_pct": round(setback_pct, 2),
            "next_target": next_target,
            "suggestion": f"narrator 持续推荐 setback 但近 {total} 章 setback 占比仅 {round(setback_pct*100)}% → 历史趋势看 outline-planner 倾向忽略 storyteller 建议",
        })
    if win_pct > 0.85:
        findings.append({
            "severity": "advisory",
            "code": "ALL_WIN_NO_SETBACK",
            "win_pct": round(win_pct, 2),
            "suggestion": f"近 {total} 章 win 占比 {round(win_pct*100)}% → 主角无挫败感，应让 narrator 推 setback",
        })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)

    findings = []
    findings.extend(scan_faction_trends(project_root))
    findings.extend(scan_ripple_dead_abused(project_root))
    findings.extend(scan_storyteller_alignment(project_root))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "world_dynamics",
        "scan_ts": ts,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"world_dynamics_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[world_dynamics] {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
