#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_hub_hierarchical_planner.py — DRAMATURGE 三阶段分层 audit 脚手架
(advisory · shadow 默认 · 2026-06-20 · R8 W4 Batch-H · L24)

【缺口】R8 W4 Batch-H · L24 联网调研：arXiv:2510.05188 DRAMATURGE +53.4%/+66.7%（vs 单层 LLM
审稿）+ OpenReview STORYWRITER 实证『单层平铺打分缺大局视野』。现有 audit_hub.py 走单层并行
13 scanner = 像编剧手册里的逐句审查·没有先看 plot-level 大局再下沉到 scene level 再统筹
revision 的工作流。

【做什么】（脚手架·与现有 audit_hub 共存·全 advisory）
  1. Global pass (本模块原生)：读 cluster_brief.scope_summary / volume_arc / cluster_emergence
     因果链 / 反派阶梯 (antagonist_ladder)·产 global_priority_hints (每 issue code → priority
     0.0/1.0)·识别大局风险 (volume_arc 偏离 / 因果链断裂 / 反派阶梯倒挂)。
  2. Scene pass (沿用现有 audit_hub) ：本模块不实施·由调用者 (audit_hub --hierarchical) 把
     global_priority_hints 注入 audit_hub.run_hub_for_chapter 的 prior context·让现有 13
     scanner 按 hint 排序/加权 (实施层默认透传·shadow 不改 issue gate)。
  3. Coordinated stage：聚合 audit_hub 返回的 issues + global hints·产 revision_plan.json
     (issues 按 priority 排序 + 合并同根问题为一条修改建议)。

【边界 · 北极星】
  · 全 advisory·不引入新 hard_gate (15 码不变)。
  · 不修改 audit_hub.py 任何 scanner 逻辑·不破坏现有路径。
  · shadow 默认·env HIERARCHICAL_AUDIT_MODE=active 才真注入。
  · 缺数据 → 返回骨架 (零回归)。

API
  · plan_global_pass(project_root, cluster_key) -> dict  全局视野扫描
  · coordinate_revision_plan(issues, global_hints) -> dict  统筹修订计划
  · run_hierarchical(project_root, cluster_key, audit_result=None) -> dict  一站式入口

