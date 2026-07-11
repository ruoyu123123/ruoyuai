#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""event_relation_graph_builder.py — outline 跨 cluster 因果有向图建构(R19 W8 Batch-W·P1)

【缺口·2026-06-21·arxiv 2506.16445 StoryWriter】outline 阶段缺一张
「跨 cluster 因果 + 角色 throughline」有向图·当前只有 scene_storyboard
(cluster 内)/foreshadowing(单事件)/涟漪规则(状态机) + signed_relation_graph
(关系符号)·没有 event-level 跨 cluster 因果 DAG·orphan event/孤儿伏笔/角色
通路断裂无法静态检测。

【输入】
  事件簇.json (clusters[].id/title/scope_summary/foreshadowing_to_plant/payoff)
  大势卡.json (major_events[].id/title/volume/prerequisites)
  伏笔表.json (promises[].id/setup_cluster/payoff_cluster/related_chars)

【输出 schema】(_数据库/event_relation_graph.json)
  {
    "schema_version": 1,
    "_doc": "...",
    "nodes": [
      {"id": "ME-V1-1", "kind": "major_event", "title": "...", "volume": 1, "cluster": "cluster_001"},
      {"id": "cluster_002", "kind": "cluster", "title": "...", "volume": 1},
      {"id": "F-001", "kind": "foreshadowing", "title": "...", "setup": "cluster_001", "payoff": "cluster_005"}
    ],
    "edges": [
      {"src": "ME-V1-1", "dst": "ME-V1-2", "kind": "causal_prerequisite"},
      {"src": "F-001", "dst": "cluster_005", "kind": "foreshadowing_payoff"},
      {"src": "char:江条款", "dst": "cluster_002", "kind": "throughline"}
    ],
    "character_throughlines": {"char:江条款": ["cluster_001", "cluster_002", "cluster_003"]}
  }

【北极星】
  - cluster 单位·注册于 scanner_registry.json，未被 outline.plan.json 或任何 plan/orchestrator 调度
  - builder 只读 outline 产物·不改写任何子系统·只写新文件
  - env EVENT_RELATION_GRAPH_MODE 默认 shadow(不写文件) / build(实际产 JSON)

