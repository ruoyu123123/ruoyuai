#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bremond_cadence_scanner.py — Bremond 三段式 + blockage 节奏分布检测
(advisory · cross-cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (Bremond《Logique du récit》1973): 每个叙事单元都是 potential→process→
outcome 三段式·outcome 四态（成功 success / 失败 failure / 混合 mixed / 未决 deferred）。
此前全系统:
  · R7 Sagging Middle 检测中段塌陷
  · R8 event_density_rhythm 检测高烈度连胜
  · 【cluster-level outcome 节奏分布零检测】——LLM 默认 outcome 全 success=爽文连胜流水账·
    或全 failure=憋屈作文感·或全 deferred=拖延不解。

【做法 · 确定性零依赖（跨 cluster aggregator + per-cluster snapshot）】：
  1. 读 _数据库/事件簇.json 各 cluster 的 cluster_bremond_arc.outcome 字段。
  2. 末窗 N（默认 5）cluster outcome 分布:
     · 同型连续 ≥3 → BREMOND_CADENCE_MONOTONE（『连胜/连憋屈/拖延癌』警示）。
     · 末 N 个 success 占比 ≥80% → BREMOND_CADENCE_OVER_SUCCESS。
     · 末 N 个 failure 占比 ≥60% → BREMOND_CADENCE_OVER_FAILURE。
  3. 作者档 outcome_signature.allow_no_setback=True（无敌流 override）→ 屏蔽 OVER_SUCCESS。
  4. 输出 next_recommended_outcome 给下个 cluster brief 注入（writer manifest 软提示）。

【北极星② / ⑤ 顾问非法官】无敌流是合法风格·作者档第一权威·全 advisory，
  code BREMOND_CADENCE_* **绝不进 audit_hub.HARD_GATE_CODES**。
  env BREMOND_CADENCE_MODE: off / shadow(默认) / active。

用法：python bremond_cadence_scanner.py [--project <root>] [--window N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE_MONOTONE = "BREMOND_CADENCE_MONOTONE"
ISSUE_CODE_OVER_SUCCESS = "BREMOND_CADENCE_OVER_SUCCESS"
ISSUE_CODE_OVER_FAILURE = "BREMOND_CADENCE_OVER_FAILURE"

VALID_OUTCOMES = ("success", "failure", "mixed", "deferred")
DEFAULT_WINDOW = 5


def _mode() -> str:
    m = (os.environ.get("BREMOND_CADENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(path: Path):
    return load_json(path)


def _read_clusters(project_root):
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "事件簇.json"
    if not p.exists():
        return []
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return []
    clusters = obj.get("clusters")
    return clusters if isinstance(clusters, list) else []


def _outcome_of(cluster: dict):
    arc = cluster.get("cluster_bremond_arc") if isinstance(cluster, dict) else None
    if not isinstance(arc, dict):
        return None
    v = arc.get("outcome")
    if isinstance(v, str) and v.strip().lower() in VALID_OUTCOMES:
        return v.strip().lower()
    return None


def _read_author_signature(project_root):
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return {}
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return {}
    sig = obj.get("outcome_signature")
    return sig if isinstance(sig, dict) else {}


def _recommend_next(distribution: dict) -> str:
    """简易：上轮最少的态优先（混合优先于 deferred）。"""
    order = ["mixed", "failure", "success", "deferred"]
    sorted_pool = sorted(order, key=lambda k: distribution.get(k, 0))
    return sorted_pool[0]


def scan(project_root, window=DEFAULT_WINDOW) -> dict:
    mode = _mode()
    out = {"scanner": "bremond_cadence", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out
    clusters = _read_clusters(project_root)
    outcomes = [_outcome_of(c) for c in clusters]
    outcomes = [o for o in outcomes if o]
    out["total_resolved_clusters"] = len(outcomes)
    if len(outcomes) < 3:
        out["note"] = "已结块 <3 · 节奏判断样本不足"
        return out

    win = outcomes[-window:]
    out["window"] = window
    out["window_outcomes"] = win

    distribution = {k: win.count(k) for k in VALID_OUTCOMES}
    out["distribution"] = distribution
    next_rec = _recommend_next(distribution)
    out["next_recommended_outcome"] = next_rec

    violations = []
    sig = _read_author_signature(project_root)
    allow_no_setback = bool(sig.get("allow_no_setback"))

    # 同型连续 streak
    streak = 1
    max_streak_kind = win[-1]
    for i in range(len(win) - 2, -1, -1):
        if win[i] == win[-1]:
            streak += 1
        else:
            break
    out["tail_streak"] = streak
    out["tail_streak_kind"] = max_streak_kind
    if streak >= 3 and not (max_streak_kind == "success" and allow_no_setback):
        violations.append({"code": ISSUE_CODE_MONOTONE, "kind": "bremond_cadence",
                           "severity": "minor",
                           "message": (f"outcome 同型连续 {streak} 个({max_streak_kind})·"
                                       f"节奏单调·建议下 cluster 走 {next_rec}"),
                           "streak": streak, "kind_value": max_streak_kind,
                           "_doc": "advisory·无敌流 allow_no_setback 可屏蔽 success 串"})

    total = len(win)
    success_ratio = distribution["success"] / total
    failure_ratio = distribution["failure"] / total

    if success_ratio >= 0.8 and not allow_no_setback:
        violations.append({"code": ISSUE_CODE_OVER_SUCCESS,
                           "kind": "bremond_over_success", "severity": "minor",
                           "message": (f"近 {total} 块 success 占比 {success_ratio:.0%}"
                                       f"·连胜流水账·建议 mixed/failure 调味"),
                           "ratio": success_ratio,
                           "_doc": "advisory·allow_no_setback 可豁免"})
    if failure_ratio >= 0.6:
        violations.append({"code": ISSUE_CODE_OVER_FAILURE,
                           "kind": "bremond_over_failure", "severity": "minor",
                           "message": (f"近 {total} 块 failure 占比 {failure_ratio:.0%}"
                                       f"·憋屈剧本·建议小胜/混合"),
                           "ratio": failure_ratio,
                           "_doc": "advisory·暗黑/虐主题材可豁免"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] bremond_cadence: {v['message']} — 不上报",
                      file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Bremond outcome 节奏分布(advisory · cross-cluster)")
    ap.add_argument("draft_path", nargs="?", default=None,
                    help="占位兼容 audit_hub 风格(本 scanner 实际只读 事件簇.json)")
    ap.add_argument("--project", default=None, required=False)
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.project, args.window)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
