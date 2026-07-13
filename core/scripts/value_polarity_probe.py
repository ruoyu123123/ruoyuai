#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""value_polarity_probe.py — Coyne/McKee 场景价值极性翻转 probe · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 影视前置 id 8】Shawn Coyne《The Story Grid》 + McKee《Story》：
每场必有一条价值轴（生/死 · 信任/背叛 · 自由/束缚 …），start_polarity 与
end_polarity 必须翻转或递进（不能持平）· LLM 默认 neutral→neutral 没有翻转。

【做法 · 确定性 storyboard 字段校验】
  · 输入：cluster brief.json 的 scene_storyboard
  · 校验：每 scene 有 (value_axis, start_polarity, end_polarity) 三字段
  · 五档：strongly_positive / positive / neutral / negative / strongly_negative
  · 强制 start_polarity ≠ end_polarity（即 turn 发生）

【两 advisory】
  · VALUE_NO_TURN — 有三字段但 start == end → 该 scene 无翻转
  · TURN_FIDELITY_LOW — turn_fidelity_rate = 翻转 scene 数 / 填充 scene 数 < 0.7

【边界】只校验 storyboard 声明字段，不读草稿验证 start→end 翻转是否真兑现
  （无二阶草稿验证 · 输出 _second_order_av_placeholder=true 如实标注）。

【北极星】②④⑤ advisory shadow · 绝不 hard_gate

用法: python value_polarity_probe.py --project <项目根> [--cluster <cluster_id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402
from atomic_json import load_json  # noqa: E402

ISSUE_CODE_NO_TURN = "VALUE_NO_TURN"
ISSUE_CODE_LOW = "TURN_FIDELITY_LOW"

VALID_POLARITY = {"strongly_positive", "positive", "neutral", "negative", "strongly_negative"}
POLARITY_RANK = {"strongly_negative": -2, "negative": -1, "neutral": 0,
                 "positive": 1, "strongly_positive": 2}

DEFAULT_FIDELITY_LOW = 0.70
DEFAULT_FILLED_LOW = 0.30


def _mode() -> str:
    m = (os.environ.get("VALUE_POLARITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(path: Path, default=None):
    return load_json(path, default=default)


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


def probe(storyboard: list[dict]) -> dict:
    mode = _mode()
    out = {"scanner": "value_polarity_probe", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_second_order_av_placeholder": True}
    if mode == "off":
        return out
    total = len(storyboard or [])
    if total == 0:
        out["note"] = "scene_storyboard 为空·跳过"
        return out
    filled = 0
    turns = 0
    no_turn_indices = []
    invalid = 0
    polarity_distribution = {p: 0 for p in VALID_POLARITY}
    for i, sc in enumerate(storyboard):
        if not isinstance(sc, dict):
            continue
        axis = sc.get("value_axis")
        sp = sc.get("start_polarity")
        ep = sc.get("end_polarity")
        if axis and sp and ep:
            if sp not in VALID_POLARITY or ep not in VALID_POLARITY:
                invalid += 1
                continue
            filled += 1
            polarity_distribution[sp] = polarity_distribution.get(sp, 0) + 1
            if sp == ep:
                no_turn_indices.append(i)
            else:
                turns += 1
    fidelity = round(turns / filled, 3) if filled else 0.0
    filled_share = round(filled / total, 3) if total else 0.0

    out.update({
        "scenes_total": total,
        "scenes_filled": filled,
        "filled_share": filled_share,
        "turns": turns,
        "turn_fidelity_rate": fidelity,
        "no_turn_indices": no_turn_indices,
        "invalid_polarity": invalid,
        "polarity_distribution": polarity_distribution,
    })

    flags = []
    if no_turn_indices:
        flags.append({"code": ISSUE_CODE_NO_TURN,
                      "msg": f"{len(no_turn_indices)} 个 scene start==end·价值轴没翻转"
                             f"·indices={no_turn_indices[:6]}"})
    if filled >= 3 and fidelity < DEFAULT_FIDELITY_LOW:
        flags.append({"code": ISSUE_CODE_LOW,
                      "msg": f"turn_fidelity_rate={fidelity} < {DEFAULT_FIDELITY_LOW}·"
                             f"超 30% scene 价值静止·建议每场必翻"})

    out["flags"] = flags
    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "value_polarity_probe", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "Coyne/McKee 价值极性翻转 · advisory · 绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] value_polarity_probe: {'·'.join(f['msg'] for f in flags)} — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def scan(project_root, cluster_id=None) -> dict:
    """project/cluster 解析层（仿 causal_connector_scanner.scan 分层）：按 cluster_id 精确定位
    目标 cluster 的 scene_storyboard，调用 probe() 纯函数分析，聚合结果并按 cluster_id 打标签。
    probe() 内部已按 _mode() 自行决定是否写入 violations（shadow 恒空）·本层不重复判断。"""
    mode = _mode()
    out = {"scanner": "value_polarity_probe", "schema_version": "1.0", "mode": mode,
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
    ap = argparse.ArgumentParser(description="Coyne/McKee value polarity probe (shadow)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", default=None, help="cluster_id（省略=扫所有标注 storyboard 的 cluster）")
    args = ap.parse_args()
    rep = scan(args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
