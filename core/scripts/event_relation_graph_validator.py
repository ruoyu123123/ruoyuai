#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""event_relation_graph_validator.py — 跨 cluster 因果 DAG 校验(R19 W8 Batch-W·P1)

【4 类 issue·全 advisory·绝不 hard_gate】
  NODE_ORPHAN                  : 节点入度 0 且出度 0 (孤儿)
  EDGE_CYCLE                   : DAG 中检测到环 (Tarjan SCC 大小 ≥2)
  PAYOFF_UNREACHABLE           : foreshadowing 节点没有可达 payoff cluster
  CHARACTER_THROUGH_LINE_BROKEN: 角色 throughline 中间 cluster 缺位
                                 (出现 ch1, ch5 但 ch2/3/4 都不在 cluster 列表)

【北极星⑤】顾问非法官·全 advisory·env EVENT_RELATION_GRAPH_VALIDATE_MODE
  默认 shadow·4 个 code 绝不 hard_gate。

用法: python event_relation_graph_validator.py <graph_path>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODES = (
    "NODE_ORPHAN",
    "EDGE_CYCLE",
    "PAYOFF_UNREACHABLE",
    "CHARACTER_THROUGH_LINE_BROKEN",
)


def _mode() -> str:
    m = (os.environ.get("EVENT_RELATION_GRAPH_VALIDATE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _detect_cycles(nodes, edges):
    """Tarjan SCC·返回环节点列表。"""
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}
    for e in edges:
        if not isinstance(e, dict):
            continue
        s, d = e.get("src"), e.get("dst")
        if isinstance(s, str) and isinstance(d, str) and s in adj and d in adj:
            adj[s].append(d)

    index = [0]
    stack: list[str] = []
    on_stack: dict[str, bool] = {}
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str):
        # 迭代式 Tarjan 防栈溢出
        work = [(v, iter(adj.get(v, [])))]
        indices[v] = index[0]
        lowlink[v] = index[0]
        index[0] += 1
        stack.append(v)
        on_stack[v] = True
        while work:
            cur, it = work[-1]
            nxt = next(it, None)
            if nxt is None:
                if lowlink[cur] == indices[cur]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        comp.append(w)
                        if w == cur:
                            break
                    if len(comp) >= 2 or (len(comp) == 1 and comp[0] in adj.get(comp[0], [])):
                        sccs.append(comp)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[cur])
            else:
                if nxt not in indices:
                    indices[nxt] = index[0]
                    lowlink[nxt] = index[0]
                    index[0] += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(adj.get(nxt, []))))
                elif on_stack.get(nxt):
                    lowlink[cur] = min(lowlink[cur], indices[nxt])

    for nid in list(adj.keys()):
        if nid not in indices:
            strongconnect(nid)
    return sccs


def _check_payoff_reachable(nodes, edges):
    """foreshadowing 节点出度需含 foreshadowing_payoff·payoff cluster 节点需存在。"""
    cluster_ids = {n["id"] for n in nodes if isinstance(n, dict)
                   and n.get("kind") == "cluster" and isinstance(n.get("id"), str)}
    out_by_src: dict[str, list[dict]] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        s = e.get("src")
        if isinstance(s, str):
            out_by_src.setdefault(s, []).append(e)

    unreachable = []
    for n in nodes:
        if not isinstance(n, dict) or n.get("kind") != "foreshadowing":
            continue
        fid = n.get("id")
        if not isinstance(fid, str):
            continue
        outs = out_by_src.get(fid, [])
        has_payoff = False
        for e in outs:
            if e.get("kind") == "foreshadowing_payoff":
                dst = e.get("dst")
                if isinstance(dst, str) and dst in cluster_ids:
                    has_payoff = True
                    break
        if not has_payoff:
            unreachable.append(fid)
    return unreachable


