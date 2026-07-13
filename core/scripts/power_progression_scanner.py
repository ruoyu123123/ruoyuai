#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""power_progression_scanner.py — 升级流主角力量 tier 单调性 / 加速峰值 / 停滞检测
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L60 P0 STRONG)

【缺口】R10 联网调研(Sanderson 2025 Lecture 3 + Andrew Rowe progression fantasy +
凡人修仙传 9 阶 + 百度百科升级流 + Wikipedia)：升级流是男频网文核心子题材，但
全系统【0 检测 tier 单调性】。LLM 默认易写出 tier 倒退(非剧情设计的重伤)/突跳
(章节内瞬升 3 阶)/停滞(长期 0 推进)。

【做法 · 确定性纯规则】：
  1. 读 _数据库/角色弧线.json 的 characters[<protagonist>].protagonist_power_tier
     字段，按 cluster 序列化为 tier_series。
  2. tier_regression：当前 tier < 上一 cluster tier 且无 character_state_changes
     中"重伤/濒死/受创/丹田碎/经脉断"标志 → flag。
  3. escalation_acceleration_spike：5-cluster 滑窗 Δtier/Δcluster >= 2× 全书中位 → flag。
  4. progression_stall：滑窗 Δtier=0 持续 >= 8 cluster 且无 "瓶颈/闭关/沉淀/磨炼"
     标志 → flag。
  5. genre 门控：作者档 user_preferences.power_progression_mode=off 或题材∈
     {romance/mystery/slice_of_life/family_drama} → skip。

【北极星② / ⑤】纯 advisory · 蹿升/倒退/停滞作者档可豁免 · 绝不 hard_gate。
  env POWER_PROGRESSION_MODE: off / shadow(默认) / active。

用法：python power_progression_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE_REGRESSION = "POWER_TIER_REGRESSION"
ISSUE_CODE_SPIKE = "ESCALATION_ACCELERATION_SPIKE"
ISSUE_CODE_STALL = "PROGRESSION_STALL"

_GENRE_SKIP = {"romance", "mystery", "slice_of_life", "family_drama",
               "workplace_drama", "fluff", "espionage"}

INJURY_MARKERS = re.compile(
    r"重伤|濒死|受创|垂死|丹田碎|经脉断|筋骨断|气海乱|功力大损|功体崩|"
    r"道基损|本源损|寿元损|魂体损|境界跌|废了功|散功|境界跌落|修为暴跌")
PLATEAU_MARKERS = re.compile(
    r"瓶颈|闭关|沉淀|磨炼|顿悟|沉心修炼|温养|稳固境界|淬炼|凝实|融合道韵")


