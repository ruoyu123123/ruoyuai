#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""event_density_rhythm_aggregate.py — 跨 cluster 事件密度节律 scanner
(advisory · cross-cluster · 2026-06-20 · R8 W4 Batch-J · L33)

【缺口】R8 W4 联网调研(Writers Digest Pacing for Emotional Impact tension+release +
Darling Axe Building a Novel + 中国作家网长度问题研究): N 连击高烈度事件无 breather
→ 读者疲劳。LLM 默认每 cluster 都拉满 stakes_delta → 高峰积累但无缓冲。

【做法 · 确定性纯规则】:
  1. 聚合 _数据库/cluster_index.json (或 cluster_summary 等)中各 cluster 的
     stakes_delta + 走向卡事件烈度(intensity)。
  2. 阈值: 连续 ≥ 3 cluster intensity >= 0.7 且无 breather (<0.4) → advisory。
  3. 作者档 breather_cadence_baseline.cluster_n_between_breathers 优先 (默认 3)。
  4. emergence_engine 候选 brief 可读 advisory 输出软提示下个 cluster 走 breather。

【与 R7 Sagging Middle 相反】: Sagging 查中段缺事件; 本项查持续高事件疲劳。

【北极星 ⑤ 顾问非法官】code EVENT_DENSITY_RHYTHM_OVER 绝不进 hard_gate 。
env EVENT_DENSITY_RHYTHM_MODE: off / shadow(默认) / active。

退出码: 0 健康 / 1 advisory
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "EVENT_DENSITY_RHYTHM_OVER"


def _mode() -> str:
    m = (os.environ.get("EVENT_DENSITY_RHYTHM_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_cluster_intensities(project_root) -> list:
    """读各 cluster 的 (cluster_id, intensity) 序列。容忍多 schema。"""
    db = Path(project_root) / "_数据库"
    result = []
    # cluster_index.json
    idx = _load_json(db / "cluster_index.json") or {}
    if isinstance(idx, dict):
        clusters = idx.get("clusters") or []
        if isinstance(clusters, list):
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                cid = c.get("cluster_id") or c.get("id")
                intensity = None
                for k in ("intensity", "stakes_delta", "stakes_intensity",
                          "event_intensity"):
                    v = c.get(k)
                    if isinstance(v, (int, float)):
                        intensity = float(v)
                        break
                if cid and intensity is not None:
                    result.append((cid, intensity))
    # 事件簇.json fallback (仅 done/in_progress)
    if not result:
        ec = _load_json(db / "事件簇.json") or {}
        if isinstance(ec, dict):
            clusters = ec.get("clusters") or []
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                cid = c.get("cluster_id")
                status = (c.get("status") or "").strip().lower()
                if status not in {"done", "completed", "in_progress", "active",
                                  "进行中"}:
                    continue
                intensity = None
                for k in ("intensity", "stakes_delta", "event_intensity"):
                    v = c.get(k)
                    if isinstance(v, (int, float)):
                        intensity = float(v)
                        break
                if cid and intensity is not None:
                    result.append((cid, intensity))
    return result


def _resolve_cadence(project_root) -> int:
    if not project_root:
        return 3
    ap = _load_json(Path(project_root) / "_数据库" / "作者风格.json") or {}
    if isinstance(ap, dict):
        prof = ap.get("breather_cadence_baseline") or {}
        v = prof.get("cluster_n_between_breathers")
        if isinstance(v, int) and v >= 1:
            return v
    return 3  # 通用兜底


def aggregate(project_root) -> dict:
    mode = _mode()
    out = {"scanner": "event_density_rhythm", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out

    series = _read_cluster_intensities(project_root)
    out["cluster_count"] = len(series)
    if len(series) < 3:
        out["note"] = f"cluster 数过少 (n={len(series)})·跳过"
        return out

    cadence = _resolve_cadence(project_root)
    out["breather_cadence_baseline"] = cadence
    HIGH_THRESH = 0.7
    BREATHER_THRESH = 0.4

    # 找连续 N 连击 intensity >= HIGH_THRESH 且无 breather
    streak = 0
    max_streak = 0
    streak_start = None
    streak_window = []
    for cid, intensity in series:
        if intensity >= HIGH_THRESH:
            if streak == 0:
                streak_start = cid
            streak += 1
            streak_window.append((cid, intensity))
            max_streak = max(max_streak, streak)
        else:
            if intensity <= BREATHER_THRESH and streak > 0:
                streak = 0
                streak_window = []
            elif streak > 0:
                # 中间值不算 breather 也不算继续 → reset
                streak = 0
                streak_window = []
    out["max_high_intensity_streak"] = max_streak
    out["high_threshold"] = HIGH_THRESH
    out["breather_threshold"] = BREATHER_THRESH

    msg = None
    if max_streak >= max(3, cadence + 1):
        msg = (f"高烈度事件 {max_streak} 连击无 breather (>= max(3, cadence+1)="
               f"{max(3, cadence + 1)})·建议下个 cluster 走 breather brief"
               f"(intensity <= {BREATHER_THRESH})")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "event_density_rhythm", "severity": "minor",
                "message": msg, "max_streak": max_streak,
                "breather_cadence": cadence,
                "_doc": ("breather 节律是工艺 advisory · 作者档可豁免 · 绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] event_density_rhythm: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="跨 cluster 事件密度节律 (advisory · cross-cluster)")
    ap.add_argument("--project", required=True, help="项目根路径")
    args = ap.parse_args()
    report = aggregate(args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
