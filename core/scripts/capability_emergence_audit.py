#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capability_emergence_audit.py — 能力/技艺首现无建立 advisory
(advisory · cluster · 2026-06-20 R12 W6 Batch-Q · P2 · shadow)

【缺口】arxiv 2603.05890 ConStory-Bench 19 子类 + arxiv 2506.05939 Entity-Event
KG + arxiv 2311.09648 Event Causality EMNLP 报告：
LLM 写主角能力/技艺/法宝/招式时，"首次使用"前常缺 acquisition 锚点
（学习/拜师/捡到/突破等），构成"能力凭空冒出"现象。

【做法 · 确定性轻量规则（不依赖 LLM）】
  1. 读 _数据库/capability_ledger.json 取本 cluster 已注册能力 entries（schema:
     {capability_id, name, acquisition_event, acquired_at_cluster}）。无 → 跳过。
  2. 正文按段扫"首次使用"signal: 一段中含 capability.name 且段内含使用动词
     （施展/释放/凝聚/挥出/打出/吐出/激发/催动/觉醒/突破）。
  3. 若同一能力在本 cluster 第一次出现的使用段之前（含历史 cluster），无 acquisition
     锚点（acquired_at_cluster 为空 或 acquisition_event 为空）→
     CAPABILITY_EMERGENCE_UNGROUNDED advisory。
  4. 同时可豁免：能力 entry 标记 inherent=true（与生俱来）→ 不报。

【北极星】②④⑤ cluster 视野·作者档第一权威·advisory shadow·绝不 hard_gate
CAPABILITY_EMERGENCE_UNGROUNDED 绝不进 audit_hub.HARD_GATE_CODES。

env CAPABILITY_EMERGENCE_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CAPABILITY_EMERGENCE_UNGROUNDED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 使用动词字典（修真/玄幻/武侠/异能·占位）
_USE_VERBS = re.compile(
    r"(施展|施放|释放|催动|凝聚|凝结|挥出|打出|吐出|激发|觉醒|突破|引动|"
    r"调动|引爆|爆发|展开|开启|启动|施法|布阵|结印|引动|搭弓|出手|出招)")


def _mode() -> str:
    m = (os.environ.get("CAPABILITY_EMERGENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_ledger(project_root):
    """读 capability_ledger.json → list of entries。无 → []。"""
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "capability_ledger.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(obj, dict):
        entries = obj.get("entries") or obj.get("capabilities") or []
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
    if isinstance(obj, list):
        return [e for e in obj if isinstance(e, dict)]
    return []


def find_first_use(text: str, capability_name: str):
    """返回首次"使用段"位置；找不到 → None。"""
    if not capability_name:
        return None
    paragraphs = text.split("\n")
    pos = 0
    for para in paragraphs:
        if capability_name in para and _USE_VERBS.search(para):
            return {"pos": pos, "snippet": para.strip()[:80]}
        pos += len(para) + 1
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "capability_emergence_audit", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "violations": [],
           "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    if _cjk_count(text) < 500:
        out["note"] = "草稿太短·跳过"
        return out

    ledger = _read_ledger(project_root)
    if not ledger:
        out["note"] = "capability_ledger.json 不存在或为空·跳过（占位待蒸馏注入）"
        return out

    findings = []
    out["ledger_size"] = len(ledger)
    for entry in ledger:
        name = entry.get("name") or entry.get("capability_id")
        if not name:
            continue
        first_use = find_first_use(text, name)
        if not first_use:
            continue  # 本 cluster 没用过
        # 已有 acquisition 锚点
        acq_evt = entry.get("acquisition_event")
        acq_cluster = entry.get("acquired_at_cluster")
        inherent = bool(entry.get("inherent"))
        if inherent:
            continue
        if acq_evt and acq_cluster:
            continue
        findings.append({
            "capability": name, "first_use_pos": first_use["pos"],
            "snippet": first_use["snippet"],
            "missing_acquisition_event": not acq_evt,
            "missing_acquired_at_cluster": not acq_cluster})
    out["findings"] = findings
    out["ungrounded_count"] = len(findings)

    if findings:
        sample = "、".join(f["capability"] for f in findings[:3])
        msg = (f"能力首现无建立锚点：{len(findings)} 项（{sample}）·"
               f"建议补 acquisition_event / acquired_at_cluster 或标 inherent=true")
        if mode == "active":
            for f in findings:
                out["violations"].append({
                    "kind": "capability_emergence", "severity": "minor",
                    "code": ISSUE_CODE,
                    "message": f"能力 {f['capability']} 首次使用前无 acquisition 锚点",
                    "capability": f["capability"], "snippet": f["snippet"],
                    "_doc": "ConStory-Bench 19 子类·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] capability_emergence: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="能力首现无建立 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