def _mode() -> str:
    m = (os.environ.get("POWER_PROGRESSION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    return load_json(p)


def _genre(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if isinstance(obj, dict):
        g = obj.get("genre") or obj.get("genre_pack")
        if isinstance(g, str):
            return g.strip().lower()
    return None


def _user_pref_skip(project_root) -> bool:
    if not project_root:
        return False
    p = Path(project_root) / "_数据库" / "用户偏好.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return False
    return (obj.get("power_progression_mode") or "").strip().lower() == "off"


def _arc_state(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "角色弧线.json"
    return _read_json(p) if p.exists() else None


def _protagonist_id(arc_state):
    if not isinstance(arc_state, dict):
        return None
    chars = arc_state.get("characters")
    if not isinstance(chars, dict):
        return None
    for name, info in chars.items():
        if isinstance(info, dict) and info.get("role") in (
                "protagonist", "主角", "主"):
            return name
    # fallback first character
    for name in chars:
        return name
    return None


def _tier_series(arc_state, protagonist_id):
    """从 angles 角色弧线.json characters[pid].protagonist_power_tier 提取 [(cluster, tier, notes)]"""
    chars = (arc_state or {}).get("characters", {})
    info = chars.get(protagonist_id)
    if not isinstance(info, dict):
        return []
    series = info.get("protagonist_power_tier")
    if not isinstance(series, list):
        return []
    out = []
    for item in series:
        if not isinstance(item, dict):
            continue
        cid = item.get("cluster_id") or item.get("cluster")
        tier = item.get("tier")
        notes = item.get("notes") or item.get("note") or ""
        if cid is None or not isinstance(tier, (int, float)):
            continue
        out.append((str(cid), float(tier), str(notes)))
    return out


def detect_regressions(series):
    """检测非重伤情节的 tier 回退。"""
    flags = []
    prev = None
    for cid, tier, notes in series:
        if prev is not None and tier < prev[1]:
            if not INJURY_MARKERS.search(notes):
                flags.append({"cluster_id": cid, "tier": tier,
                              "prev_tier": prev[1], "prev_cluster": prev[0],
                              "notes": notes[:80]})
        prev = (cid, tier, notes)
    return flags


def detect_acceleration_spike(series, window=5):
    """5-cluster 滑窗 Δtier 比全书中位高 ≥ 2 倍。"""
    if len(series) < window + 1:
        return [], None, None
    deltas = []
    for i in range(1, len(series)):
        deltas.append(series[i][1] - series[i - 1][1])
    if not deltas:
        return [], None, None
    median = statistics.median([d for d in deltas if d > 0]) if any(
        d > 0 for d in deltas) else 0
    spikes = []
    for i in range(window, len(series)):
        win_delta = series[i][1] - series[i - window][1]
        per_step = win_delta / window
        if median > 0 and per_step >= 2 * median:
            spikes.append({"cluster_id": series[i][0], "tier": series[i][1],
                           "window_delta": win_delta,
                           "median_delta": median})
    return spikes, median, deltas


def detect_stall(series, threshold=8):
    """连续 ≥ threshold cluster Δtier=0 且无 plateau marker。"""
    stalls = []
    run_start = None
    run_count = 0
    has_plateau = False
    for i, (cid, tier, notes) in enumerate(series):
        if i == 0:
            continue
        prev_tier = series[i - 1][1]
        if tier == prev_tier:
            if run_count == 0:
                run_start = series[i - 1][0]
            run_count += 1
            if PLATEAU_MARKERS.search(notes):
                has_plateau = True
        else:
            if run_count >= threshold and not has_plateau:
                stalls.append({"start_cluster": run_start,
                               "end_cluster": cid, "stall_len": run_count})
            run_count = 0
            has_plateau = False
            run_start = None
    if run_count >= threshold and not has_plateau:
        stalls.append({"start_cluster": run_start,
                       "end_cluster": series[-1][0], "stall_len": run_count})
    return stalls


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "power_progression", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out
    # genre / preferences gate (北极星②)
    if _user_pref_skip(project_root):
        out["note"] = "用户偏好 power_progression_mode=off · 跳过"
        return out
    g = _genre(project_root)
    if g in _GENRE_SKIP:
        out["note"] = f"题材 {g} 非升级流 · 跳过"
        return out
    arc = _arc_state(project_root)
    if not arc:
        out["note"] = "无 _数据库/角色弧线.json · 跳过(北极星②)"
        return out
    pid = _protagonist_id(arc)
    if not pid:
        out["note"] = "无 protagonist · 跳过"
        return out
    series = _tier_series(arc, pid)
    out["protagonist"] = pid
    out["tier_series_len"] = len(series)
    if len(series) < 2:
        out["note"] = "tier 序列过短(<2) · 无法判断"
        return out

    regressions = detect_regressions(series)
    spikes, median_delta, deltas = detect_acceleration_spike(series)
    stalls = detect_stall(series)
    out["regression_count"] = len(regressions)
    out["spike_count"] = len(spikes)
    out["stall_count"] = len(stalls)
    out["median_positive_delta"] = median_delta
    out["regressions"] = regressions[:5]
    out["spikes"] = spikes[:5]
    out["stalls"] = stalls[:5]

    msgs = []
    if regressions:
        msgs.append({"code": ISSUE_CODE_REGRESSION,
                     "message": (f"tier 倒退 {len(regressions)} 处无受伤标志 "
                                 f"({regressions[0]['cluster_id']}: "
                                 f"{regressions[0]['prev_tier']}→{regressions[0]['tier']})")})
    if spikes:
        msgs.append({"code": ISSUE_CODE_SPIKE,
                     "message": (f"升级加速峰值 {len(spikes)} 处 "
                                 f"({spikes[0]['cluster_id']} 滑窗 Δ="
                                 f"{spikes[0]['window_delta']} ≥2× 中位)")})
    if stalls:
        msgs.append({"code": ISSUE_CODE_STALL,
                     "message": (f"升级停滞 {len(stalls)} 段(最长 "
                                 f"{max(s['stall_len'] for s in stalls)} cluster · "
                                 f"无瓶颈/闭关标志)")})

    if msgs:
        if mode == "active":
            for m in msgs:
                out["violations"].append({
                    "code": m["code"], "kind": "power_progression",
                    "severity": "minor", "message": m["message"],
                    "_doc": "advisory · 升级流主角力量 tier 单调性 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "; ".join(m["message"] for m in msgs)
        else:
            for m in msgs:
                print(f"[SHADOW] power_progression: {m['message']} — 不上报",
                      file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="升级流 tier 单调性/加速峰值/停滞 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
