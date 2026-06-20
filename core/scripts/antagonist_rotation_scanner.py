#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""antagonist_rotation_scanner.py — 反派轮替节奏(长篇 1000+)
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L59 P1)

【缺口】R10 联网调研(TVTropes RotatingArcs + Sanderson 2025 + 吞噬星空提前埋
更高 tier 反派 + Wikipedia progression fantasy stage/rank)：长篇网文反派
轮替节奏是核心引擎(打完一个换更高 tier)。LLM 默认易 ① defeated 后空窗过长 ②
新反派 tier 不升 ③ motive_type 同类 ④ power_system_tag 同类。此前【0 检测】。

【做法 · 确定性 JSON ledger】：
  1. 读 _数据库/反派轮替.json append-only ledger:
     {cluster_id, antagonist_id, tier, faction, motive_type, power_system_tag,
      defeat_cluster}.
  2. 四条 advisory：
     · defeated 后 > 3 cluster 无新反派 → ROTATION_VOID
     · 新反派 tier ≤ 上任 tier → ROTATION_TIER_DOWNGRADE
     · 连续 ≥2 反派 motive_type 同类 → ROTATION_MOTIVE_MONOTONE
     · 连续 ≥2 反派 power_system_tag 同类 → ROTATION_POWER_MONOTONE

【北极星② / ⑤】纯 advisory · 无 ledger → skip · 绝不 hard_gate。
  env ANTAGONIST_ROTATION_MODE: off / shadow(默认) / active。

用法：python antagonist_rotation_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_VOID = "ANTAGONIST_ROTATION_VOID"
CODE_DOWNGRADE = "ANTAGONIST_ROTATION_TIER_DOWNGRADE"
CODE_MOTIVE = "ANTAGONIST_ROTATION_MOTIVE_MONOTONE"
CODE_POWER = "ANTAGONIST_ROTATION_POWER_MONOTONE"


def _mode() -> str:
    m = (os.environ.get("ANTAGONIST_ROTATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_ledger(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "反派轮替.json"
    if not p.exists():
        return None
    obj = _read_json(p)
    if isinstance(obj, dict):
        return obj.get("entries") or obj.get("rotations") or []
    if isinstance(obj, list):
        return obj
    return None


def _cluster_index(project_root):
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "cluster_index.json"
    obj = _read_json(p) if p.exists() else None
    out = {}
    items = []
    if isinstance(obj, dict):
        items = obj.get("clusters") or []
    elif isinstance(obj, list):
        items = obj
    for i, item in enumerate(items):
        cid = item.get("cluster_id") if isinstance(item, dict) else str(item)
        if cid:
            out[cid] = i
    return out


def detect_void(entries, idx_map, threshold=3):
    flags = []
    for i, e in enumerate(entries):
        defeat = e.get("defeat_cluster")
        if not defeat or defeat not in idx_map:
            continue
        defeat_idx = idx_map[defeat]
        # find next entry's start cluster
        if i + 1 < len(entries):
            next_start = entries[i + 1].get("cluster_id")
            if next_start in idx_map:
                gap = idx_map[next_start] - defeat_idx
                if gap > threshold:
                    flags.append({"defeated": e.get("antagonist_id"),
                                  "gap": gap,
                                  "next": entries[i + 1].get("antagonist_id")})
        else:
            # last - if defeat was a while ago we can't be sure
            pass
    return flags


def detect_tier_downgrade(entries):
    flags = []
    for i in range(1, len(entries)):
        prev_tier = entries[i - 1].get("tier")
        cur_tier = entries[i].get("tier")
        if (isinstance(prev_tier, (int, float))
                and isinstance(cur_tier, (int, float))
                and cur_tier <= prev_tier):
            flags.append({"prev": entries[i - 1].get("antagonist_id"),
                          "prev_tier": prev_tier,
                          "cur": entries[i].get("antagonist_id"),
                          "cur_tier": cur_tier})
    return flags


def detect_attribute_monotone(entries, attr, code):
    flags = []
    for i in range(1, len(entries)):
        a = entries[i].get(attr)
        b = entries[i - 1].get(attr)
        if a and b and a == b:
            flags.append({"attr": attr, "value": a,
                          "prev_id": entries[i - 1].get("antagonist_id"),
                          "cur_id": entries[i].get("antagonist_id")})
    return flags


def scan(draft_path=None, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "antagonist_rotation", "schema_version": "1.0",
           "mode": mode_env, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    entries = _load_ledger(project_root)
    if entries is None:
        out["note"] = "无 _数据库/反派轮替.json · 跳过(北极星②)"
        return out
    if len(entries) < 2:
        out["entries_count"] = len(entries)
        out["note"] = "ledger 条目 <2 · 无法判断"
        return out
    idx_map = _cluster_index(project_root)
    out["entries_count"] = len(entries)
    voids = detect_void(entries, idx_map)
    downgrades = detect_tier_downgrade(entries)
    motive_mono = detect_attribute_monotone(entries, "motive_type",
                                            CODE_MOTIVE)
    power_mono = detect_attribute_monotone(entries, "power_system_tag",
                                           CODE_POWER)
    out["voids"] = voids[:3]
    out["downgrades"] = downgrades[:3]
    out["motive_monotones"] = motive_mono[:3]
    out["power_monotones"] = power_mono[:3]

    msgs = []
    if voids:
        msgs.append({"code": CODE_VOID,
                     "message": (f"反派轮替空窗 {len(voids)} 处(最大 "
                                 f"gap={max(v['gap'] for v in voids)} cluster)")})
    if downgrades:
        msgs.append({"code": CODE_DOWNGRADE,
                     "message": f"反派 tier 不升级 {len(downgrades)} 处"})
    if motive_mono:
        msgs.append({"code": CODE_MOTIVE,
                     "message": f"反派动机同类 {len(motive_mono)} 处"})
    if power_mono:
        msgs.append({"code": CODE_POWER,
                     "message": f"反派能力体系同类 {len(power_mono)} 处"})
    if msgs:
        if mode_env == "active":
            for m in msgs:
                out["violations"].append({
                    "code": m["code"], "kind": "antagonist_rotation",
                    "severity": "minor", "message": m["message"],
                    "_doc": "advisory · 长篇反派轮替节奏 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "; ".join(m["message"] for m in msgs)
        else:
            for m in msgs:
                print(f"[SHADOW] antagonist_rotation: {m['message']} — 不上报",
                      file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="反派轮替节奏 (advisory · shadow)")
    ap.add_argument("draft_path", nargs="?", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
