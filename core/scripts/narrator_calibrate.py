"""narrator_calibrate.py — Storyteller 风格 + Adaptation Factor 调压器（v21 R1.2 新增）

借鉴 RimWorld 的三种 Storyteller（Cassandra 升压 / Phoebe 长间歇 / Randy 随机）+ Adaptation Factor。

每章 save-state 末尾跑：
1. 读 _changes.json.self_eval 推断本章 outcome（win/setback/neutral）+ intensity
2. 写入 叙事节拍器.json.chapter_outcome_log
3. 滑窗 N 章计算 setback_count / win_streak / loss_streak
4. 对照 storyteller_profile 的 expected_setback_per_n_ch，决定下章 target_outcome:
   - 实际 setback < 预期 → 下章 target=setback（"该让主角吃亏了"）
   - 实际 setback > 预期 → 下章 target=win（"读者要喘息"）
   - 区间内 → target=auto
5. 输出更新后的 narrator_recommendation 字段

四种 phase 自动切换：
- 连续 3 章 win + intensity 累计 ≥ 12 → climax 触发后 cooldown
- cooldown 持续 ≥ 3 章 → steady
- steady 期累计 setback ≥ 2 → rising
- rising → 自动按 storyteller 节奏推进

用法：python narrator_calibrate.py <project> [--ch N] [--auto]
退出码: 0 健康 / 1 narrator 强烈建议下章修正 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：裸写 → 原子写（atomic_write_json 内部已 mkdir + fsync）
    atomic_json.atomic_write_json(p, data)


def infer_outcome_from_changes(changes: dict) -> tuple[str, int]:
    """从 _changes.json 推断 outcome + intensity（heuristic）。

    准则：
    - factual.fate_events_triggered 含 status=completed → win 偏向（除非是悲剧大事件）
    - factual.foreshadowing_paid 数量 ≥ 2 → win 偏向
    - factual.locked_facts 含负面关键词（死/失/被/暴露/受伤）→ setback
    - self_eval.judge_health_warnings 严重 → setback
    - 都没有 → neutral
    """
    factual = changes.get("factual", {}) or {}
    self_eval = changes.get("self_eval", {}) or {}

    fate_count = len(factual.get("fate_events_triggered", []) or [])
    foreshadow_paid = len(factual.get("foreshadowing_paid", []) or [])

    # 负面关键词扫
    negative_kw = ["失败", "受伤", "死亡", "暴露", "被发现", "被打", "败退", "崩溃", "失控", "受重创"]
    locked_text = json.dumps(factual.get("locked_facts") or factual.get("facts_locked") or [], ensure_ascii=False)  # 2026-05-30：writer 实产 facts_locked，双读兜底（否则 setback 信号恒空）
    relations_text = json.dumps(factual.get("relationships", []) or [], ensure_ascii=False)
    full_text = locked_text + " " + relations_text
    negative_hits = sum(1 for kw in negative_kw if kw in full_text)

    if negative_hits >= 2:
        return ("setback", min(8, 3 + negative_hits))
    if fate_count >= 1 or foreshadow_paid >= 2:
        return ("win", min(7, 3 + fate_count + foreshadow_paid))
    if foreshadow_paid >= 1 or fate_count >= 0:
        return ("neutral", 3)
    return ("neutral", 2)


def evaluate_phase(profile: str, log: list[dict], current_phase: str, since_change_ch: int, ch: int) -> tuple[str, int]:
    """评估 phase 切换。返回 (new_phase, since_change_ch)。"""
    recent = log[-5:]
    win_run = 0
    win_intensity_sum = 0
    setback_run = 0
    for entry in reversed(recent):
        if entry["outcome"] == "win":
            win_run += 1
            win_intensity_sum += entry.get("intensity", 0)
        else:
            break
    for entry in reversed(recent):
        if entry["outcome"] == "setback":
            setback_run += 1
        else:
            break

    chs_since = ch - since_change_ch + 1

    # rising → climax → cooldown → steady → rising
    if current_phase == "rising":
        if win_run >= 3 and win_intensity_sum >= 12:
            return ("climax", ch)
        return (current_phase, since_change_ch)
    if current_phase == "climax":
        # climax 持续 1-2 章后转 cooldown
        if chs_since >= 1:
            return ("cooldown", ch)
        return (current_phase, since_change_ch)
    if current_phase == "cooldown":
        if chs_since >= 3:
            return ("steady", ch)
        return (current_phase, since_change_ch)
    if current_phase == "steady":
        if setback_run >= 2 or chs_since >= 5:
            return ("rising", ch)
        return (current_phase, since_change_ch)
    return (current_phase, since_change_ch)


def calibrate(project_root: Path, ch: int) -> dict:
    pacer_path = project_root / "_数据库" / "叙事节拍器.json"
    pacer = load_json(pacer_path, None)
    if pacer is None:
        return {"error": "叙事节拍器.json 不存在"}

    # 读 _changes
    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    changes = load_json(changes_path, {})
    outcome, intensity = infer_outcome_from_changes(changes)

    # 写入 log（去重）
    log = pacer.setdefault("chapter_outcome_log", [])
    if any(e.get("ch") == ch for e in log):
        # 替换
        log = [e for e in log if e.get("ch") != ch]
    log.append({"ch": ch, "outcome": outcome, "intensity": intensity, "_note": "auto-inferred from _changes"})
    log.sort(key=lambda e: e.get("ch", 0))
    pacer["chapter_outcome_log"] = log

    # 更新 phase
    profile = pacer.get("storyteller_profile", "cassandra")
    cur_phase = pacer.get("current_pressure_phase", "rising")
    since_ch = pacer.get("since_phase_change_ch", 1)
    new_phase, new_since = evaluate_phase(profile, log, cur_phase, since_ch, ch)
    pacer["current_pressure_phase"] = new_phase
    pacer["since_phase_change_ch"] = new_since

    # 滑窗 adaptation_factor
    af = pacer.setdefault("adaptation_factor", {})
    n = af.get("recent_n_chapters", 10)
    expected = af.get("expected_setback_per_n_ch", 4)
    tol = af.get("tolerance_window", 2)
    window = log[-n:]
    setback_count = sum(1 for e in window if e["outcome"] == "setback")
    win_streak = 0
    loss_streak = 0
    for e in reversed(window):
        if e["outcome"] == "win":
            win_streak += 1
        else:
            break
    for e in reversed(window):
        if e["outcome"] == "setback":
            loss_streak += 1
        else:
            break
    af["current_setback_count_in_window"] = setback_count
    af["current_win_streak"] = win_streak
    af["current_loss_streak"] = loss_streak

    # 推荐下章 outcome
    rec = pacer.setdefault("narrator_recommendation", {})
    if setback_count + tol < expected:
        rec["next_chapter_target_outcome"] = "setback"
        rec["next_chapter_intensity_target"] = "high"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} < 期望{expected}-{tol}={expected-tol} → 该让主角吃亏"
        urgent = True
    elif setback_count - tol > expected:
        rec["next_chapter_target_outcome"] = "win"
        rec["next_chapter_intensity_target"] = "high"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} > 期望{expected}+{tol}={expected+tol} → 该给主角喘息"
        urgent = True
    else:
        rec["next_chapter_target_outcome"] = "auto"
        rec["next_chapter_intensity_target"] = "auto"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} 在期望区间 [{expected-tol}, {expected+tol}] 内 → 自由发挥"
        urgent = False

    save_json(pacer_path, pacer)

    return {
        "ch": ch,
        "outcome_inferred": outcome,
        "intensity": intensity,
        "phase_change": cur_phase != new_phase,
        "phase": new_phase,
        "adaptation_factor": af,
        "next_recommendation": rec,
        "_urgent": urgent,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not (project_root / "_数据库" / "叙事节拍器.json").exists():
        print("[SKIP] 叙事节拍器.json 不存在 — 项目未启用 storyteller 系统")
        sys.exit(0)

    ch = args.ch
    if ch is None or args.auto:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        ch = chapters[-1]

    r = calibrate(project_root, ch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(1 if r.get("_urgent") else 0)


if __name__ == "__main__":
    main()
