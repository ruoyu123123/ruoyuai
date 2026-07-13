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
用法: python scene_gap_probe.py <storyboard.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE_ABSENT = "SCENE_GAP_ABSENT"
ISSUE_CODE_MONOTONE = "SCENE_GAP_MONOTONE"

VALID_GAP_TYPES = {"reversal", "escalation", "revelation", "deflection", "ironic", "no_gap"}

DEFAULT_MONOTONE_HIGH = 0.70
DEFAULT_ABSENT_FILLED_LOW = 0.30


def _mode() -> str:
    m = (os.environ.get("SCENE_GAP_PROBE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_storyboard(path: str) -> list[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, dict):
        if isinstance(obj.get("scene_storyboard"), list):
            return obj["scene_storyboard"]
        # cluster brief shape {"clusters":[{"scene_storyboard":...}]}
        clusters = obj.get("clusters") or []
        if isinstance(clusters, list):
            for c in clusters:
                if isinstance(c, dict) and isinstance(c.get("scene_storyboard"), list):
                    return c["scene_storyboard"]
        return []
    if isinstance(obj, list):
        return obj
    return []


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


def main():
    ap = argparse.ArgumentParser(description="McKee scene GAP probe (shadow)")
    ap.add_argument("storyboard_path")
    args = ap.parse_args()
    try:
        sb = _load_storyboard(args.storyboard_path)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"加载失败：{e}"}, ensure_ascii=False))
        sys.exit(2)
    rep = probe(sb)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
