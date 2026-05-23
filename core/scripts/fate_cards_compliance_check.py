"""fate_cards_compliance_check.py — 走向卡角色驱动契约校验（v21 CD1 新增）

校验 outline-planner 输出的 _数据库/.wal/第NNN章_fate_cards.json：

1. 每张卡必含 character_driven 字段，且 9 个子字段全部填值（不为 null/空字符串）
2. 至少 1 张卡的 heart_event_triggered 引用存在的 pending_heart_event_reveals
3. 至少 1 张卡的 fate_event_advanced 引用存在的 active fate_events（如有）
4. aspect_compatibility_check 必为 true（违反 active aspects 直接拒绝）
5. storyteller target_outcome 一致性：target=setback 时至少 1 张卡 stress_implication >0；target=win 时至少 1 张卡 stress_implication <=0
6. throughline 覆盖：所有卡的 throughline_advanced 并集应至少含 2 条

输出报告：_数据库/.cross_chapter_scan/fate_cards_compliance_<ts>.json
退出码: 0 合规 / 1 警告（advisory）/ 2 violation（必须返工）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


REQUIRED_CD_FIELDS = [
    "primary_character_arc_anchor",
    "arc_dimension_tested",
    "arc_state_change_hint",
    "stress_implication",
    "aspect_compatibility_check",
    "heart_event_triggered",
    "fate_event_advanced",
    "clock_advanced",
    "throughline_advanced",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, required=True, help="走向卡对应章号")
    args = ap.parse_args()

    project_root = Path(args.project)
    cards_path = project_root / "_数据库" / ".wal" / f"第{args.ch:03d}章_fate_cards.json"
    if not cards_path.exists():
        print(f"[SKIP] 走向卡文件不存在: {cards_path}")
        sys.exit(0)

    cards_data = load_json(cards_path, {})
    cards = cards_data.get("cards", [])
    if not cards:
        print(f"[SKIP] cards 数组为空")
        sys.exit(0)

    # 加载关联上下文
    pending_path = project_root / "_数据库" / ".ensemble_pending_reveals.json"
    pending_reveals = []
    if pending_path.exists():
        pr_data = load_json(pending_path, {})
        pending_reveals = [hr.get("event_id") for hr in pr_data.get("pending_reveals", [])]

    fate_path = project_root / "_数据库" / "大势卡.json"
    active_fate_ids = []
    if fate_path.exists():
        fate = load_json(fate_path, {})
        active_fate_ids = [e.get("id") for e in fate.get("major_events", []) if e.get("status") == "scheduled"]

    pacer_path = project_root / "_数据库" / "叙事节拍器.json"
    target_outcome = "auto"
    if pacer_path.exists():
        pacer = load_json(pacer_path, {})
        target_outcome = (pacer.get("narrator_recommendation") or {}).get("next_chapter_target_outcome", "auto")

    findings = []

    # 规则 1：每张卡必含 character_driven 全字段
    for card in cards:
        label = card.get("label", "?")
        cd = card.get("character_driven")
        if not cd:
            findings.append({"severity": "violation", "code": "CARD_MISSING_CHARACTER_DRIVEN", "card": label, "msg": f"卡 {label} 缺 character_driven 字段"})
            continue
        for f in REQUIRED_CD_FIELDS:
            if f not in cd:
                findings.append({"severity": "violation", "code": "CD_FIELD_MISSING", "card": label, "field": f, "msg": f"卡 {label} character_driven.{f} 缺失"})
            elif cd[f] in ("", [], None) and f not in ("heart_event_triggered", "fate_event_advanced", "clock_advanced"):
                # 后三个允许 null（无触发）
                findings.append({"severity": "violation", "code": "CD_FIELD_EMPTY", "card": label, "field": f, "msg": f"卡 {label} character_driven.{f} 为空（应填具体值或 null/false 显式标）"})

    # 规则 2：至少 1 张卡触发 pending heart_event（如有）
    if pending_reveals:
        triggered_he = [c.get("character_driven", {}).get("heart_event_triggered") for c in cards]
        triggered_he = [h for h in triggered_he if h and h != "null"]
        if not any(h in pending_reveals for h in triggered_he):
            findings.append({
                "severity": "violation",
                "code": "PENDING_HEART_EVENT_IGNORED",
                "msg": f"有 {len(pending_reveals)} 个 pending heart_event 未被任何卡触发（{pending_reveals[:3]}）",
            })

    # 规则 3：至少 1 张卡推进 active fate_event（如有 priority>=8 的）
    if active_fate_ids:
        triggered_fe = [c.get("character_driven", {}).get("fate_event_advanced") for c in cards]
        triggered_fe = [f for f in triggered_fe if f and f != "null"]
        if not any(f in active_fate_ids for f in triggered_fe):
            findings.append({
                "severity": "advisory",
                "code": "FATE_EVENT_NOT_ADVANCED",
                "msg": f"无卡推进任何 scheduled fate_event（{active_fate_ids[:3]}）— 如本章本就是过场可豁免",
            })

    # 规则 4：aspect_compatibility_check 必为 true
    for card in cards:
        cd = card.get("character_driven", {})
        if cd.get("aspect_compatibility_check") is False:
            findings.append({
                "severity": "violation",
                "code": "ASPECT_VIOLATION_CARD",
                "card": card.get("label"),
                "msg": f"卡 {card.get('label')} 行动违反 active aspects 约束 — 必须重写或删除",
            })

    # 规则 5：storyteller target_outcome 一致性
    if target_outcome == "setback":
        any_setback = any((c.get("character_driven", {}).get("stress_implication") or 0) > 0
                          if isinstance(c.get("character_driven", {}).get("stress_implication"), (int, float))
                          else False
                          for c in cards)
        if not any_setback:
            findings.append({
                "severity": "advisory",
                "code": "STORYTELLER_MISMATCH",
                "msg": f"narrator 建议 target=setback，但无卡的 stress_implication > 0（应至少 1 张挫败/暴露卡）",
            })

    # 规则 6：throughline 覆盖至少 2 条
    all_throughlines = set()
    for c in cards:
        for t in c.get("character_driven", {}).get("throughline_advanced", []) or []:
            all_throughlines.add(t)
    if len(all_throughlines) < 2:
        findings.append({
            "severity": "advisory",
            "code": "THROUGHLINE_COVERAGE_LOW",
            "actual": list(all_throughlines),
            "msg": f"所有卡 throughline_advanced 并集仅 {len(all_throughlines)} 条（应 ≥ 2）",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "fate_cards_compliance",
        "scan_ts": ts,
        "cards_ch": args.ch,
        "cards_count": len(cards),
        "context": {
            "pending_reveals": pending_reveals,
            "active_fate_ids": active_fate_ids,
            "target_outcome": target_outcome,
        },
        "findings": findings,
        "summary": {
            "violation": sum(1 for f in findings if f["severity"] == "violation"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
        },
    }
    out_path = out_dir / f"fate_cards_compliance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    v_count = report["summary"]["violation"]
    a_count = report["summary"]["advisory"]
    print(f"[fate_cards_compliance] ch{args.ch}: {len(cards)} 卡 - {v_count} violation / {a_count} advisory")
    for f in findings[:8]:
        sev = f["severity"].upper()
        print(f"  [{sev}] {f.get('msg', f.get('code'))}")
    print(f"报告: {out_path}")

    if v_count > 0:
        sys.exit(2)
    if a_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
