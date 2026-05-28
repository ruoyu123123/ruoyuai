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

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


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
            if b_card in text_dump:
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
    if total_evals < 3:
        return []
    pos_dist = {p: round(positions[p] / total_evals, 2) for p in ["controlled", "risky", "desperate"]}
    eff_dist = {e: round(effects[e] / total_evals, 2) for e in ["great", "standard", "limited"]}

    if pos_dist.get("controlled", 0) > 0.85:
        findings.append({
            "severity": "advisory",
            "code": "POSITION_TOO_SAFE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['controlled']:.0%} 是 controlled → 主角永远稳，叙事张力低",
        })
    if pos_dist.get("desperate", 0) > 0.6:
        findings.append({
            "severity": "warning",
            "code": "POSITION_TOO_DESPERATE",
            "distribution": pos_dist,
            "suggestion": f"position 中 {pos_dist['desperate']:.0%} 是 desperate → 虐过头，读者疲劳",
        })
    if eff_dist.get("great", 0) > 0.7:
        findings.append({
            "severity": "advisory",
            "code": "EFFECT_TOO_GREAT",
            "distribution": eff_dist,
            "suggestion": f"effect 中 {eff_dist['great']:.0%} 是 great → 无失败感，无成长压力",
        })
    if eff_dist.get("limited", 0) > 0.5:
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
