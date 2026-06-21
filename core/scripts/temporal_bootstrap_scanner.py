#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""temporal_bootstrap_scanner.py — info_provenance cycle Tarjan SCC 时间环检测
(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·time travel / 预言 / 闪回叙事易出 bootstrap paradox】
若信息 X 由角色 A 在 t1 获得·而 A 的获得来源是 t2 的角色 B·B 又从 t3 的 X 自身得来 →
时间环(causal loop / bootstrap paradox). 写作端非时间旅行类小说也会发生(伏笔回收
弄成"角色提前知道自己后来才知道的事")·属穿帮.

本 scanner 读 _数据库/info_provenance.json 的 信息→来源 边·建图·跑 Tarjan SCC 找强连
通分量·SCC 大小 ≥2(含自环) → TEMPORAL_BOOTSTRAP_LOOP advisory.

【输入】project_root → _数据库/info_provenance.json
  {
    "edges": [
      {"info": "X", "from": "A_at_t1"},
      {"info": "X", "from": "B_at_t2"},
      ...
    ],
    "_doc": "..."
  }
或 cluster brief 的 info_provenance 字段·缺则 skip.

【北极星⑤】顾问非法官·全 advisory·env TEMPORAL_BOOTSTRAP_MODE 默认 shadow·
  TEMPORAL_BOOTSTRAP_LOOP 绝不 hard_gate.

【与既有 scanner 严格正交】
  - locked_fact_cross_scene : 设定一致性(同时刻)·正交
  - future_knowledge_leak   : 未来知识泄露(单点)·正交(本=多点环)
  - foreshadowing_handoff   : 伏笔交接·正交
  - event_relation_graph_validator: 跨 cluster 因果 DAG·正交(本=同 cluster info 环)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ISSUE_CODE = "TEMPORAL_BOOTSTRAP_LOOP"


def _mode() -> str:
    m = (os.environ.get("TEMPORAL_BOOTSTRAP_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_provenance(project_root, cluster_brief_path):
    """读 info_provenance(优先 cluster brief.info_provenance·后兜 _数据库/info_provenance.json)."""
    edges = []
    if cluster_brief_path and Path(cluster_brief_path).exists():
        try:
            cb = json.loads(Path(cluster_brief_path).read_text(encoding="utf-8"))
            v = (cb or {}).get("info_provenance") or {}
            if isinstance(v, dict):
                e = v.get("edges") or []
                if isinstance(e, list):
                    edges = e
        except (OSError, json.JSONDecodeError):
            edges = []
    if not edges and project_root:
        p = Path(project_root) / "_数据库" / "info_provenance.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    e = d.get("edges") or []
                    if isinstance(e, list):
                        edges = e
            except (OSError, json.JSONDecodeError):
                edges = []
    return edges


def _build_graph(edges):
    """edges 转图. node = info 名 + from 名(各成节点)·边 = info → from(信息来源指向)."""
    g = defaultdict(list)
    nodes = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        info = e.get("info")
        src = e.get("from") or e.get("source")
        if not isinstance(info, str) or not isinstance(src, str):
            continue
        if not info or not src:
            continue
        g[info].append(src)
        nodes.add(info)
        nodes.add(src)
    return g, nodes


def tarjan_scc(graph, nodes):
    """经典 Tarjan SCC. 返回 list[list[node]]·只回 size≥2 或自环."""
    index = [0]
    stack = []
    on_stack = set()
    idx = {}
    low = {}
    sccs = []

    def strongconnect(v):
        idx[v] = index[0]
        low[v] = index[0]
        index[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in idx:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    # 用迭代版避免深度递归(Python 默认 1000)
    sys.setrecursionlimit(max(2000, sys.getrecursionlimit()))
    for v in list(nodes):
        if v not in idx:
            strongconnect(v)
    # 过滤
    cycles = []
    for comp in sccs:
        if len(comp) >= 2:
            cycles.append(comp)
        elif len(comp) == 1:
            n = comp[0]
            # 自环
            if n in graph.get(n, ()):
                cycles.append(comp)
    return cycles


def scan(project_root=None, cluster_brief_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "temporal_bootstrap", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "cycles": [], "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        out["note"] = "off·skip"
        return out
    edges = _load_provenance(project_root, cluster_brief_path)
    if not edges:
        out["note"] = "无 info_provenance edges·skip"
        return out
    g, nodes = _build_graph(edges)
    if not g:
        out["note"] = "edges 全无效·skip"
        return out
    cycles = tarjan_scc(g, nodes)
    out["cycles"] = cycles
    out["metrics"] = {"node_count": len(nodes), "edge_count": len(edges),
                      "cycle_count": len(cycles)}

    if cycles:
        # 取前 3 个环展示
        preview = [{"size": len(c), "nodes": c[:6]} for c in cycles[:3]]
        msg = f"info_provenance 检出 {len(cycles)} 个时间环·首 3: {preview}"
        if mode == "active":
            out["violations"].append({
                "kind": "temporal_bootstrap", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "metrics": out["metrics"], "cycle_preview": preview,
                "_doc": "R19 W8 Batch-Y·P2·Tarjan SCC bootstrap paradox·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] temporal_bootstrap[{ISSUE_CODE}]: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·info_provenance Tarjan SCC bootstrap paradox·advisory·shadow")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-brief", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("draft_path", nargs="?", default=None,
                    help="兼容 audit_hub 签名·未使用")
    args, _ = ap.parse_known_args()
    rep = scan(args.project, args.cluster_brief)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
