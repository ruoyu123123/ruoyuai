#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""location_signature_consistency.py — 跨 cluster 地点感官签名一致性 aggregator
(advisory · cross-cluster · 2026-06-20 · R8 W4 Batch-J · L37)

【缺口】R8 W4 联网调研(Career Authors setting-as-character + Writers Helping Writers
atmospheric layering + Writing Crucible multi-sensory + Connie Jasperson 2024):
重复出现的地点应有签名感官 motif (嗅:腥/霉/檀; 听:钟声/海浪; 触温光). LLM 默认每次
重写感官 → 地点失去 character. 本 aggregator 维护 location_atmosphere_registry +
做 cross-cluster 命中率 advisory。

【做法 · 确定性纯规则】:
  1. 维护 _数据库/location_atmosphere_registry.json
     {location_name: {first_cluster, signature_sensory_motifs: [..], occurrences: N,
                       hit_history: [{cluster_id, hits: [..]}]}}
  2. 命中率 = sum(hit_count_per_recurrence) / (n_recurrence * len(signature_motifs))。
  3. 命中率 < 50% advisory 提示该地点感官签名漂移。
  4. 作者档 location_atmosphere_override.allow_drift_locations=[..] 豁免季节/灾后等。

【北极星 ⑤ 顾问非法官】code LOCATION_SIGNATURE_DRIFT 绝不进 hard_gate 。
env LOCATION_SIGNATURE_MODE: off / shadow(默认) / active。

CLI:
  python location_signature_consistency.py --project <root> [--scan-cluster <id> --draft <path>]
  --scan-cluster: 增量更新 registry (从该 cluster 草稿抽感官词)
  无 --scan-cluster: 汇总现 registry 做 advisory
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE = "LOCATION_SIGNATURE_DRIFT"

# 五感词典 (示意 · 短保守)
SMELL = re.compile(r"(腥|霉|檀|香|焦|血腥|烟味|药味|花香|海风|腐|臭)")
SOUND = re.compile(r"(钟声|海浪|风声|涛声|鸣|嘶|低吼|喧嚣|滴水|雷鸣)")
TOUCH = re.compile(r"(冷|寒|湿|潮|滚烫|阴凉|刺骨|温润|粗糙|柔软)")
LIGHT = re.compile(r"(昏暗|阴森|刺眼|柔光|月光|烛光|幽光|血色|金光)")