用法: python event_relation_graph_builder.py --project <root> [--out <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def _mode() -> str:
    m = (os.environ.get("EVENT_RELATION_GRAPH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "build") else "shadow"


def _load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _collect_majors(da_shi_ka):
    """从大势卡产 ME 节点 + cluster prereq 边。"""
    nodes = []
    edges = []
    if not isinstance(da_shi_ka, dict):
        return nodes, edges
    mes = da_shi_ka.get("major_events_pool") or da_shi_ka.get("major_events") or []
    for me in mes:
        if not isinstance(me, dict):
            continue
        mid = me.get("id")
        if not isinstance(mid, str):
            continue
        nodes.append({
            "id": mid,
            "kind": "major_event",
            "title": me.get("title", ""),
            "volume": me.get("volume"),
            "cluster": me.get("cluster"),
        })
        for prereq in me.get("prerequisites") or []:
            if isinstance(prereq, str):
                edges.append({"src": prereq, "dst": mid, "kind": "causal_prerequisite"})
    return nodes, edges


def _collect_clusters(shi_jian_cu):
    """从事件簇产 cluster 节点·payoff 字段连边到下游 cluster。"""
    nodes = []
    edges = []
    if not isinstance(shi_jian_cu, dict):
        return nodes, edges
    clusters = shi_jian_cu.get("clusters") or []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or c.get("cluster_id")
        if not isinstance(cid, str):
            continue
        nodes.append({
            "id": cid,
            "kind": "cluster",
            "title": c.get("title", ""),
            "volume": c.get("volume"),
            "scope_summary": (c.get("scope_summary") or "")[:200],
        })
        # 显式 prerequisites
        for prereq in c.get("prerequisites") or []:
            if isinstance(prereq, str):
                edges.append({"src": prereq, "dst": cid, "kind": "cluster_prerequisite"})
        # payoff 指向下游 cluster id
        payoff = c.get("payoff_cluster")
        if isinstance(payoff, str):
            edges.append({"src": cid, "dst": payoff, "kind": "cluster_payoff"})
    return nodes, edges


def _collect_foreshadowings(fu_bi):
    """从伏笔表产 F 节点·setup→F→payoff 三连。"""
    nodes = []
    edges = []
    if not isinstance(fu_bi, dict):
        return nodes, edges
    promises = fu_bi.get("promises") or []
    for p in promises:
        if not isinstance(p, dict):
            continue
        fid = p.get("id")
        if not isinstance(fid, str):
            continue
        nodes.append({
            "id": fid,
            "kind": "foreshadowing",
            "title": p.get("description", "") or p.get("title", ""),
            "tier": p.get("tier"),
            "setup": p.get("setup_cluster"),
            "payoff": p.get("payoff_cluster"),
        })
        setup = p.get("setup_cluster")
        payoff = p.get("payoff_cluster")
        if isinstance(setup, str):
            edges.append({"src": setup, "dst": fid, "kind": "foreshadowing_setup"})
        if isinstance(payoff, str):
            edges.append({"src": fid, "dst": payoff, "kind": "foreshadowing_payoff"})
    return nodes, edges


def _collect_character_throughlines(shi_jian_cu, fu_bi):
    """聚合角色跨 cluster 出现序列·角色→cluster 边 + char throughline 列表。"""
    nodes = []
    edges = []
    tlines: dict[str, list[str]] = {}
    if isinstance(shi_jian_cu, dict):
        for c in shi_jian_cu.get("clusters") or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") or c.get("cluster_id")
            if not isinstance(cid, str):
                continue
            chars = c.get("focal_characters") or c.get("characters") or []
            for ch in chars:
                if isinstance(ch, str) and ch:
                    key = f"char:{ch}"
                    tlines.setdefault(key, []).append(cid)
                    edges.append({"src": key, "dst": cid, "kind": "throughline"})
    if isinstance(fu_bi, dict):
        for p in fu_bi.get("promises") or []:
            if not isinstance(p, dict):
                continue
            chars = p.get("related_chars") or []
            setup = p.get("setup_cluster")
            for ch in chars:
                if isinstance(ch, str) and ch and isinstance(setup, str):
                    key = f"char:{ch}"
                    tlines.setdefault(key, []).append(setup)
    # 去重保序
    for k in list(tlines.keys()):
        seen = []
        for x in tlines[k]:
            if x not in seen:
                seen.append(x)
        tlines[k] = seen
        nodes.append({"id": k, "kind": "character", "title": k.split(":", 1)[1]})
    return nodes, edges, tlines


def build(project_root: Path) -> dict:
    db = project_root / "_数据库"
    da_shi_ka = _load_json(db / "大势卡.json")
    shi_jian_cu = _load_json(db / "事件簇.json")
    fu_bi = _load_json(db / "伏笔表.json")

    nodes_a, edges_a = _collect_majors(da_shi_ka)
    nodes_b, edges_b = _collect_clusters(shi_jian_cu)
    nodes_c, edges_c = _collect_foreshadowings(fu_bi)
    nodes_d, edges_d, tlines = _collect_character_throughlines(shi_jian_cu, fu_bi)

    # 节点去重(按 id)
    nodes_by_id = {}
    for n in nodes_a + nodes_b + nodes_c + nodes_d:
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            nodes_by_id.setdefault(n["id"], n)

    return {
        "schema_version": SCHEMA_VERSION,
        "_doc": "R19 W8 Batch-W·P1·outline 跨 cluster 因果 + throughline DAG·StoryWriter arxiv 2506.16445",
        "nodes": list(nodes_by_id.values()),
        "edges": edges_a + edges_b + edges_c + edges_d,
        "character_throughlines": tlines,
    }


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-W·event_relation_graph builder")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", default=None)
    args, _ = ap.parse_known_args()

    mode = _mode()
    if mode == "off":
        print(json.dumps({"mode": "off", "skipped": True}, ensure_ascii=False))
        return 0

    project = Path(args.project)
    graph = build(project)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = project / "_数据库" / "event_relation_graph.json"

    if mode == "build":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": "build", "path": str(out_path),
                          "nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
                         ensure_ascii=False))
    else:
        # shadow: 只输出统计·不落盘
        print(json.dumps({"mode": "shadow", "nodes": len(graph["nodes"]),
                          "edges": len(graph["edges"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
