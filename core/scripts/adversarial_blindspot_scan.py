"""adversarial_blindspot_scan.py — 集体盲点曝光器（v23 Layer 1 辅助）

业界依据：VIGIL (arxiv 2512.07094) sibling supervisor · Counterfactual Debating
(2406.11514) preset stances · Self-Correction Bench (2507.02778)：单一视角自检
会有 64.5% 盲点率，**需要异质 sibling 做交叉对比才能曝光**。

【它做的事】
读 `novel-adversarial-reader` 的报告 vs 内部 audit / reading-reflector 报告，
做 issue type 集合 diff：

  adversarial 抓到 ∧ 内部全部没抓到 → **集体盲点曝光**（最高价值）
  adversarial 抓到 ∧ 内部部分抓到    → 重要 issue，至少没全盲
  内部抓到 ∧ adversarial 没抓到      → adversarial 视角弱区（次要参考）

【关键】blindspot 优先级
- adversarial verdict=drop_book/skip_skim 且内部全 pass → **红色警报**（用户体验崩坏但
  机械检测全过 = 系统性盲点，必须 escalate）
- adversarial issue type 集合中存在 > 2 个 type 内部全没覆盖 → 该类 type 是系统盲区

【输出】
<project>/_数据库/.learning/adversarial_blindspots_<ts>.json

【CLI】
  python adversarial_blindspot_scan.py <project>            # 扫所有有 adversarial 报告的章
  python adversarial_blindspot_scan.py <project> --ch N     # 指定章

退出码：0 健康 / 1 advisory 盲点 / 2 红色警报（系统性盲点）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================
# adversarial issue type → 内部维度的近义映射
# 用于判断"内部检测器是否覆盖了这个 type"
# ============================================================
TYPE_ALIAS = {
    "AI味":      ["anti_slop", "anti-slop", "AI 套话", "AI腔", "结构层anti-slop", "BANNED_"],
    "塑料感":    ["塑料感", "voice 漂移", "互动质感", "READER_EXP_"],
    "看着烦":    ["重复", "info_dump", "long_paragraph", "STYLE_单段超长"],
    "无聊":      ["pacing", "节奏感", "钩子", "HOOK_", "黄金三章"],
    "看不懂":    ["信息密度", "info_dump", "上下文"],
    "出戏":      ["POV", "perspective_shift", "tone"],
    "前后不搭":  ["LOCKED_FACT", "FUTURE_KNOWLEDGE", "FORESHADOW", "consistency"],
    "人物不像人":["CHARACTER_", "OOC", "persona", "对话工艺"],
    "假":        ["对话工艺", "DIALOGUE_", "互动质感"],
    "无效信息":  ["info_dump", "长描写", "节奏"],
}


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


def collect_internal_signals(project_root: Path, ch: int) -> dict:
    """收集第 ch 章的所有内部检测信号（audit_hub + reading-reflector + judge）。"""
    signals = {
        "audit_codes": set(),         # audit_hub 的 issue code 集合
        "reflector_dims": set(),       # reading-reflector 的 dimension 集合
        "judge_warnings": set(),       # judge 报告的 health_warnings
        "raw_evidence": [],            # 用于报告里溯源
    }

    audit_dir = project_root / "_数据库" / ".audit"
    if audit_dir.exists():
        for f in audit_dir.glob(f"ch_{ch:03d}_audit*.json"):
            d = load_json(f, {})
            for issue in (d.get("issues") or []):
                if not issue.get("waived"):
                    code = issue.get("code")
                    if code:
                        signals["audit_codes"].add(code)
            signals["raw_evidence"].append(f.name)

    rr_dir = project_root / "_数据库" / ".reading_reflection"
    if rr_dir.exists():
        for f in rr_dir.glob(f"cluster_*_round_*.json"):
            d = load_json(f, {})
            ch_list = []
            for issue in (d.get("new_issues_this_round") or []):
                if issue.get("ch") == ch:
                    dim = issue.get("dimension")
                    if dim:
                        signals["reflector_dims"].add(dim)
                    ch_list.append(issue)
            if ch_list:
                signals["raw_evidence"].append(f.name)

    judge_dir = project_root / "_数据库" / ".judge_reports"
    if judge_dir.exists():
        for f in judge_dir.glob(f"ch_{ch:03d}_*.json"):
            d = load_json(f, {})
            for w in (d.get("health_warnings") or []):
                if isinstance(w, str):
                    signals["judge_warnings"].add(w)
                elif isinstance(w, dict):
                    code = w.get("code") or w.get("warning")
                    if code:
                        signals["judge_warnings"].add(code)

    return signals


def type_covered_by_internal(issue_type: str, internal: dict) -> bool:
    """判断 adversarial 的某个 issue type 是否被内部检测器覆盖。"""
    aliases = TYPE_ALIAS.get(issue_type, [])
    if not aliases:
        return False
    all_internal = (
        " ".join(internal["audit_codes"]) + " " +
        " ".join(internal["reflector_dims"]) + " " +
        " ".join(internal["judge_warnings"])
    )
    return any(alias in all_internal for alias in aliases)


def analyze_chapter(project_root: Path, ch: int) -> dict | None:
    """对一章做 adversarial vs internal 的 diff。返回 None 表示没有 adversarial 报告可分析。"""
    adv_path = project_root / "_数据库" / ".audit" / f"ch_{ch:03d}_adversarial.json"
    if not adv_path.exists():
        return None
    adv = load_json(adv_path, {})
    if adv.get("context_contamination"):
        return {
            "ch": ch,
            "status": "skipped_contaminated",
            "reason": "adversarial reader 报告了 context 污染，本次结果不可信",
        }

    adv_issues = adv.get("issues") or []
    adv_types = Counter(i.get("type", "?") for i in adv_issues)
    adv_verdict = adv.get("verdict", "?")

    internal = collect_internal_signals(project_root, ch)

    # 类型覆盖分析
    blindspot_types = []     # adversarial 抓到、内部全部没覆盖
    partial_types = []       # 部分覆盖
    for t, cnt in adv_types.items():
        if type_covered_by_internal(t, internal):
            partial_types.append({"type": t, "count": cnt})
        else:
            blindspot_types.append({
                "type": t,
                "count": cnt,
                "sample_quotes": [
                    i.get("quote", "")[:80]
                    for i in adv_issues if i.get("type") == t
                ][:3],
                "reactions": [
                    i.get("real_reader_reaction", "")[:80]
                    for i in adv_issues if i.get("type") == t
                ][:3],
            })

    # 红色警报判定
    user_dropped = adv_verdict in ("drop_book", "skip_skim")
    internal_clean = (
        len(internal["audit_codes"]) == 0
        and len(internal["reflector_dims"]) == 0
        and len(internal["judge_warnings"]) == 0
    )
    red_alert = user_dropped and internal_clean

    severity = "ok"
    if red_alert:
        severity = "red_alert"
    elif len(blindspot_types) > 2:
        severity = "warning"
    elif blindspot_types:
        severity = "advisory"

    return {
        "ch": ch,
        "status": "analyzed",
        "severity": severity,
        "adversarial_verdict": adv_verdict,
        "adversarial_one_liner": adv.get("verdict_one_liner", ""),
        "adversarial_total_issues": len(adv_issues),
        "internal_audit_codes_count": len(internal["audit_codes"]),
        "internal_reflector_dims_count": len(internal["reflector_dims"]),
        "blindspot_types": blindspot_types,
        "partial_coverage_types": partial_types,
        "red_alert_reason": (
            f"用户视角弃书（{adv_verdict}）但内部检测全 pass —— 经典系统性盲点"
            if red_alert else None
        ),
        "raw_evidence": internal["raw_evidence"][:8] + [adv_path.name],
    }


def find_chapters_with_adversarial(project_root: Path) -> list[int]:
    audit_dir = project_root / "_数据库" / ".audit"
    if not audit_dir.exists():
        return []
    chs = []
    for f in audit_dir.glob("ch_*_adversarial.json"):
        m = re.match(r"ch_(\d+)_adversarial\.json", f.name)
        if m:
            chs.append(int(m.group(1)))
    return sorted(chs)


def aggregate_blindspot_types(per_chapter: list[dict]) -> list[dict]:
    """跨章节聚合：哪些 type 反复盲，构成"系统盲区"。"""
    type_chapters = defaultdict(list)
    for r in per_chapter:
        for bt in r.get("blindspot_types", []) or []:
            type_chapters[bt["type"]].append(r["ch"])
    return [
        {
            "type": t,
            "blind_chapter_count": len(set(chs)),
            "chapters": sorted(set(chs))[:10],
            "is_systemic": len(set(chs)) >= 3,  # 3 章以上 = 系统盲区
        }
        for t, chs in type_chapters.items()
    ]


def main():
    ap = argparse.ArgumentParser(description="集体盲点曝光器 v23 Layer 1")
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, help="只分析指定章")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[blindspot_scan] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)

    chs = [args.ch] if args.ch is not None else find_chapters_with_adversarial(project_root)
    if not chs:
        print("[blindspot_scan] 未发现任何 ch_*_adversarial.json — 先跑 novel-adversarial-reader agent")
        sys.exit(0)

    per_chapter = []
    for ch in chs:
        r = analyze_chapter(project_root, ch)
        if r:
            per_chapter.append(r)

    type_aggregate = aggregate_blindspot_types(per_chapter)
    systemic_types = [t for t in type_aggregate if t["is_systemic"]]

    red_alerts = [r for r in per_chapter if r.get("severity") == "red_alert"]
    warnings = [r for r in per_chapter if r.get("severity") == "warning"]
    advisories = [r for r in per_chapter if r.get("severity") == "advisory"]

    summary = {
        "chapters_analyzed": len(per_chapter),
        "red_alert": len(red_alerts),
        "warning": len(warnings),
        "advisory": len(advisories),
        "systemic_blindspot_types": [t["type"] for t in systemic_types],
    }

    out = {
        "scan_type": "adversarial_blindspot",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "per_chapter": per_chapter,
        "type_aggregate": type_aggregate,
        "systemic_blindspot_types": systemic_types,
        "_note": (
            "Self-Correction Blind Spot 64.5% (arxiv 2507.02778) — 内部 audit / "
            "reading-reflector / judge 全 pass 但 adversarial reader 弃书 = 集体盲点曝光"
        ),
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"adversarial_blindspots_{ts}.json"
    save_json(out_path, out)

    print(f"[blindspot_scan] 分析 {summary['chapters_analyzed']} 章: "
          f"{summary['red_alert']}🚨 / {summary['warning']}⚠️ / {summary['advisory']}ℹ️")
    if systemic_types:
        print(f"  系统盲区 (≥3 章反复盲): {', '.join(t['type'] for t in systemic_types)}")
    for r in red_alerts[:4]:
        print(f"  🚨 ch{r['ch']}: {r.get('red_alert_reason', '')}")
        print(f"     verdict={r['adversarial_verdict']}: {r.get('adversarial_one_liner', '')[:80]}")
    for r in warnings[:3]:
        bt_names = [b["type"] for b in r.get("blindspot_types", [])][:3]
        print(f"  ⚠️ ch{r['ch']}: 盲点 type = {', '.join(bt_names)}")
    print(f"  报告: {out_path}")

    if red_alerts:
        sys.exit(2)
    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