def _check_orphan_nodes(nodes, edges):
    """入度 0 且出度 0 的节点。character 节点豁免(它本身是 throughline 源)。"""
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            in_deg.setdefault(n["id"], 0)
            out_deg.setdefault(n["id"], 0)
    for e in edges:
        if not isinstance(e, dict):
            continue
        s, d = e.get("src"), e.get("dst")
        if isinstance(s, str):
            out_deg[s] = out_deg.get(s, 0) + 1
        if isinstance(d, str):
            in_deg[d] = in_deg.get(d, 0) + 1
    orphans = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if not isinstance(nid, str):
            continue
        if n.get("kind") == "character":
            continue
        if in_deg.get(nid, 0) == 0 and out_deg.get(nid, 0) == 0:
            orphans.append(nid)
    return orphans


def _check_character_throughline_broken(throughlines, nodes):
    """角色 throughline 中间 cluster 缺位。"""
    cluster_ids = sorted({n["id"] for n in nodes if isinstance(n, dict)
                          and n.get("kind") == "cluster" and isinstance(n.get("id"), str)})
    broken = []
    if not isinstance(throughlines, dict):
        return broken
    # 抽 cluster id 的数字尾缀做序
    def _cnum(cid):
        digits = "".join(ch for ch in cid if ch.isdigit())
        return int(digits) if digits else None

    cluster_order = {cid: _cnum(cid) for cid in cluster_ids if _cnum(cid) is not None}

    for ch, cluster_list in throughlines.items():
        if not isinstance(cluster_list, list) or len(cluster_list) < 2:
            continue
        present = [cid for cid in cluster_list if cid in cluster_order]
        if len(present) < 2:
            continue
        nums = sorted({cluster_order[cid] for cid in present})
        # 若 nums 不连续(min..max 内有缺) 且缺的 cluster 存在 → broken
        span = set(range(nums[0], nums[-1] + 1))
        present_nums = set(nums)
        missing_nums = span - present_nums
        if not missing_nums:
            continue
        # missing 的 cluster 存在于全图但没在 throughline → broken
        # 反映角色在某些中间 cluster 没出现
        broken.append({"character": ch, "missing_clusters": sorted(missing_nums)})
    return broken


def validate(graph: dict) -> dict:
    mode = _mode()
    out = {
        "validator": "event_relation_graph",
        "schema_version": "1.0",
        "mode": mode,
        "codes": list(ISSUE_CODES),
        "gate_level": "advisory",
        "violations": [],
        "verdict": "PASS",
    }
    if mode == "off":
        return out
    if not isinstance(graph, dict):
        out["note"] = "图为空·skip"
        return out

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    throughlines = graph.get("character_throughlines") or {}

    orphans = _check_orphan_nodes(nodes, edges)
    cycles = _detect_cycles(nodes, edges)
    unreachable = _check_payoff_reachable(nodes, edges)
    broken = _check_character_throughline_broken(throughlines, nodes)

    out["metrics"] = {
        "nodes_total": len(nodes),
        "edges_total": len(edges),
        "orphan_count": len(orphans),
        "cycle_count": len(cycles),
        "unreachable_count": len(unreachable),
        "broken_throughline_count": len(broken),
    }

    findings = []
    if orphans:
        findings.append(("NODE_ORPHAN", f"孤儿节点 {len(orphans)} 个: {orphans[:5]}"))
    if cycles:
        findings.append(("EDGE_CYCLE", f"DAG 含环 {len(cycles)} 个 SCC: {[c[:3] for c in cycles[:3]]}"))
    if unreachable:
        findings.append(("PAYOFF_UNREACHABLE",
                         f"foreshadowing 无 payoff cluster {len(unreachable)} 个: {unreachable[:5]}"))
    if broken:
        findings.append(("CHARACTER_THROUGH_LINE_BROKEN",
                         f"角色 throughline 断裂 {len(broken)} 处: {broken[:3]}"))

    if findings:
        if mode == "active":
            for code, msg in findings:
                out["violations"].append({
                    "kind": "event_relation_graph", "severity": "minor",
                    "code": code, "message": msg, "metrics": out["metrics"],
                    "_doc": "R19 W8 Batch-W·event_relation_graph·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = " · ".join(m for _, m in findings)
        else:
            for code, msg in findings:
                print(f"[SHADOW] event_relation_graph[{code}]: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-W·event_relation_graph validator")
    ap.add_argument("graph_path")
    args, _ = ap.parse_known_args()
    p = Path(args.graph_path)
    try:
        graph = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False))
        return 1
    rep = validate(graph)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
