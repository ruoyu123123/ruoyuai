#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_gap_probe.py — McKee 期望-结果 GAP 前置注入 + 二阶验证 · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 影视前置 id 6】Robert McKee《Story》核心命题：每一场都建立在
expectation 与 actual_outcome 之间的 GAP 上 · 无 GAP = 流水账。LLM 默认平铺直叙 ·
GAP 全空。

【做法 · 确定性 storyboard 字段校验】
  · 输入：cluster brief.json 的 scene_storyboard
  · 校验：每个 scene 是否有 (expectation, actual_outcome, gap_type) 三字段
  · 五种 gap_type：reversal / escalation / revelation / deflection / ironic / no_gap
  · 全 no_gap 或全空 → SCENE_GAP_ABSENT advisory
  · 单 gap_type 占比 > 70% → SCENE_GAP_MONOTONE advisory（建议多样）

【边界】只校验 storyboard 声明字段，不读草稿验证 declared_gap_type 是否真兑现
  （无二阶草稿验证 · 输出 _second_order_av_placeholder=true 如实标注）。

【北极星】②④⑤ advisory shadow · 绝不 hard_gate
  SCENE_GAP_ABSENT / SCENE_GAP_MONOTONE 永不进 audit_hub.HARD_GATE_CODES。

env SCENE_GAP_PROBE_MODE: off / shadow(默认) / active
用法: python scene_gap_probe.py --project <项目根> [--cluster <cluster_id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402

ISSUE_CODE_ABSENT = "SCENE_GAP_ABSENT"
ISSUE_CODE_MONOTONE = "SCENE_GAP_MONOTONE"

VALID_GAP_TYPES = {"reversal", "escalation", "revelation", "deflection", "ironic", "no_gap"}

DEFAULT_MONOTONE_HIGH = 0.70
DEFAULT_ABSENT_FILLED_LOW = 0.30


def _mode() -> str:
    m = (os.environ.get("SCENE_GAP_PROBE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _iter_cluster_storyboards(project_root, cluster_id=None):
    """按 cluster_id 精确匹配（cluster_lookup.normalize_cluster_id）定位目标 cluster 的
    scene_storyboard；cluster_id 省略=遍历项目内所有非空 storyboard 的 cluster。"""
    data = _load_json(Path(project_root) / "_数据库" / "事件簇.json", {}) or {}
    target = cl.normalize_cluster_id(cluster_id) if cluster_id else None
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        cid = cl.normalize_cluster_id(c.get("cluster_id"))
        if target and cid != target:
            continue
        sb = c.get("scene_storyboard")
        if isinstance(sb, list) and sb:
            yield cid, sb


def probe(scene_storyboard: list[dict]) -> dict:
    mode = _mode()
    out = {"scanner": "scene_gap_probe", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_second_order_av_placeholder": True}
    if mode == "off":
        return out
    total = len(scene_storyboard or [])
    if total == 0:
        out["note"] = "scene_storyboard 为空·跳过"
        return out
    filled = 0
    type_hits = {}
    invalid_types = 0
    missing_pairs = []
    for i, sc in enumerate(scene_storyboard):
        if not isinstance(sc, dict):
            continue
        exp = sc.get("expectation")
        act = sc.get("actual_outcome")
        gt = sc.get("gap_type")
        if exp and act and gt:
            if gt not in VALID_GAP_TYPES:
                invalid_types += 1
            else:
                filled += 1
                type_hits[gt] = type_hits.get(gt, 0) + 1
        else:
            missing_pairs.append({"scene_index": i,
                                  "missing": [k for k, v in (("expectation", exp),
                                                              ("actual_outcome", act),
                                                              ("gap_type", gt)) if not v]})
    filled_share = round(filled / total, 3) if total else 0.0
    out.update({
        "scenes_total": total,
        "scenes_filled": filled,
        "filled_share": filled_share,
        "type_hits": type_hits,
        "invalid_types": invalid_types,
        "missing_pairs": missing_pairs,
    })

    flags = []
    if filled_share < DEFAULT_ABSENT_FILLED_LOW:
        flags.append({"code": ISSUE_CODE_ABSENT,
                      "msg": f"GAP 三字段填充率={filled_share} < {DEFAULT_ABSENT_FILLED_LOW}·"
                             f"绝大多数 scene 没设期望-结果差·剧情扁平风险"})
    # 全 no_gap 也视作 absent
    if filled >= 3 and type_hits.get("no_gap", 0) / max(1, filled) > 0.9:
        flags.append({"code": ISSUE_CODE_ABSENT,
                      "msg": f"全部 scene gap_type=no_gap·零 GAP 设计·剧情扁平"})

    if filled >= 4:
        for gt, n in type_hits.items():
            share = n / filled
            if gt != "no_gap" and share > DEFAULT_MONOTONE_HIGH:
                flags.append({"code": ISSUE_CODE_MONOTONE,
                              "msg": f"gap_type={gt} 占 {share:.2f} > {DEFAULT_MONOTONE_HIGH}·"
                                     f"单一 GAP 模板·建议混 5 种"})
                break

    out["flags"] = flags
    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "scene_gap_probe", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "McKee《Story》scene GAP · advisory · 绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] scene_gap_probe: {'·'.join(f['msg'] for f in flags)} — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def scan(project_root, cluster_id=None) -> dict:
    """project/cluster 解析层（仿 causal_connector_scanner.scan 分层）：按 cluster_id 精确定位
    目标 cluster 的 scene_storyboard，调用 probe() 纯函数分析，聚合结果并按 cluster_id 打标签。
    probe() 内部已按 _mode() 自行决定是否写入 violations（shadow 恒空）·本层不重复判断。"""
    mode = _mode()
    out = {"scanner": "scene_gap_probe", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out
    all_violations, per_cluster = [], []
    for cid, storyboard in _iter_cluster_storyboards(project_root, cluster_id):
        try:
            rep = probe(storyboard)
        except Exception as e:
            per_cluster.append({"cluster_id": cid, "error": str(e)})
            continue
        for v in rep.get("violations", []):
            all_violations.append({**v, "cluster_id": cid})
        per_cluster.append({"cluster_id": cid, "scenes_total": rep.get("scenes_total")})
    out["per_cluster"] = per_cluster
    if not per_cluster:
        out["note"] = "无标注 scene_storyboard 或未命中目标 cluster"
    out["violations"] = all_violations
    if all_violations:
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(v.get("message", "") for v in all_violations)
    out["violations_count"] = len(all_violations)
    return out


def main():
    ap = argparse.ArgumentParser(description="McKee scene GAP probe (shadow)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", default=None, help="cluster_id（省略=扫所有标注 storyboard 的 cluster）")
    args = ap.parse_args()
    rep = scan(args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
