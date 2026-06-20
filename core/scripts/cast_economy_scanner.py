#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cast_economy_scanner.py — 配角经济（introduce_burst/composite_hint/role_split）
(advisory · cross-cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (Truby《Anatomy of Story》配角网络): cast 经济三常见症状:
  · introduce_burst: 单 cluster 新引入 ≥3 角色（读者负担过载）。
  · composite_hint: 同 actant 位重复角色（建议合并）。
  · role_split: 同名角色在不同 cluster 出现互斥设定（隐式拆分=未声明的双重身份）。
此前全系统【cast 经济层零检测】。

【做法 · 确定性零依赖（依赖 manifest.cast_state + 历史 cluster 角色档）】：
  1. 读 manifest.active_cast（本 cluster 活跃角色 list）+ manifest.cluster_actant_state
     （actant 派分·Batch-L#1 同源）+ _数据库/人物卡.json（已声明角色名）。
  2. 历史 cast 累计 → 计本 cluster 新引入（manifest.active_cast - 历史）。
  3. 三类 advisory:
     · CAST_INTRODUCE_BURST: 新引入 > intro_budget（默认 2·群像题材 override=4）。
     · CAST_COMPOSITE_HINT: 同 actant 位 ≥2 角色挂 helper（建议合并）。
     · CAST_ROLE_SPLIT_IMPLICIT: 历史某角色 first_actant=helper 当前=opponent 且 无 pivot=隐式拆分。
  4. 群像题材（scheming_politics/heist_caper/ensemble_*）作者档/genre pack 可 override intro_budget。

【北极星② / ⑤ 顾问非法官】群像题材有更宽容 budget·全 advisory，
  code CAST_* **绝不进 audit_hub.HARD_GATE_CODES**。
  env CAST_ECONOMY_MODE: off / shadow(默认) / active。

用法：python cast_economy_scanner.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE_BURST = "CAST_INTRODUCE_BURST"
ISSUE_CODE_COMPOSITE = "CAST_COMPOSITE_HINT"
ISSUE_CODE_SPLIT = "CAST_ROLE_SPLIT_IMPLICIT"

# 群像题材默认 budget override
ENSEMBLE_GENRES = {"scheming_politics", "heist_caper", "espionage",
                   "ensemble_drama", "court_intrigue"}
DEFAULT_INTRO_BUDGET = 2
ENSEMBLE_INTRO_BUDGET = 4


def _mode() -> str:
    m = (os.environ.get("CAST_ECONOMY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_manifest(manifest_path):
    if not manifest_path:
        return {}
    obj = _read_json(Path(manifest_path))
    return obj if isinstance(obj, dict) else {}


def _read_known_cast(project_root):
    """从 人物卡.json 抽已声明角色名集合（历史 cumulative）。"""
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return set()
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return set()
    out = set()
    chars = obj.get("characters") or obj.get("人物") or []
    if isinstance(chars, dict):
        chars = list(chars.values())
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, dict):
                name = c.get("name") or c.get("姓名")
                if isinstance(name, str) and name.strip():
                    out.add(name.strip())
            elif isinstance(c, str):
                out.add(c.strip())
    return out


def _read_cluster_ledger_history(project_root, cluster_id):
    """从 cluster_actant_ledger.json 读历史 cluster 的角色 actant 分配。"""
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "cluster_actant_ledger.json"
    if not p.exists():
        return {}
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return {}
    out = {}  # name -> [(cluster_id, position)]
    for rec in obj.get("clusters") or []:
        if not isinstance(rec, dict):
            continue
        cid = str(rec.get("cluster_id") or "")
        if cid == str(cluster_id):
            continue
        for pos, name in (rec.get("assignments") or {}).items():
            if name:
                out.setdefault(name, []).append((cid, pos))
    return out


def _resolve_genre(project_root):
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for candidate, key in [("用户偏好.json", "genre"),
                            ("作者风格.json", "genre")]:
        p = db / candidate
        if not p.exists():
            continue
        obj = _read_json(p)
        if isinstance(obj, dict):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    return None


def _pivot_events(manifest):
    pivots = set()
    for key in ("pivot_events",):
        seq = manifest.get(key)
        if isinstance(seq, list):
            for it in seq:
                if isinstance(it, str) and it.strip():
                    pivots.add(it.strip())
                elif isinstance(it, dict):
                    name = it.get("character") or it.get("name")
                    if isinstance(name, str) and name.strip():
                        pivots.add(name.strip())
    return pivots


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "cast_economy", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out

    manifest = _read_manifest(manifest_path)
    cluster_id = (manifest.get("cluster_id") or manifest.get("cluster_key")
                  or "cluster_001")
    active_cast = manifest.get("active_cast") or []
    if not isinstance(active_cast, list):
        active_cast = []
    active_cast = [c.strip() for c in active_cast
                   if isinstance(c, str) and c.strip()]
    out["cluster_id"] = cluster_id
    out["active_cast_count"] = len(active_cast)

    if not active_cast:
        out["note"] = "manifest 无 active_cast·跳过(北极星②)"
        return out

    known = _read_known_cast(project_root)
    history_ledger = _read_cluster_ledger_history(project_root, cluster_id)
    historical_names = set(history_ledger.keys()) | known

    newly_introduced = [c for c in active_cast if c not in historical_names]
    out["newly_introduced"] = newly_introduced

    genre = _resolve_genre(project_root)
    if genre in ENSEMBLE_GENRES:
        intro_budget = ENSEMBLE_INTRO_BUDGET
    else:
        intro_budget = DEFAULT_INTRO_BUDGET
    out["intro_budget"] = intro_budget
    out["genre"] = genre

    violations = []
    if len(newly_introduced) > intro_budget:
        violations.append({"code": ISSUE_CODE_BURST, "kind": "cast_burst",
                           "severity": "minor",
                           "message": (f"本 cluster 新引入 {len(newly_introduced)} 角色 "
                                       f"> budget {intro_budget}: "
                                       f"{','.join(newly_introduced[:5])}"),
                           "intro_count": len(newly_introduced),
                           "intro_budget": intro_budget,
                           "names": newly_introduced[:8],
                           "_doc": "advisory·群像题材自动放宽"})

    # composite_hint: 同 actant 位 ≥2 角色
    cluster_actant = manifest.get("cluster_actant_state") or {}
    if isinstance(cluster_actant, dict):
        # 兼容: helpers/opponents 多角色 list
        composite = []
        for pos in ("helper", "opponent"):
            v = cluster_actant.get(pos)
            if isinstance(v, list) and len(v) >= 2:
                composite.append({"position": pos, "names": v[:5]})
        if composite:
            violations.append({"code": ISSUE_CODE_COMPOSITE,
                               "kind": "cast_composite", "severity": "minor",
                               "message": (f"同 actant 位 ≥2 角色: "
                                           f"{composite[0]['position']}={composite[0]['names']}"),
                               "composites": composite,
                               "_doc": "advisory·考虑合并配角节省读者负担"})

    # role_split_implicit
    pivots = _pivot_events(manifest)
    splits = []
    for name in active_cast:
        history = history_ledger.get(name)
        if not history:
            continue
        history_positions = {pos for _, pos in history}
        if "helper" in history_positions:
            current_pos = None
            for p in ("subject", "object", "sender", "receiver", "helper", "opponent"):
                v = cluster_actant.get(p) if isinstance(cluster_actant, dict) else None
                if v == name or (isinstance(v, list) and name in v):
                    current_pos = p
                    break
            if current_pos == "opponent" and name not in pivots:
                splits.append({"character": name, "history": list(history_positions),
                               "current": current_pos})
    if splits:
        violations.append({"code": ISSUE_CODE_SPLIT, "kind": "cast_split_implicit",
                           "severity": "minor",
                           "message": (f"隐式 role_split: {splits[0]['character']} "
                                       f"历史 helper→当前 opponent 且无 pivot"),
                           "splits": splits,
                           "_doc": "advisory·声明 pivot_events 或拆双名"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] cast_economy: {v['message']} — 不上报",
                      file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="cast 经济 advisory(introduce_burst+composite+split)")
    ap.add_argument("draft_path", help="cluster 草稿路径(实际读 manifest)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
