"""cross_cluster_character_dynamics_aggregate.py — 角色行为动态扫（CCR5+7+8）

合并 3 类角色行为分布问题：

A. STRESS_TREND — 主角 stress 累计趋势
   - STRESS_RUNAWAY：连续 ≥ 4 章 stress 单调上涨
   - STRESS_PERMA_HIGH：≥ 5 章 stress 持续 ≥ 75% threshold 但未触发 break（卡顿）
   - COPING_NEVER_TRIGGERED：高 stress 章中 coping_behaviors 从未在正文出现
   - MENTAL_BREAK_EFFECTS_FORGOTTEN：触发 break 后 ≥ 3 章无 permanent_changes 体现

B. MOVES_USAGE — 角色 moves 使用分布
   - MOVE_OVERUSE：某 move 单章计数 > frequency_per_chapter
   - MOVE_NEVER_USED：core 角色某 move 在近 N 章 0 次（角色失声）
   - CHARACTER_VOICELESS：core 角色全部 moves 0 次（完全失声）

C. POSITION_EFFECT_DISTRIBUTION — Blades 双轴判定分布
   - POSITION_TOO_SAFE：position 全 controlled（主角永远稳）
   - POSITION_TOO_DESPERATE：≥ 60% desperate（虐到读者掉书）
   - EFFECT_TOO_GREAT：≥ 70% great（无失败感）
   - EFFECT_TOO_LIMITED：≥ 50% limited（始终半推半就）

退出码: 0 健康 / 1 advisory / 2 warning
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
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

_cd_sys = __import__("sys")
_cd_sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：账本驱动 stress/moves/position


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _rec_self_eval_field(rec: dict, field: str):
    """2026-05-29 复审修复 [M8]：moves_used / position_effect_evals / ending_type / applied_style
    的权威来源是 changes.self_eval（SC-4）。账本 ChapterRecord 多为扁平字段，但 builder 若把
    self_eval 整段嵌进 rec（或派生扁平失败）就会让原来的 rec.get("moves_used") 变死代码取空。
    这里：先取扁平 key，缺则回退 rec["self_eval"][field]，再缺回退 None。零回归（扁平命中即返回）。
    """
    if not isinstance(rec, dict):
        return None
    v = rec.get(field)
    if v not in (None, [], {}, ""):
        return v
    se = rec.get("self_eval")
    if isinstance(se, dict):
        sv = se.get(field)
        if sv not in (None, [], {}, ""):
            return sv
    return v


def _rec_has_self_eval_field(rec: dict, field: str) -> bool:
    val = _rec_self_eval_field(rec, field)
    return val not in (None, [], {}, "")


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


def read_changes(project_root: Path, ch: int) -> dict:
    return load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})


def read_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- A. STRESS_TREND ----------

def scan_stress_trend(project_root: Path, chapters: list[int]) -> list[dict]:
    stress_path = project_root / "_数据库" / "主角压力档.json"
    if not stress_path.exists():
        return []
    stress = load_json(stress_path, {})
    log = stress.get("stress_log", [])
    if not log:
        return []
    threshold = stress.get("stress_threshold_break", 8)
    high_pct = threshold * 0.75
    coping = stress.get("coping_mechanisms", {}).get("high_stress_behaviors", []) or []

    findings = []
    # 按 ch 取 last_n 内的 entry，并按 ch 唯一化（同 ch 多 entry 取最新 new_total）
    by_ch = {}
    for e in log:
        ch = e.get("ch")
        if ch in chapters and e.get("trigger_type") != "mental_break_triggered":
            by_ch[ch] = e.get("new_total", 0)
    sorted_ch_stress = sorted(by_ch.items())

    if len(sorted_ch_stress) < 2:
        return []

    # STRESS_RUNAWAY
    runaway_streak = 0
    for i in range(1, len(sorted_ch_stress)):
        if sorted_ch_stress[i][1] > sorted_ch_stress[i - 1][1]:
            runaway_streak += 1
            if runaway_streak >= 3:  # 连续 3 章上涨 = 4 个数据点
                findings.append({
                    "severity": "advisory",
                    "code": "STRESS_RUNAWAY",
                    "consecutive_chs": [sorted_ch_stress[j][0] for j in range(i - 3, i + 1)],
                    "stress_trail": [sorted_ch_stress[j][1] for j in range(i - 3, i + 1)],
                    "suggestion": f"主角 stress 连续 {runaway_streak+1} 章单调上涨 → 应有 coping/relief 章节插入",
                })
                runaway_streak = 0
        else:
            runaway_streak = 0

    # STRESS_PERMA_HIGH
    high_chs = [ch for ch, s in sorted_ch_stress if s >= high_pct]
    if len(high_chs) >= 5:
        # 检查是否有 mental_break 触发
        breaks = [e for e in log if e.get("trigger_type") == "mental_break_triggered" and e.get("ch") in chapters]
        if not breaks:
            findings.append({
                "severity": "warning",
                "code": "STRESS_PERMA_HIGH_NO_BREAK",
                "high_stress_chs": high_chs,
                "suggestion": f"主角 stress 在 {len(high_chs)} 章持续 ≥ 75% threshold 但从未触发 mental_break → 检查 stress_threshold_break 是否过高 / 触发逻辑是否漏",
            })

    # COPING_NEVER_TRIGGERED
    if high_chs and coping:
        coping_kws = []
        for c in coping:
            coping_kws.extend(re.findall(r"[一-鿿]{2,4}", c)[:2])
        coping_hit_chs = []
        for ch in high_chs:
            text = read_text(project_root, ch)
            if any(kw in text for kw in coping_kws):
                coping_hit_chs.append(ch)
        if not coping_hit_chs and len(high_chs) >= 3:
            findings.append({
                "severity": "advisory",
                "code": "COPING_NEVER_TRIGGERED",
                "high_stress_chs_unaddressed": high_chs,
                "coping_options": coping[:3],
                "suggestion": f"高 stress 章节（{len(high_chs)} 章）从未在正文带入任何 coping 行为 → writer 漏消费",
            })

    # MENTAL_BREAK_EFFECTS_FORGOTTEN
    breaks = [e for e in log if e.get("trigger_type") == "mental_break_triggered" and e.get("ch") in chapters]
    for b in breaks:
        b_ch = b.get("ch")
        b_card = b.get("card_id", "")
        post_chs = [c for c in chapters if c > b_ch][:5]
        if len(post_chs) < 3:
            continue
        # 简单检测：post chapters 是否含 break card 的关键词
        # 这里用 card_id 字符串匹配 _changes.json.factual.locked_facts
        any_hit = False
        for ch in post_chs:
            changes = read_changes(project_root, ch)
            text_dump = json.dumps(changes, ensure_ascii=False)
            # 2026-06 修复：b_card 缺失/空串时 `'' in text_dump` 恒 True → any_hit 恒 True →
            # MENTAL_BREAK_FORGOTTEN 永久静默（假阴性）。加真值守卫，与账本版 L416 对齐。
            if b_card and b_card in text_dump:
                any_hit = True
                break
        if not any_hit:
            findings.append({
                "severity": "warning",
                "code": "MENTAL_BREAK_FORGOTTEN",
                "break_at_ch": b_ch,
                "card_id": b_card,
                "post_chs": post_chs,
                "suggestion": f"ch{b_ch} 触发 mental_break {b_card}，但后续 {len(post_chs)} 章无任何 _changes 引用该 card → permanent_changes 没落实",
            })
    return findings


# ---------- B. MOVES_USAGE ----------

def scan_moves_usage(project_root: Path, chapters: list[int]) -> list[dict]:
    moves_path = project_root / "_数据库" / "角色行动表.json"
    if not moves_path.exists():
        return []
    moves_data = load_json(moves_path, {})
    chars = moves_data.get("characters", {}) or {}
    if not chars:
        return []

    # 收集每章 _changes.json.self_eval.moves_used
    per_chapter_moves = {}  # ch -> [{character, move_id, instances}]
    for ch in chapters:
        changes = read_changes(project_root, ch)
        moves_used = (changes.get("self_eval", {}) or {}).get("moves_used", []) or []
        per_chapter_moves[ch] = moves_used

    findings = []
    # 每个 core 角色统计
    for char_name, char_data in chars.items():
        moves_def = char_data.get("moves", []) or []
        if not moves_def:
            continue
        per_move_total = Counter()
        per_move_per_ch = defaultdict(lambda: defaultdict(int))
        for ch, used_list in per_chapter_moves.items():
            for u in used_list:
                if u.get("character") != char_name:
                    continue
                mid = u.get("move_id")
                inst = u.get("instances", 1)
                per_move_total[mid] += inst
                per_move_per_ch[mid][ch] = inst

        # MOVE_OVERUSE
        for m in moves_def:
            mid = m.get("move_id")
            freq_cap = m.get("frequency_per_chapter", 99)
            for ch, inst in per_move_per_ch[mid].items():
                if inst > freq_cap:
                    findings.append({
                        "severity": "advisory",
                        "code": "MOVE_OVERUSE",
                        "character": char_name,
                        "move_id": mid,
                        "ch": ch,
                        "instances": inst,
                        "limit": freq_cap,
                        "suggestion": f"{char_name} move {mid} 在 ch{ch} 用了 {inst} 次（上限 {freq_cap}）→ 公式化",
                    })

        # MOVE_NEVER_USED：moves 全部 0 次（且角色在 chapters 范围内出现过）
        moves_zero = [m.get("move_id") for m in moves_def if per_move_total[m.get("move_id")] == 0]
        # 角色是否真出场？
        char_appeared_chs = []
        for ch in chapters:
            if char_name in read_text(project_root, ch):
                char_appeared_chs.append(ch)
        # CHARACTER_VOICELESS
        if char_appeared_chs and not per_move_total and len(char_appeared_chs) >= 3:
            findings.append({
                "severity": "warning",
                "code": "CHARACTER_VOICELESS",
                "character": char_name,
                "appearances": char_appeared_chs,
                "moves_count": len(moves_def),
                "suggestion": f"{char_name} 在 {len(char_appeared_chs)} 章出场但 moves_used 全 0 → writer 完全没消费 moves 系统",
            })
        elif moves_zero and len(moves_zero) >= max(3, len(moves_def) // 2) and len(char_appeared_chs) >= 5:
            findings.append({
                "severity": "advisory",
                "code": "MOVES_UNDERUSED",
                "character": char_name,
                "never_used_moves": moves_zero[:5],
                "suggestion": f"{char_name} 有 {len(moves_zero)}/{len(moves_def)} moves 在近 {len(chapters)} 章 0 次使用 → 角色行为单一化",
            })
    return findings


# ---------- C. POSITION_EFFECT_DISTRIBUTION ----------

def scan_position_effect(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    positions = Counter()
    effects = Counter()
    total_evals = 0
    total_effects = 0
    for ch in chapters:
        changes = read_changes(project_root, ch)
        evals = (changes.get("self_eval", {}) or {}).get("position_effect_evals", []) or []
        for e in evals:
            p = e.get("evaluated_position")
            ef = e.get("evaluated_effect")
            if p:
                positions[p] += 1
                total_evals += 1
            if ef:
                effects[ef] += 1
                total_effects += 1
    # 2026-06 修复：position / effect 是两个独立分布，须各用自己的分母（只有 effect 无
    # position 的 eval 是合法可达条目 → 复用 total_evals 会让 eff_dist 百分比 >100% 虚报）。
    if total_evals < 3 and total_effects < 3:
        return []
    pos_dist = {p: round(positions[p] / total_evals, 2) for p in ["controlled", "risky", "desperate"]} if total_evals else {}
    eff_dist = {e: round(effects[e] / total_effects, 2) for e in ["great", "standard", "limited"]} if total_effects else {}

    if total_evals >= 3 and pos_dist.get("controlled", 0) > 0.85:
        findings.append({
            "severity": "advisory",
            "code": "POSITION_TOO_SAFE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['controlled']:.0%} 是 controlled → 主角永远稳，叙事张力低",
        })
    if total_evals >= 3 and pos_dist.get("desperate", 0) > 0.6:
        findings.append({
            "severity": "warning",
            "code": "POSITION_TOO_DESPERATE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['desperate']:.0%} 是 desperate → 虐过头，读者疲劳",
        })
    if total_effects >= 3 and eff_dist.get("great", 0) > 0.7:
        findings.append({
            "severity": "advisory",
            "code": "EFFECT_TOO_GREAT",
            "distribution": eff_dist,
            "suggestion": f"effect 中 {eff_dist['great']:.0%} 是 great → 无失败感，无成长压力",
        })
    if total_effects >= 3 and eff_dist.get("limited", 0) > 0.5:
        findings.append({
            "severity": "advisory",
            "code": "EFFECT_TOO_LIMITED",
            "distribution": eff_dist,
            "suggestion": f"effect 中 {eff_dist['limited']:.0%} 是 limited → 始终半推半就，主角不主动",
        })
    return findings


# ============================================================
# 2026-05-29 cluster 化：账本驱动分支
# CLUSTER_MODE=1 且账本含 stress_total/moves_used/position_effect_evals →
# 从 ChapterRecord 取预算字段。趋势检测「连续 N 章」逻辑逐字保留，数据点来自账本。
# - stress: stress_total(逐章) + mental_break_card(标记 break) + coping_hit(bool)
# - moves: moves_used(逐章) + char_mention_counts(出场判定，取代正文 grep)
# - position: position_effect_evals(逐章；兼容 position/effect 与 evaluated_* 两种键)
# 仍读 主角压力档.json 取 threshold/coping 定义、角色行动表.json 取 moves 定义。
# --last-n 在 cluster 模式 = 最后 N 个 cluster。账本缺字段 → 回退逐章逻辑（零回归）。
# ============================================================

def scan_stress_trend_ledger(project_root: Path, recs) -> list[dict]:
    """与 scan_stress_trend 同构，stress 序列来自账本 stress_total。"""
    stress = load_json(project_root / "_数据库" / "主角压力档.json", {}) or {}
    threshold = stress.get("stress_threshold_break", 8)
    high_pct = threshold * 0.75
    # 2026-06 修复：与 disk 版 scan_stress_trend L157 `if high_chs and coping:` 对齐——
    # coping 子系统未定义（新书 skeleton 默认 coping_mechanisms: {}）时不报 COPING_NEVER_TRIGGERED，
    # 否则把『作者没启用可选 coping 子系统』误报成『writer 漏消费』(违北极星⑤)。
    coping_defined = bool(stress.get("coping_mechanisms", {}).get("high_stress_behaviors"))

    findings = []
    by_ch = {}
    break_chs = []  # (ch, card)
    coping_chs = set()
    for ch, rec in recs:
        st = rec.get("stress_total")
        if isinstance(st, (int, float)):
            by_ch[ch] = st
        card = rec.get("mental_break_card")
        if card:
            break_chs.append((ch, card))
        if rec.get("coping_hit"):
            coping_chs.add(ch)
    sorted_ch_stress = sorted(by_ch.items())

    if len(sorted_ch_stress) < 2:
        return []

    # STRESS_RUNAWAY
    runaway_streak = 0
    for i in range(1, len(sorted_ch_stress)):
        if sorted_ch_stress[i][1] > sorted_ch_stress[i - 1][1]:
            runaway_streak += 1
            if runaway_streak >= 3:
                findings.append({
                    "severity": "advisory",
                    "code": "STRESS_RUNAWAY",
                    "consecutive_chs": [sorted_ch_stress[j][0] for j in range(i - 3, i + 1)],
                    "stress_trail": [sorted_ch_stress[j][1] for j in range(i - 3, i + 1)],
                    "suggestion": f"主角 stress 连续 {runaway_streak+1} 章单调上涨 → 应有 coping/relief 章节插入",
                })
                runaway_streak = 0
        else:
            runaway_streak = 0

    # STRESS_PERMA_HIGH_NO_BREAK
    high_chs = [ch for ch, s in sorted_ch_stress if s >= high_pct]
    if len(high_chs) >= 5 and not break_chs:
        findings.append({
            "severity": "warning",
            "code": "STRESS_PERMA_HIGH_NO_BREAK",
            "high_stress_chs": high_chs,
            "suggestion": f"主角 stress 在 {len(high_chs)} 章持续 ≥ 75% threshold 但从未触发 mental_break → 检查 stress_threshold_break 是否过高 / 触发逻辑是否漏",
        })

    # COPING_NEVER_TRIGGERED：高 stress 章中 coping_hit 全 False
    if coping_defined and len(high_chs) >= 3 and not (set(high_chs) & coping_chs):
        findings.append({
            "severity": "advisory",
            "code": "COPING_NEVER_TRIGGERED",
            "high_stress_chs_unaddressed": high_chs,
            "suggestion": f"高 stress 章节（{len(high_chs)} 章）从未在正文带入任何 coping 行为 → writer 漏消费",
        })

    # MENTAL_BREAK_EFFECTS_FORGOTTEN：break 后 ≥ 3 章无后续章 stress_trigger/card 引用
    all_chs = [c for c, _ in sorted_ch_stress]
    trigger_dump = {c: (r.get("stress_trigger") or "") for c, r in recs}
    for b_ch, b_card in break_chs:
        post_chs = [c for c in all_chs if c > b_ch][:5]
        if len(post_chs) < 3:
            continue
        any_hit = any(b_card and b_card in (trigger_dump.get(c) or "") for c in post_chs)
        if not any_hit:
            findings.append({
                "severity": "warning",
                "code": "MENTAL_BREAK_FORGOTTEN",
                "break_at_ch": b_ch,
                "card_id": b_card,
                "post_chs": post_chs,
                "suggestion": f"ch{b_ch} 触发 mental_break {b_card}，但后续 {len(post_chs)} 章无任何记录引用该 card → permanent_changes 没落实",
            })
    return findings


def scan_moves_usage_ledger(project_root: Path, recs) -> list[dict]:
    """与 scan_moves_usage 同构，moves_used + 出场判定来自账本。"""
    moves_data = load_json(project_root / "_数据库" / "角色行动表.json", {}) or {}
    chars = moves_data.get("characters", {}) or {}
    if not chars:
        return []

    per_chapter_moves = {}     # ch -> moves_used list
    per_chapter_appear = {}    # ch -> char_mention_counts dict
    chapters = []
    for ch, rec in recs:
        chapters.append(ch)
        # 2026-05-29 复审修复 [M8]：moves_used 权威来源 self_eval（SC-4），扁平缺失则回退。
        per_chapter_moves[ch] = _rec_self_eval_field(rec, "moves_used") or []
        per_chapter_appear[ch] = rec.get("char_mention_counts", {}) or {}

    findings = []
    for char_name, char_data in chars.items():
        moves_def = char_data.get("moves", []) or []
        if not moves_def:
            continue
        per_move_total = Counter()
        per_move_per_ch = defaultdict(lambda: defaultdict(int))
        for ch, used_list in per_chapter_moves.items():
            for u in used_list:
                if u.get("character") != char_name:
                    continue
                mid = u.get("move_id")
                inst = u.get("instances", 1)
                per_move_total[mid] += inst
                per_move_per_ch[mid][ch] = inst

        # MOVE_OVERUSE
        for m in moves_def:
            mid = m.get("move_id")
            freq_cap = m.get("frequency_per_chapter", 99)
            for ch, inst in per_move_per_ch[mid].items():
                if inst > freq_cap:
                    findings.append({
                        "severity": "advisory",
                        "code": "MOVE_OVERUSE",
                        "character": char_name,
                        "move_id": mid,
                        "ch": ch,
                        "instances": inst,
                        "limit": freq_cap,
                        "suggestion": f"{char_name} move {mid} 在 ch{ch} 用了 {inst} 次（上限 {freq_cap}）→ 公式化",
                    })

        moves_zero = [m.get("move_id") for m in moves_def if per_move_total[m.get("move_id")] == 0]
        # 出场判定：char_mention_counts 命中 > 0（取代正文 grep）
        char_appeared_chs = [ch for ch in chapters if (per_chapter_appear.get(ch, {}).get(char_name, 0) or 0) > 0]
        # CHARACTER_VOICELESS
        if char_appeared_chs and not per_move_total and len(char_appeared_chs) >= 3:
            findings.append({
                "severity": "warning",
                "code": "CHARACTER_VOICELESS",
                "character": char_name,
                "appearances": char_appeared_chs,
                "moves_count": len(moves_def),
                "suggestion": f"{char_name} 在 {len(char_appeared_chs)} 章出场但 moves_used 全 0 → writer 完全没消费 moves 系统",
            })
        elif moves_zero and len(moves_zero) >= max(3, len(moves_def) // 2) and len(char_appeared_chs) >= 5:
            findings.append({
                "severity": "advisory",
                "code": "MOVES_UNDERUSED",
                "character": char_name,
                "never_used_moves": moves_zero[:5],
                "suggestion": f"{char_name} 有 {len(moves_zero)}/{len(moves_def)} moves 在近 {len(chapters)} 章 0 次使用 → 角色行为单一化",
            })
    return findings


def scan_position_effect_ledger(recs) -> list[dict]:
    """与 scan_position_effect 同构，position_effect_evals 来自账本（兼容两种键名）。"""
    findings = []
    positions = Counter()
    effects = Counter()
    total_evals = 0
    total_effects = 0
    for _ch, rec in recs:
        # 2026-05-29 复审修复 [M8]：position_effect_evals 权威来源 self_eval（SC-4）。
        evals = _rec_self_eval_field(rec, "position_effect_evals") or []
        for e in evals:
            if not isinstance(e, dict):
                continue
            p = e.get("position") or e.get("evaluated_position")
            ef = e.get("effect") or e.get("evaluated_effect")
            if p:
                positions[p] += 1
                total_evals += 1
            if ef:
                effects[ef] += 1
                total_effects += 1
    # 2026-06 修复：与 disk 版 scan_position_effect 同步——position / effect 是两个独立分布，
    # 各用自己的分母（只有 effect 无 position 的 eval 合法可达 → 复用 total_evals 会虚报）。
    if total_evals < 3 and total_effects < 3:
        return []
    pos_dist = {p: round(positions[p] / total_evals, 2) for p in ["controlled", "risky", "desperate"]} if total_evals else {}
    eff_dist = {e: round(effects[e] / total_effects, 2) for e in ["great", "standard", "limited"]} if total_effects else {}

    if total_evals >= 3 and pos_dist.get("controlled", 0) > 0.85:
        findings.append({
            "severity": "advisory",
            "code": "POSITION_TOO_SAFE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['controlled']:.0%} 是 controlled → 主角永远稳，叙事张力低",
        })
    if total_evals >= 3 and pos_dist.get("desperate", 0) > 0.6:
        findings.append({
            "severity": "warning",
            "code": "POSITION_TOO_DESPERATE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['desperate']:.0%} 是 desperate → 虐过头，读者疲劳",
        })
    if total_effects >= 3 and eff_dist.get("great", 0) > 0.7:
        findings.append({
            "severity": "advisory",
            "code": "EFFECT_TOO_GREAT",
            "distribution": eff_dist,
            "suggestion": f"effect 中 {eff_dist['great']:.0%} 是 great → 无失败感，无成长压力",
        })
    if total_effects >= 3 and eff_dist.get("limited", 0) > 0.5:
        findings.append({
            "severity": "advisory",
            "code": "EFFECT_TOO_LIMITED",
            "distribution": eff_dist,
            "suggestion": f"effect 中 {eff_dist['limited']:.0%} 是 limited → 始终半推半就，主角不主动",
        })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)

    # 2026-05-29 复审修复 [M8]：moves_used / position_effect_evals 可能嵌在账本 ChapterRecord
    # 的 self_eval 段（SC-4 权威来源），ledger_has_field 只看顶层 key 会漏 → 补一次 self_eval 探测。
    def _ledger_has_self_eval_field(field: str) -> bool:
        if csr.ledger_has_field(project_root, field):
            return True
        for _ch, _rec in csr.get_chapter_records(project_root):
            if _rec_has_self_eval_field(_rec, field):
                return True
        return False

    use_ledger = IS_CLUSTER_MODE and (
        csr.ledger_has_field(project_root, "stress_total")
        or _ledger_has_self_eval_field("moves_used")
        or _ledger_has_self_eval_field("position_effect_evals")
    )
    if use_ledger:
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        chapters = [ch for ch, _ in recs]
        if not chapters:
            print("[SKIP] 无账本章记录")
            sys.exit(0)
        findings = []
        findings.extend(scan_stress_trend_ledger(project_root, recs))
        findings.extend(scan_moves_usage_ledger(project_root, recs))
        findings.extend(scan_position_effect_ledger(recs))
    else:
        chapters = get_chapters(project_root, args.last_n)
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)

        findings = []
        findings.extend(scan_stress_trend(project_root, chapters))
        findings.extend(scan_moves_usage(project_root, chapters))
        findings.extend(scan_position_effect(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "character_dynamics",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"character_dynamics_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[character_dynamics] {summary['warning']} warning / {summary['advisory']} advisory")
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