def _mode() -> str:
    m = (os.environ.get("LOCATION_SIGNATURE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _load_json(p, default=None):
    return load_json(p, default=default)


def _registry_path(project_root) -> Path:
    return Path(project_root) / "_数据库" / "location_atmosphere_registry.json"


def _extract_sensory_motifs(text: str) -> dict:
    return {
        "smell": sorted(set(SMELL.findall(text)))[:5],
        "sound": sorted(set(SOUND.findall(text)))[:5],
        "touch": sorted(set(TOUCH.findall(text)))[:5],
        "light": sorted(set(LIGHT.findall(text)))[:5],
    }


def _detect_locations(project_root) -> list:
    """从 _数据库/地图.json / 世界观.json 抽地名词表。容忍 dict / list 根 schema。"""
    out = set()
    db = Path(project_root) / "_数据库"
    for fname in ("地图.json", "世界观.json", "地点池.json"):
        obj = _load_json(db / fname)
        if obj is None:
            continue
        # 根 = list (直接是地点列表)
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("location")
                    if isinstance(name, str) and 2 <= len(name) <= 12:
                        out.add(name)
                elif isinstance(item, str) and 2 <= len(item) <= 12:
                    out.add(item)
            continue
        # 根 = dict
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict) and v.get("type") in {"location", "place"}:
                    out.add(k)
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            name = item.get("name") or item.get("location")
                            if isinstance(name, str) and 2 <= len(name) <= 12:
                                out.add(name)
                        elif isinstance(item, str) and 2 <= len(item) <= 12:
                            out.add(item)
    return sorted(out)


def update_registry(project_root, cluster_id, draft_text) -> dict:
    reg_path = _registry_path(project_root)
    reg = _load_json(reg_path, {}) or {}
    if not isinstance(reg, dict):
        reg = {}
    locations = _detect_locations(project_root)
    for loc in locations:
        if loc not in draft_text:
            continue
        # 抽该地点 ±200 字窗口的感官词
        idx = draft_text.find(loc)
        window = draft_text[max(0, idx - 200):min(len(draft_text), idx + 200)]
        motifs = _extract_sensory_motifs(window)
        entry = reg.get(loc) or {
            "first_cluster": cluster_id,
            "signature_sensory_motifs": [],
            "occurrences": 0, "hit_history": []}
        # 首次出现 = signature
        if entry["occurrences"] == 0:
            sig = []
            for cat, words in motifs.items():
                for w in words:
                    sig.append(f"{cat}:{w}")
            entry["signature_sensory_motifs"] = sig[:8]
        # 记 hit
        hits = []
        for cat, words in motifs.items():
            for w in words:
                token = f"{cat}:{w}"
                if token in entry["signature_sensory_motifs"]:
                    hits.append(token)
        entry["occurrences"] += 1
        entry["hit_history"].append({"cluster_id": cluster_id, "hits": hits})
        reg[loc] = entry
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return reg


def aggregate(project_root) -> dict:
    mode = _mode()
    out = {"scanner": "location_signature_consistency", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    if not project_root or not Path(project_root).exists():
        out["note"] = "无项目根·跳过"
        return out

    reg = _load_json(_registry_path(project_root), {}) or {}
    if not isinstance(reg, dict) or not reg:
        out["note"] = "无 location_atmosphere_registry·跳过"
        return out

    ap = _load_json(Path(project_root) / "_数据库" / "作者风格.json") or {}
    allow_drift = []
    if isinstance(ap, dict):
        ovr = ap.get("location_atmosphere_override") or {}
        if isinstance(ovr, dict):
            allow_drift = ovr.get("allow_drift_locations") or []

    issues = []
    per_location = []
    for loc, entry in reg.items():
        if not isinstance(entry, dict):
            continue
        if loc in allow_drift:
            continue
        sig = entry.get("signature_sensory_motifs") or []
        history = entry.get("hit_history") or []
        if len(history) < 3 or not sig:
            continue
        # 命中率 = sum(hit count per recurrence) / (recurrences * len(sig))
        recurrences = history[1:]  # 去首次
        if not recurrences or not sig:
            continue
        total_possible = len(recurrences) * len(sig)
        actual_hits = sum(len(h.get("hits") or []) for h in recurrences)
        rate = round(actual_hits / total_possible, 3) if total_possible > 0 else 0
        per_location.append({"location": loc, "rate": rate,
                              "recurrences": len(recurrences)})
        if rate < 0.5:
            issues.append(f"{loc}: 命中率 {rate} < 50% ({len(recurrences)} 次重现)")

    out["location_count"] = len(reg)
    out["per_location"] = per_location

    msg = " · ".join(issues) if issues else None
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "location_signature", "severity": "minor",
                "message": msg, "issues": issues,
                "_doc": ("地点感官签名是工艺 advisory · 作者档 allow_drift 豁免 · "
                         "灾后/季节变更合理 · 绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] location_signature_consistency: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="跨 cluster 地点感官签名一致性(advisory · cross-cluster)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--scan-cluster", default=None,
                    help="增量更新 registry: cluster_id")
    ap.add_argument("--draft", default=None,
                    help="搭配 --scan-cluster: 草稿路径")
    args = ap.parse_args()
    if args.scan_cluster and args.draft:
        try:
            draft = Path(args.draft).read_text(encoding="utf-8")
        except OSError as e:
            print(f"[error] 草稿读取失败: {e}", file=sys.stderr)
            sys.exit(2)
        update_registry(args.project, args.scan_cluster, draft)
    report = aggregate(args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