用法
  python audit_hub_hierarchical_planner.py --project <path> --cluster cluster_001
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _mode() -> str:
    m = (os.environ.get("HIERARCHICAL_AUDIT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


# ─── Stage 1: Global pass (本模块原生) ─────────────────────────────────────────

def _read_cluster_brief(project_root: Path, cluster_key: str) -> dict:
    """从 故事块摘要.json / 事件簇.json 提 cluster 大局信息。"""
    db = project_root / "_数据库"
    summary = _read_json(db / "故事块摘要.json")
    clusters = summary.get("clusters") or []
    for c in clusters:
        if isinstance(c, dict) and c.get("cluster_id") == cluster_key:
            return c
    cluster_book = _read_json(db / "事件簇.json")
    cl2 = cluster_book.get("clusters") or []
    if isinstance(cl2, dict):
        return cl2.get(cluster_key, {}) if isinstance(cl2.get(cluster_key), dict) else {}
    if isinstance(cl2, list):
        for c in cl2:
            if isinstance(c, dict) and c.get("cluster_id") == cluster_key:
                return c
    return {}


def _read_volume_arc(project_root: Path) -> dict:
    db = project_root / "_数据库"
    for fn in ("大势卡.json", "进度.json"):
        obj = _read_json(db / fn)
        if obj:
            return obj
    return {}


def plan_global_pass(project_root, cluster_key: str) -> dict:
    """Stage 1：Global pass·读 cluster_brief + volume_arc + 反派阶梯·产 priority hints。

    输出 schema：
      {
        "global_risks": [{"axis": "volume_arc"|"causal_chain"|"antagonist_ladder", ...}],
        "priority_hints": {issue_code: 0.0|1.0},
        "cluster_key": str,
        "scope_summary": str,
      }
    """
    out = {
        "scanner": "audit_hub_hierarchical_planner",
        "stage": "global_pass",
        "mode": _mode(),
        "cluster_key": cluster_key,
        "global_risks": [],
        "priority_hints": {},
        "scope_summary": "",
    }
    if not project_root:
        out["note"] = "无 project_root·骨架返回"
        return out
    project_root = Path(project_root)
    brief = _read_cluster_brief(project_root, cluster_key)
    scope = brief.get("scope_summary") or brief.get("summary") or ""
    out["scope_summary"] = scope[:200] if isinstance(scope, str) else ""
    out["has_brief"] = bool(brief)

    # 大局风险 ① volume_arc 偏离 (advisory hint only · 实判仍在 cross-cluster scanner)
    vol_arc = _read_volume_arc(project_root)
    volume_id = brief.get("volume") or brief.get("_me_volume")
    if vol_arc and volume_id:
        out["global_risks"].append({
            "axis": "volume_arc",
            "volume_id": volume_id,
            "hint": "本 cluster 隶属 volume_id={vid}·关注 cluster 是否服务卷主题 / 是否引入超本卷 stakes".format(vid=volume_id),
            "priority_boost_codes": ["VOLUME_ARC_DRIFT", "PLOT_arc", "PLOT_beat"],
        })
        for code in ("VOLUME_ARC_DRIFT", "PLOT_arc", "PLOT_beat"):
            out["priority_hints"][code] = 1.0

    # 大局风险 ② 因果链断裂 hint
    em_chain = brief.get("cluster_emergence") or brief.get("emergence_chain") or {}
    if em_chain:
        out["global_risks"].append({
            "axis": "causal_chain",
            "hint": "本 cluster 由前块涌现·关注涟漪因果是否在正文兑现",
            "priority_boost_codes": ["FORESHADOWING_HANDOFF_*", "LOCKED_FACT_CROSS_SCENE_CONFLICT"],
        })
        out["priority_hints"]["FORESHADOWING_HANDOFF_NOT_PAID"] = 1.0
        out["priority_hints"]["LOCKED_FACT_CROSS_SCENE_CONFLICT"] = 1.0

    # 大局风险 ③ 反派阶梯倒挂 hint
    ant = brief.get("antagonist_ladder") or brief.get("antagonists") or []
    if ant:
        out["global_risks"].append({
            "axis": "antagonist_ladder",
            "hint": "本 cluster 有反派阶梯·关注反派 fidelity (避免冷哼/狰狞 substitution)",
            "priority_boost_codes": ["ANTAGONIST_FIDELITY_FLAT"],
        })
        out["priority_hints"]["ANTAGONIST_FIDELITY_FLAT"] = 1.0

    return out


# ─── Stage 3: Coordinated revision_plan ───────────────────────────────────────

def _merge_root_cause(issues: list) -> list:
    """同根问题聚类：按 issue.code 分桶·同 code 聚成一条 root_cause item。"""
    buckets: dict[str, list] = {}
    for it in issues:
        if not isinstance(it, dict):
            continue
        code = it.get("code") or it.get("issue_code") or "UNKNOWN"
        buckets.setdefault(code, []).append(it)
    merged = []
    for code, items in buckets.items():
        merged.append({
            "code": code,
            "count": len(items),
            "first_severity": items[0].get("severity") or "minor",
            "first_message": (items[0].get("message") or "")[:200],
            "gate_level": items[0].get("gate_level") or "advisory",
        })
    return merged


def coordinate_revision_plan(issues: list, global_hints: dict | None = None) -> dict:
    """Stage 3：把 audit_hub 的 issues + global hints 统筹为 revision_plan。

    输出：
      {
        "revision_items": [{code,count,priority,severity,gate_level,first_message}],
        "total_issues": N,
        "high_priority_count": M,
      }
    """
    global_hints = global_hints or {}
    if not isinstance(issues, list):
        issues = []
    merged = _merge_root_cause(issues)
    # 按 priority 排序：hard_gate 优先；其次有 global_hint boost·再次按 count 降序
    for m in merged:
        pri = 0.0
        if m["gate_level"] == "hard_gate":
            pri += 10.0
        # global_hint：精确匹配 code 或前缀通配 (FORESHADOWING_HANDOFF_*)
        for hint_code, boost in global_hints.items():
            if not isinstance(boost, (int, float)):
                continue
            if hint_code == m["code"]:
                pri += boost
            elif hint_code.endswith("*") and m["code"].startswith(hint_code[:-1]):
                pri += boost
        pri += min(m["count"], 5) * 0.1
        m["priority"] = round(pri, 2)
    merged.sort(key=lambda x: (-x["priority"], -x["count"]))
    return {
        "scanner": "audit_hub_hierarchical_planner",
        "stage": "coordinated",
        "mode": _mode(),
        "revision_items": merged,
        "total_issues": len(issues),
        "high_priority_count": sum(1 for m in merged if m["priority"] >= 1.0),
    }


# ─── 一站式入口 ───────────────────────────────────────────────────────────────

def run_hierarchical(project_root, cluster_key: str, audit_result: dict | None = None) -> dict:
    """三阶段一站式·脚手架版 (scene pass 透传给现有 audit_hub)。

    audit_result: 调用者 (audit_hub --hierarchical 或外部脚本) 跑完 run_hub_for_chapter 的产出。
      为 None 时只跑 global pass (不实际跑 scene pass·脚手架阶段)。
    """
    global_pass = plan_global_pass(project_root, cluster_key)
    out = {
        "scanner": "audit_hub_hierarchical_planner",
        "schema_version": "1.0",
        "mode": _mode(),
        "cluster_key": cluster_key,
        "stages": {"global": global_pass, "scene": {"deferred_to": "audit_hub.run_hub_for_chapter"}},
    }
    if audit_result and isinstance(audit_result, dict):
        issues = audit_result.get("all_issues") or audit_result.get("issues") or []
        plan = coordinate_revision_plan(issues, global_pass.get("priority_hints"))
        out["stages"]["coordinated"] = plan
    else:
        out["stages"]["coordinated"] = {
            "deferred": True,
            "note": "未传 audit_result·脚手架只跑 global pass·调用方跑完 audit_hub 再 coordinate",
        }
    return out


def main():
    ap = argparse.ArgumentParser(description="DRAMATURGE 三阶段分层 audit 脚手架 (advisory)")
    ap.add_argument("--project", required=True, help="项目根目录")
    ap.add_argument("--cluster", required=True, help="cluster_key (如 cluster_001)")
    ap.add_argument("--audit-result", default=None, help="可选：现有 audit_hub 产出 JSON 路径")
    args = ap.parse_args()
    audit = None
    if args.audit_result:
        try:
            audit = json.loads(Path(args.audit_result).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[hierarchical] audit-result 读失败：{e}", file=sys.stderr)
    result = run_hierarchical(args.project, args.cluster, audit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
