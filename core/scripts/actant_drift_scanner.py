#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""actant_drift_scanner.py — Greimas 六 actant 角色功能漂移检测
(advisory · cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (Greimas《Sémantique structurale》1966) 提出六 actant 模型：
subject(主体)/object(客体)/sender(发送者)/receiver(接收者)/helper(帮手)/opponent(对手)。
此前全系统【角色功能层零检测】——同一角色在不同 cluster 间从 helper 翻成 opponent
而无背叛/转化铺垫=人物功能漂移 bug；单角色一人占 ≥3 actant 位=角色过载(stuffed character)；
关键 actant 位 (subject/opponent) 空缺=故事缺动力源。

【做法 · 确定性零依赖（依赖 _数据库/cluster_actant_ledger.json 历史台账）】：
  1. 读 manifest.cluster_actant_state（六位 → 角色名/None 映射 · cluster_001 可空）。
  2. 读 _数据库/cluster_actant_ledger.json（历史每个 cluster 的 actant 派分）+ 作者档
     `actant_signature.allow_multi_role_hero` 豁免清单（multi-role 英雄豁免）。
  3. 三类 advisory:
     · ACTANT_DRIFT_NO_PIVOT: 同角色历史 cluster 是 helper, 本 cluster 翻 opponent (或反向)
       且未在 brief/storyboard 标 `pivot_event`(背叛/觉醒/反水)。
     · ACTANT_VACANCY: subject 或 opponent 在 active cluster 为空（无主体或无对手=故事死水）。
     · ACTANT_OVERLOADED: 单角色占 ≥3 actant 位且不在 multi_role_hero 豁免清单。
  4. 同步写回 ledger.json（仅 active 模式）供下个 cluster 比对。

【北极星② / ⑤ 顾问非法官】六 actant 是分析框架·作者可声明无敌流/独狼/解构反派故事·全 advisory，
  code ACTANT_DRIFT_* **绝不进 audit_hub.HARD_GATE_CODES**。
  env ACTANT_DRIFT_MODE: off / shadow(默认) / active。

用法：python actant_drift_scanner.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE_DRIFT = "ACTANT_DRIFT_NO_PIVOT"
ISSUE_CODE_VACANCY = "ACTANT_VACANCY"
ISSUE_CODE_OVERLOAD = "ACTANT_OVERLOADED"

ACTANT_POSITIONS = ("subject", "object", "sender", "receiver", "helper", "opponent")
# helper↔opponent 翻转最常见的漂移信号
_ANTAGONIST_PAIR = {("helper", "opponent"), ("opponent", "helper")}


def _mode() -> str:
    m = (os.environ.get("ACTANT_DRIFT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(path: Path):
    return load_json(path)


def _read_manifest(manifest_path):
    if not manifest_path:
        return {}
    obj = _read_json(Path(manifest_path))
    return obj if isinstance(obj, dict) else {}


def _read_author_signature(project_root):
    if not project_root:
        return {}
    ap = Path(project_root) / "_数据库" / "作者风格.json"
    if not ap.exists():
        return {}
    obj = _read_json(ap)
    if not isinstance(obj, dict):
        return {}
    sig = obj.get("actant_signature")
    return sig if isinstance(sig, dict) else {}


def _read_ledger(project_root):
    if not project_root:
        return {"clusters": []}
    p = Path(project_root) / "_数据库" / "cluster_actant_ledger.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return {"clusters": []}
    if not isinstance(obj.get("clusters"), list):
        obj["clusters"] = []
    return obj


def _write_ledger(project_root, ledger):
    if not project_root:
        return
    db = Path(project_root) / "_数据库"
    if not db.exists():
        return
    p = db / "cluster_actant_ledger.json"
    try:
        p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


def _current_assignments(manifest: dict) -> dict:
    """从 manifest.cluster_actant_state 读六位 → 角色名（None/缺失→None）。"""
    raw = manifest.get("cluster_actant_state") or {}
    if not isinstance(raw, dict):
        return {p: None for p in ACTANT_POSITIONS}
    out = {}
    for pos in ACTANT_POSITIONS:
        v = raw.get(pos)
        if isinstance(v, str) and v.strip():
            out[pos] = v.strip()
        else:
            out[pos] = None
    return out


def _pivot_events(manifest: dict) -> set:
    """读 brief/storyboard 显式 pivot_event 标记（背叛/觉醒/反水/转化）。"""
    pivots = set()
    for key in ("pivot_events", "scope_summary_pivots"):
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


def _previous_positions(ledger, cluster_id):
    """所有早于当前 cluster_id 的 actant 派分聚合（角色 → 上次位）。"""
    out = {}
    for rec in ledger.get("clusters", []):
        if not isinstance(rec, dict):
            continue
        if str(rec.get("cluster_id") or "") == str(cluster_id):
            continue
        for pos, name in (rec.get("assignments") or {}).items():
            if name and pos in ACTANT_POSITIONS:
                out[name] = pos
    return out


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "actant_drift", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out

    manifest = _read_manifest(manifest_path)
    cluster_id = (manifest.get("cluster_id") or manifest.get("cluster_key")
                  or "cluster_001")
    assignments = _current_assignments(manifest)
    out["cluster_id"] = cluster_id
    out["assignments"] = assignments

    # 全空（cluster_001 brief 未填）→ 不判 vacancy 单跳过
    if all(v is None for v in assignments.values()):
        out["note"] = "manifest 无 cluster_actant_state·跳过(北极星②:作者未标 actant 不擅判)"
        return out

    sig = _read_author_signature(project_root)
    multi_role_hero = set(sig.get("allow_multi_role_hero") or [])
    pivots = _pivot_events(manifest)

    ledger = _read_ledger(project_root)
    prev = _previous_positions(ledger, cluster_id)

    violations = []

    # ① 漂移检测（helper↔opponent 无 pivot）
    drifts = []
    for pos, name in assignments.items():
        if not name:
            continue
        prev_pos = prev.get(name)
        if prev_pos and prev_pos != pos:
            if (prev_pos, pos) in _ANTAGONIST_PAIR and name not in pivots:
                drifts.append({"character": name, "from": prev_pos,
                               "to": pos, "had_pivot": False})
    if drifts:
        msg = ("·".join(f"{d['character']} {d['from']}→{d['to']}"
                          for d in drifts[:3]))
        violations.append({"code": ISSUE_CODE_DRIFT, "kind": "actant_drift",
                           "severity": "minor",
                           "message": f"角色功能漂移无背叛/觉醒铺垫: {msg}",
                           "drifts": drifts,
                           "_doc": "advisory·作者可声明 pivot_events 豁免"})

    # ② 关键位空缺
    vacancies = [p for p in ("subject", "opponent") if assignments.get(p) is None]
    if vacancies:
        violations.append({"code": ISSUE_CODE_VACANCY, "kind": "actant_vacancy",
                           "severity": "minor",
                           "message": f"关键 actant 位空缺: {','.join(vacancies)}",
                           "vacancies": vacancies,
                           "_doc": "advisory·主体/对手缺失=故事缺动力源"})

    # ③ 过载（≥3 位且不在豁免清单）
    role_count = {}
    for pos, name in assignments.items():
        if name:
            role_count[name] = role_count.get(name, 0) + 1
    overloaded = [(n, c) for n, c in role_count.items()
                  if c >= 3 and n not in multi_role_hero]
    if overloaded:
        names = "·".join(f"{n}×{c}" for n, c in overloaded[:3])
        violations.append({"code": ISSUE_CODE_OVERLOAD, "kind": "actant_overload",
                           "severity": "minor",
                           "message": f"单角色 actant 过载: {names}",
                           "overloaded": [{"character": n, "count": c}
                                          for n, c in overloaded],
                           "_doc": "advisory·multi_role_hero 可豁免"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] actant_drift: {v['message']} — 不上报",
                      file=sys.stderr)

    # 写回 ledger（仅 active·shadow 不污染）
    if mode == "active":
        clusters = [r for r in ledger.get("clusters", [])
                    if not (isinstance(r, dict)
                            and str(r.get("cluster_id") or "") == str(cluster_id))]
        clusters.append({"cluster_id": cluster_id,
                         "assignments": assignments})
        ledger["clusters"] = clusters
        _write_ledger(project_root, ledger)

    return out


def main():
    ap = argparse.ArgumentParser(description="Greimas 六 actant 漂移检测(advisory)")
    ap.add_argument("draft_path", help="cluster 草稿路径(本 scanner 实际读 manifest)")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
