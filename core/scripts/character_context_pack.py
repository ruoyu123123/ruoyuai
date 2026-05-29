"""character_context_pack.py — 走向卡生成预消化上下文打包（v21 CD1 配套）

主代理在 spawn novel-outline-planner 之前调本脚本，把 12 项角色剧情数据浓缩成 1 份 markdown，
作为 agent 输入。免去 agent 自己 Read 12 个文件的 token + delay 开销。

读取：
  - character_arc_state.json
  - 主角压力档.json
  - 角色烙印.json
  - 群像档.json + .ensemble_pending_reveals.json
  - 大势卡.json
  - 时钟表.json
  - 叙事节拍器.json
  - 四线脉络.json
  - 角色池.json (propp_function)

输出：_数据库/.wal/第NNN+1章_planner_context.md（NNN+1 = 即将开写的章号）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int, help="即将生成走向卡的章号（即下一章）")
    args = ap.parse_args()

    project_root = Path(args.project)
    ch = args.ch
    db = project_root / "_数据库"

    out_lines = [
        f"# 走向卡生成上下文（章 {ch}）",
        f"",
        f"v21 CD1 — 角色剧情驱动数据预打包。outline-planner 必基于本上下文生成卡。",
        f"",
    ]

    # 1. character_arc_state
    arc = load_json(db / "character_arc_state.json", {})
    if arc.get("characters"):
        out_lines.append("## 1. 角色弧光（McKee/Truby v2.0）")
        out_lines.append("")
        for char_name, char_data in arc.get("characters", {}).items():
            out_lines.append(f"### {char_name}（current_stage: {char_data.get('current_stage_at_ch', '?')}）")
            out_lines.append(f"- **lie**：{char_data.get('lie', '-')}")
            out_lines.append(f"- **want / desire**：{char_data.get('want', '')} / {char_data.get('desire', '')}")
            out_lines.append(f"- **need / weakness_need**：{char_data.get('need', '')} / {char_data.get('weakness_need', '')}")
            ghost = char_data.get("ghost", {}) or {}
            if ghost:
                out_lines.append(f"- **ghost**：{ghost.get('core_ghost', '')}")
            ma = char_data.get("moral_argument", {}) or {}
            if ma:
                out_lines.append(f"- **moral_argument**：{ma.get('central_question', ma.get('_doc', ''))}")
            out_lines.append("")

    # 2. stress
    stress = load_json(db / "主角压力档.json", {})
    if stress:
        level = stress.get("stress_level", 0)
        threshold = stress.get("stress_threshold_break", 8)
        out_lines.append(f"## 2. 主角 Stress 状态")
        out_lines.append(f"- **当前**：{level}/{stress.get('stress_max', 10)}（threshold={threshold}）")
        out_lines.append(f"- **is_high**：{'是' if level >= threshold * 0.75 else '否'}")
        coping = stress.get("coping_mechanisms", {}).get("high_stress_behaviors", [])
        if coping and level >= threshold * 0.6:
            out_lines.append(f"- **建议 coping 行为**：{', '.join(coping[:3])}")
        # 检查是否触发过 mental_break
        for entry in reversed(stress.get("stress_log") or []):
            if entry.get("trigger_type") == "mental_break_triggered":
                out_lines.append(f"- **last_mental_break**：ch{entry['ch']} {entry.get('card_label')} ⚠️ 后续所有卡必尊重 card 永久效应")
                break
        out_lines.append("")

    # 3. aspects
    aspects = load_json(db / "角色烙印.json", {})
    if aspects:
        out_lines.append("## 3. Active Aspects（永久烙印）")
        out_lines.append("")
        any_active = False
        for char, char_d in (aspects.get("characters") or {}).items():
            actives = char_d.get("active_aspects") or []
            if actives:
                any_active = True
                out_lines.append(f"### {char}")
                for a in actives:
                    out_lines.append(f"- **{a.get('label', '?')}**：{', '.join(a.get('narrative_constraints', [])[:2])}")
        if not any_active:
            out_lines.append("（暂无 active aspects — 卡片 aspect_compatibility_check 默认 true）")
        out_lines.append("")

    # 4. pending heart events
    pending = load_json(db / ".ensemble_pending_reveals.json", {})
    if pending and pending.get("pending_reveals"):
        out_lines.append("## 4. Pending Heart Events（关系阈值已到，必触发）")
        out_lines.append("")
        for hr in pending.get("pending_reveals", []):
            out_lines.append(f"- **{hr.get('event_id')}** ({hr.get('npc')}, {hr.get('tier_label')})：{hr.get('reveal', '')[:80]}")
        out_lines.append("")
        out_lines.append("⚠️ **至少 1 张卡必触发上述 reveal 之一**")
        out_lines.append("")

    # 5. fate events
    fate = load_json(db / "大势卡.json", {})
    scheduled = [e for e in (fate.get("major_events") or []) if e.get("status") == "scheduled"][:5]
    if scheduled:
        out_lines.append("## 5. Active Fate Events（按 prerequisites 已可激活）")
        out_lines.append("")
        for e in scheduled:
            out_lines.append(f"- **{e.get('id')}** {e.get('title', '')}：{e.get('trigger_when', '')}")
        out_lines.append("")

    # 6. urgent clocks
    clocks = load_json(db / "时钟表.json", {})
    if clocks:
        urgent = []
        for c in clocks.get("clocks", []):
            if c.get("status") != "active":
                continue
            ticks = c.get("ticks", 0)
            max_v = c.get("max", 99)
            if max_v - ticks <= 2:
                urgent.append(c)
        if urgent:
            out_lines.append("## 6. Urgent Clocks（剩余 ≤ 2 章满格）")
            out_lines.append("")
            for c in urgent:
                out_lines.append(f"- **{c.get('clock_id')}** {c.get('label')}：{c.get('ticks')}/{c.get('max')} → {c.get('trigger_on_max')}")
            out_lines.append("")
            out_lines.append("⚠️ **至少 1 张卡应触发或显著推进**")
            out_lines.append("")

    # 7. storyteller
    pacer = load_json(db / "叙事节拍器.json", {})
    if pacer:
        rec = pacer.get("narrator_recommendation", {})
        out_lines.append(f"## 7. Storyteller 建议")
        out_lines.append(f"- **profile**：{pacer.get('storyteller_profile', '?')}")
        out_lines.append(f"- **current_phase**：{pacer.get('current_pressure_phase', '?')}")
        out_lines.append(f"- **next_target_outcome**：**{rec.get('next_chapter_target_outcome', 'auto')}** （reason: {rec.get('_reason', '')}）")
        out_lines.append("")

    # 8. throughlines
    th = load_json(db / "四线脉络.json", {})
    if th:
        out_lines.append("## 8. Throughlines 当前状态")
        out_lines.append("")
        for k, v in (th.get("throughlines") or {}).items():
            label = v.get("label", k)
            arc_str = v.get("current_arc") or v.get("current_stage") or ""
            progress = v.get("current_progress") or v.get("current_stage_ch") or ""
            out_lines.append(f"- **{label}**：{arc_str}（当前：{progress}）")
        out_lines.append("")
        out_lines.append("⚠️ **3 张卡的 throughline_advanced 并集应 ≥ 2 条**")
        out_lines.append("")

    # 9. propp functions
    pool = load_json(db / "角色池.json", {})
    if pool:
        out_lines.append("## 9. Propp 角色功能型（参考用）")
        out_lines.append("")
        # 2026-05-29 复审修复：L17 — core/emerged_characters 项可能是字符串（id）而非
        # dict（角色池.json 历史形态不一），对 str 调 .get 会 AttributeError。加 isinstance 守卫。
        for c in (pool.get("core_characters") or [])[:6]:
            if isinstance(c, dict):
                out_lines.append(f"- **{c.get('id')}**：{c.get('propp_function', '?')}")
            elif isinstance(c, str):
                out_lines.append(f"- **{c}**：?")
        for c in (pool.get("emerged_characters") or [])[:4]:
            if isinstance(c, dict):
                out_lines.append(f"- **{c.get('id')}**：{c.get('propp_function', '?')}")
            elif isinstance(c, str):
                out_lines.append(f"- **{c}**：?")
        out_lines.append("")

    # 10. final reminder
    out_lines.append("---")
    out_lines.append("")
    out_lines.append("## 卡片字段必填提醒")
    out_lines.append("")
    out_lines.append("每张卡必含 `character_driven` 9 字段（primary_character_arc_anchor / arc_dimension_tested / arc_state_change_hint / stress_implication / aspect_compatibility_check / heart_event_triggered / fate_event_advanced / clock_advanced / throughline_advanced）")
    out_lines.append("")
    out_lines.append("`based_on_state._v21_character_driven_state` 7 字段必填真实值。")

    out_path = db / ".wal" / f"第{ch:03d}章_planner_context.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(out_lines)
    out_path.write_text(content, encoding="utf-8")
    print(f"[OK] 走向卡生成上下文: {out_path}")
    print(f"行数: {len(out_lines)} / 预估 token: ~{len(content) // 3}")


if __name__ == "__main__":
    main()
