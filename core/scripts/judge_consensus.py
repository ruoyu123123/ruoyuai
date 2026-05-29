"""judge_consensus.py — 多 Judge 共识仲裁器（v17.4 / OpenClaw 启发 + P2-6 persona）

为关键决策提供 2-3 个独立 judge 并行评 + 主代理仲裁机制。
解决"单 judge 一票通过、易被 writer 引导"问题。

设计原则：
1. 关键维度（opening_type 匹配 / ending_type 匹配 / anchor_hit 真实性 / voice 匹配）
   不能由单 judge 一锤定音
2. 2-3 个 judge 独立评，结果用 majority vote 或 median 仲裁
3. 不一致超阈值 → 升级到用户
4. 【P2-6】支持 persona 维度：JudgeReport 可选 `persona` 字段（如
   'common_reader' / 'developmental_editor' / 'line_editor' / 'harsh_critic'），
   merge 时按 persona 分组算平均，跨 persona 分歧 ≥ 2 grade levels → escalate。
   向前兼容：所有 report 都没 persona 字段 → 退回原行为。

用法（被主代理调用）：
    python judge_consensus.py merge <judge_report_1.json> <judge_report_2.json> [...]

输出（stdout JSON）：
    {
      "consensus_grade": "A",
      "consensus_confidence": 0.82,
      "agreement_score": 0.95,
      "majority_findings": {...},
      "dissent": [...],
      "persona_breakdown": {                   # P2-6
        "common_reader": {"count":1,"avg_grade":"A","grades":["A"]},
        "harsh_critic":  {"count":1,"avg_grade":"C","grades":["C"]}
      },
      "persona_dissent_severity": 2.0,         # P2-6：persona 间 grade level 极差
      "escalate_to_user": false
    }
"""

import sys
import json
import math
from pathlib import Path
from collections import Counter


GRADE_TO_NUM = {"A": 4, "B": 3, "C": 2, "D": 1}
NUM_TO_GRADE = {4: "A", 3: "B", 2: "C", 1: "D"}


