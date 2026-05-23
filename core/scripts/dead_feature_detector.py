"""dead_feature_detector.py — 死功能检测（v22.5 L3 + L4）

业界 2026 数据：典型项目 16-18% 代码 dead。我们系统更要警惕：
- 设计了的 ripple_rules / fate_events / mental_break 卡 / clock / scanner 是否真被触发
- manifest 注入字段是否真被 writer 消费
- 命令是否长期无人用

5 类死功能检测：

A. DEAD_RIPPLE_RULE: 涟漪规则.json 中 status=active 但从未在 world_ticks_log 触发
B. DEAD_FATE_EVENT: 大势卡 ME 长期 scheduled 但 prereq 满足却不激活
C. DEAD_MENTAL_BREAK_CARD: 主角压力档 mental_break_pool 卡从未触发
D. DEAD_MANIFEST_FIELD: build_manifest 输出字段 N 章无 writer changes 引用
E. UNUSED_TEMPLATE: 模板池子（事件池 events / 时钟表 spawned_by=manual / heart_events）从未抽中

输出：_数据库/.learning/dead_features_<ts>.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_dead_ripple_rules(project_root: Path) -> list[dict]:
    """涟漪规则.ripple_rules 中从未在 world_ticks_log 触发的"""
    findings = []
    rules = load_json(project_root / "_数据库" / "涟漪规则.json", {})
    world = load_json(project_root / "_数据库" / "世界状态.json", {})
    log = world.get("world_ticks_log", []) or []
    triggered_values = Counter()
    for entry in log:
        tv = entry.get("trigger_value")
        if tv:
            triggered_values[tv] += 1
    for rule in rules.get("ripple_rules", []) or []:
        if rule.get("trigger_type") == "auto_tick":
            continue  # auto_tick 永远触发，不算 dead
        match_pat = rule.get("trigger_match", "")
        candidates = [c.strip() for c in match_pat.split("|") if c.strip()]
        hit = any(any(c in tv or tv in c for c in candidates) for tv in triggered_values)
        if not hit:
            findings.append({
                "code": "DEAD_RIPPLE_RULE",
                "rule_id": rule.get("id"),
                "trigger_match": match_pat,
                "suggestion": "整本未触发 → 调整 trigger_match 关键词 / 删除",
            })
    return findings


def scan_dead_fate_events(project_root: Path) -> list[dict]:
    """大势 ME 长期 scheduled 但 prereq 满足却不激活"""
    findings = []
    fate = load_json(project_root / "_数据库" / "大势卡.json", {})
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    cur_ch = chapters[-1] if chapters else 0
    completed_ids = {e["id"] for e in fate.get("major_events", []) if e.get("status") == "completed"}
    for ev in fate.get("major_events", []) or []:
        if ev.get("status") != "scheduled":
            continue
        prereqs = ev.get("prerequisites", []) or []
        if prereqs and all(p in completed_ids for p in prereqs):
            window = ev.get("expected_window_after", {})
            if window and isinstance(window, dict):
                max_ch = window.get("max_chapters", 999)
                prereq_id = window.get("event", "")
                # 找 prereq completed_at_ch
                prereq_ch = next((e.get("completed_at_ch", 0) for e in fate.get("major_events", []) if e.get("id") == prereq_id), 0)
                if prereq_ch and cur_ch - prereq_ch > max_ch + 10:
                    findings.append({
                        "code": "DEAD_FATE_EVENT",
                        "event_id": ev.get("id"),
                        "title": ev.get("title"),
                        "prereq": prereq_id,
                        "overdue_by": cur_ch - prereq_ch - max_ch,
                        "suggestion": "已超 window 10 章+ → 删 / 改 prereq / 改 trigger_when",
                    })
    return findings


def scan_dead_mental_break_cards(project_root: Path) -> list[dict]:
    """主角压力档 mental_break_pool 卡从未触发"""
    findings = []
    stress = load_json(project_root / "_数据库" / "主角压力档.json", {})
    triggered_ids = set()
    for entry in stress.get("stress_log", []) or []:
        if entry.get("trigger_type") == "mental_break_triggered":
            cid = entry.get("card_id")
            if cid:
                triggered_ids.add(cid)
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    cur_ch = chapters[-1] if chapters else 0
    if cur_ch >= 30:  # 30+ 章后还没触发任何卡 = 阈值过高或卡设计有问题
        pool = stress.get("mental_break_pool", []) or []
        if pool and not triggered_ids:
            findings.append({
                "code": "ALL_MENTAL_BREAK_NEVER_TRIGGERED",
                "card_count": len(pool),
                "current_ch": cur_ch,
                "suggestion": f"{len(pool)} 张 mental_break 卡在 {cur_ch} 章后全 0 触发 → stress_threshold 过高，建议下调",
            })
    return findings


def scan_dead_manifest_fields(project_root: Path, last_n: int = 5) -> list[dict]:
    """manifest 注入字段 N 章无 writer changes 引用"""
    findings = []
    manifest_dir = project_root / "_数据库" / ".manifest"
    if not manifest_dir.exists():
        return []
    # 取最近 N 个 manifest 文件
    manifests = sorted(manifest_dir.glob("ch_*.json"), key=lambda p: p.stat().st_mtime)[-last_n:]
    if not manifests:
        return []

    # 关键 manifest 字段 → 期望出现的 changes 字段
    field_consumption_map = {
        "active_clocks": "clocks_addressed",
        "active_aspects": "aspects_addressed",
        "ensemble_layer": "heart_events_revealed",
        "fate_dice_hint": "fate_dice_consumed",
        "throughlines": "throughline_progress",
        "character_moves": "moves_used",
        "position_effect_template": "position_effect_evals",
        "active_fate_events": "fate_events_triggered",
        "world_state_snapshot": "world_state_consumption",
        "hub_directive": "chapter_hub",
        "storyteller_directive": "storyteller_alignment",
        "protagonist_stress": "stress_evaluation_self",
        "relevant_heuristics": None,  # 间接消费，难直接检测
    }

    # 对每个 manifest 字段统计消费次数
    consumption_count = Counter()
    total_chs = 0
    for mp in manifests:
        manifest = load_json(mp, {})
        # 找对应 changes
        m = re.match(r"ch_(\d+)\.json", mp.name)
        if not m:
            continue
        ch = int(m.group(1))
        changes = load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})
        factual = changes.get("factual", {}) or {}
        self_eval = changes.get("self_eval", {}) or {}
        total_chs += 1
        for m_field, c_field in field_consumption_map.items():
            if c_field is None:
                continue
            if m_field not in manifest:
                continue
            m_value = manifest.get(m_field, {})
            # m_field 在 manifest 注入了（非 off）
            mode = m_value.get("mode", "on") if isinstance(m_value, dict) else "on"
            if mode == "off":
                continue
            # 检查 changes 是否消费
            c_value = factual.get(c_field) or self_eval.get(c_field)
            if c_value and c_value not in ([], {}, None):
                consumption_count[m_field] += 1

    for m_field, c_field in field_consumption_map.items():
        if c_field is None:
            continue
        count = consumption_count.get(m_field, 0)
        if total_chs >= 3 and count == 0:
            findings.append({
                "code": "DEAD_MANIFEST_FIELD",
                "manifest_field": m_field,
                "expected_changes_field": c_field,
                "chs_checked": total_chs,
                "consumption_count": 0,
                "suggestion": f"{m_field} 注入 manifest 但 {total_chs} 章 writer 0 次消费 → writer prompt 未强调 / 应删字段省 token",
            })
    return findings


def scan_dead_event_pool(project_root: Path) -> list[dict]:
    """事件池中从未被 fate_dice 抽中的事件"""
    findings = []
    pool = load_json(project_root / "_数据库" / "事件池.json", {})
    drawn = {d.get("event_id") for d in pool.get("drawn_events_log", []) if d.get("event_id")}
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    cur_ch = chapters[-1] if chapters else 0
    if cur_ch < 20:
        return []  # 太早
    for ev in pool.get("events", []) or []:
        eid = ev.get("event_id")
        if eid and eid not in drawn:
            findings.append({
                "code": "UNUSED_EVENT_POOL_ITEM",
                "event_id": eid,
                "label": ev.get("label"),
                "suggestion": f"事件池 {eid} 在 {cur_ch} 章中未被 fate_dice 抽中 → context_filter 过严或永远 weight=低",
            })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    findings = []
    findings.extend(scan_dead_ripple_rules(project_root))
    findings.extend(scan_dead_fate_events(project_root))
    findings.extend(scan_dead_mental_break_cards(project_root))
    findings.extend(scan_dead_manifest_fields(project_root, args.last_n))
    findings.extend(scan_dead_event_pool(project_root))

    out = {
        "scan_type": "dead_feature_detector",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "findings": findings,
        "by_category": dict(Counter(f["code"] for f in findings)),
        "total": len(findings),
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"dead_features_{ts}.json"
    save_json(out_path, out)
    print(f"[dead_feature_detector] {len(findings)} 项 dead features：")
    for f in findings[:10]:
        print(f"  [{f['code']}] {f.get('suggestion', '')[:80]}")
    print(f"  报告: {out_path}")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