def median_grade(grades: list[str]) -> str:
    """grades=['A','B','A'] → 'A'（majority + median tie-break）"""
    if not grades:
        return "C"
    nums = sorted([GRADE_TO_NUM.get(g, 2) for g in grades])
    n = len(nums)
    if n % 2 == 1:
        med = nums[n // 2]
    else:
        # 2026-05-29 修：偶数个 judge 时原来取上中位 nums[n//2]（偏高），
        # 与 self-protection 检测目标（宁低勿高）矛盾，例如 ['A','C'] 恒判 A。
        # 改为取两个中位的平均并向下取整（偏保守/偏低），['A','C']→B、['B','D']→C。
        med = math.floor((nums[n // 2 - 1] + nums[n // 2]) / 2)
    # 钳到合法 grade 区间 [1, 4]
    med = min(4, max(1, med))
    return NUM_TO_GRADE[med]


def agreement_score(grades: list[str]) -> float:
    """相同 grade 比例。3 个全相同 = 1.0；2:1 split = 0.67；3 个全不同 = 0.33"""
    if not grades:
        return 0.0
    counter = Counter(grades)
    return max(counter.values()) / len(grades)


def persona_breakdown(reports: list[dict]) -> dict:
    """P2-6：按 persona 字段分组分析评分。
    缺失 persona 视为 'default'。返回
        {persona: {count, grades, avg_grade_num, avg_grade}}。
    用途：在多 persona judges 场景（老读者 / 严苛书评人 / 编辑等）下，
    识别「不同视角间」的系统性分歧。"""
    by: dict = {}
    for r in reports:
        p = r.get("persona") or "default"
        by.setdefault(p, {"count": 0, "grades": []})
        by[p]["count"] += 1
        by[p]["grades"].append(r.get("overall_grade", "C"))
    for p, info in by.items():
        nums = [GRADE_TO_NUM.get(g, 2) for g in info["grades"]]
        info["avg_grade_num"] = round(sum(nums) / max(1, len(nums)), 2)
        # 平均数四舍五入到最近 grade（边界 0.5 偏向高分）
        info["avg_grade"] = NUM_TO_GRADE.get(round(info["avg_grade_num"]), "C")
    return by


def persona_dissent_severity(breakdown: dict) -> float:
    """P2-6：跨 persona 的分歧度。返回 max - min 的 grade level 差（0-3）。
    breakdown 元素数 < 2 → 返回 0（没有 persona 对比基础）。"""
    if len(breakdown) < 2:
        return 0.0
    avgs = [info["avg_grade_num"] for info in breakdown.values()]
    return max(avgs) - min(avgs)


def merge_reports(reports: list[dict]) -> dict:
    """合并 N 个 JudgeReport 为 consensus。"""
    if not reports:
        return {"error": "no reports"}

    grades = [r.get("overall_grade", "C") for r in reports]
    confidences = [r.get("confidence", 0.5) for r in reports]
    chapters = [r.get("chapter") for r in reports]
    judge_ids = [r.get("judge_id", "?") for r in reports]

    consensus_grade = median_grade(grades)
    avg_confidence = sum(confidences) / len(confidences)
    agreement = agreement_score(grades)

    # 收集 dissent（不同意见）
    dissent = []
    for r, g in zip(reports, grades):
        if g != consensus_grade:
            dissent.append({
                "judge_id": r.get("judge_id"),
                "grade": g,
                "confidence": r.get("confidence"),
                "reasoning_trace": r.get("reasoning_trace", []),
                "uncertainty_flags": r.get("uncertainty_flags", []),
            })

    # P2-6：persona 维度分析
    pbreak = persona_breakdown(reports)
    pdissent = persona_dissent_severity(pbreak)

    # P2-11：evidence_quotes 完备度统计
    # 业界 grounding 实践：judge 评分必须附原文 quote。无 quote 评的 grade 凭印象
    # 不可靠 → 降权或升级。统计 reports 中 evidence_quotes 段非空且 ≥2 条的比例。
    reports_with_evidence = sum(
        1 for r in reports
        if isinstance(r.get("evidence_quotes"), list) and len(r["evidence_quotes"]) >= 2
    )
    evidence_quality = reports_with_evidence / max(1, len(reports))

    # 升级条件
    escalate = False
    escalate_reasons = []
    # 2026-05-29 修：原 `< 0.5` 时 2 judge 完全分歧（agreement=0.5）不触发升级。
    # 改为 `<= 0.5`，让 2 judge 各执一词（如 A vs C）也能升级到用户。
    if agreement <= 0.5:
        escalate = True
        escalate_reasons.append(f"agreement {agreement:.2f} <= 0.5 严重分歧")
    if avg_confidence < 0.65:
        escalate = True
        escalate_reasons.append(f"avg confidence {avg_confidence:.2f} < 0.65 整体信心不足")
    # 任何 judge 给 D 都要升级
    if "D" in grades:
        escalate = True
        escalate_reasons.append("至少一个 judge 给 D 级")
    # P2-6：persona 间分歧 ≥ 2 grade levels → 升级
    # （如老读者评 A 但严苛书评人评 C，4-2=2，说明视角悬殊需人工权衡）
    if pdissent >= 2.0 and len(pbreak) >= 2:
        escalate = True
        personas_list = ", ".join(f"{p}={info['avg_grade']}" for p, info in pbreak.items())
        escalate_reasons.append(
            f"persona 间分歧 {pdissent:.1f} grade levels（{personas_list}）"
        )

    # P2-11：evidence_quality < 0.5 → 升级（多数 judge 凭印象评分，结论不可靠）
    if len(reports) > 0 and evidence_quality < 0.5:
        escalate = True
        escalate_reasons.append(
            f"evidence 不足 {evidence_quality:.2f} < 0.5（{reports_with_evidence}/"
            f"{len(reports)} 个 judge 给了 ≥2 条 quote 支撑，其余凭印象评分）"
        )

    # 合并 findings：对 validator-repair 类的 style_directive_check 字段做 OR
    merged_findings = {}
    for r in reports:
        sf = r.get("specific_findings", {})
        for k, v in sf.items():
            if k not in merged_findings:
                merged_findings[k] = [v]
            else:
                merged_findings[k].append(v)

    # 收集所有 uncertainty
    all_uncertainty = []
    for r in reports:
        all_uncertainty.extend(r.get("uncertainty_flags", []))

    # v17.5 C5：检测 schema 版本兼容
    schema_versions = [r.get("schema_version") for r in reports if r.get("schema_version")]
    if schema_versions and len(set(schema_versions)) > 1:
        # 不一致告警
        print(f"[WARN] reports schema_version 不一致：{schema_versions}", file=sys.stderr)

    return {
        "schema_version": "1.2",  # P2-6：persona | P2-11：evidence_quality
        "consensus_grade": consensus_grade,
        "consensus_confidence": round(avg_confidence, 3),
        "agreement_score": round(agreement, 3),
        "chapter": chapters[0] if chapters else None,
        "judges_participated": judge_ids,
        "grades_distribution": dict(Counter(grades)),
        "majority_findings_merged": merged_findings,
        "dissent": dissent,
        "persona_breakdown": pbreak,                       # P2-6
        "persona_dissent_severity": round(pdissent, 2),    # P2-6
        "evidence_quality": round(evidence_quality, 2),    # P2-11
        "reports_with_evidence": reports_with_evidence,    # P2-11
        "all_uncertainty_flags": all_uncertainty,
        "escalate_to_user": escalate,
        "escalate_reasons": escalate_reasons,
    }


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "merge":
        print("用法: python judge_consensus.py merge <report1.json> <report2.json> [report3.json]")
        sys.exit(1)
    paths = [Path(p) for p in sys.argv[2:]]
    reports = []
    for p in paths:
        if not p.exists():
            print(f"[FATAL] 找不到 report: {p}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            reports.append(json.load(f))
    consensus = merge_reports(reports)
    print(json.dumps(consensus, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
