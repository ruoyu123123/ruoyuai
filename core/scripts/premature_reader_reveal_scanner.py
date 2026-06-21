#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""premature_reader_reveal_scanner.py — 读者尚未被 show 的 fact 角色却当公知·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】reader belief ledger 反向穿帮：
  叙事方向：角色当公知信息使用某 fact，但该 fact 在读者信念账本里从未被 show
  → 读者一脸懵，等于反向越权（与 character_knowledge_leak 互补：那个查 character
  越知道·这个查 reader 没被 show 却被当成『大家都知道』的事实）。

【与既有 scanner 显式去重】
  - character_belief_ledger_scanner (CHARACTER_KNOWLEDGE_LEAK)
    本 scanner = 读者侧反向（角色当公知·读者却没被告知）·正交
  - dramatic_irony_gap_scanner
    那个查整体差集结构·本 scanner 查具体『当公知使用』语言信号

【做法 · 确定性占位（零 LLM）】
  1. 读 _数据库/读者信念账本.json 的 reader_known
  2. 当公知信号词典占位：「众所周知/大家都知道/谁都明白/不用说也知道/向来如此」
     + 后接 fact_ref（占位词典或 locked_fact.json）
  3. 信号词后 ±30 CJK 命中 fact_ref，但 fact_ref ∉ reader_known
     → PREMATURE_READER_REVEAL advisory

【北极星⑤】顾问非法官·全 advisory·env PREMATURE_READER_REVEAL_MODE
  PREMATURE_READER_REVEAL 绝不进 audit_hub.HARD_GATE_CODES。

用法: python premature_reader_reveal_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PREMATURE_READER_REVEAL"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 当公知信号词典占位
COMMON_KNOWLEDGE_SIGNALS_PLACEHOLDER = {
    "_placeholder": True,
    "tokens": [
        "众所周知", "大家都知道", "谁都明白", "不用说也知道",
        "向来如此", "人人都晓得", "天下皆知", "无人不知",
    ],
}

# fact_ref 占位（与其他账本 scanner 对齐）
DEFAULT_FACT_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "facts": [
        "身世", "真名", "真相", "秘密", "暗号", "暗记",
        "下落", "藏身", "底细", "出身", "血脉", "宝藏",
        "阴谋", "计划", "病情", "婚事",
    ],
}


def _mode() -> str:
    m = (os.environ.get("PREMATURE_READER_REVEAL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_reader_known(project_root) -> set:
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "读者信念账本.json"
    if not p.exists():
        return set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            rk = obj.get("reader_known") or []
            if isinstance(rk, list):
                return {x for x in rk if isinstance(x, str)}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _load_fact_refs(project_root) -> list:
    if project_root:
        p = Path(project_root) / "_数据库" / "locked_fact.json"
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(obj, dict):
                    items = obj.get("facts") or obj.get("items") or []
                    refs = []
                    for it in items:
                        if isinstance(it, dict) and it.get("key"):
                            refs.append(str(it["key"]))
                        elif isinstance(it, str):
                            refs.append(it)
                    if refs:
                        return refs
            except (OSError, json.JSONDecodeError):
                pass
    return list(DEFAULT_FACT_LEXICON_PLACEHOLDER["facts"])


def _detect_premature(text: str, reader_known: set, fact_refs: list) -> list:
    """信号词后 ±30 CJK 命中 fact_ref·fact_ref ∉ reader_known → 报警"""
    out = []
    signals = COMMON_KNOWLEDGE_SIGNALS_PLACEHOLDER["tokens"]
    for sig in signals:
        for m in re.finditer(re.escape(sig), text):
            lo = max(0, m.start() - 30)
            hi = min(len(text), m.end() + 30)
            ctx = text[lo:hi]
            for f in fact_refs:
                if f in ctx and f not in reader_known:
                    out.append({
                        "signal": sig, "fact_ref": f,
                        "context": ctx.replace("\n", " ")[:80],
                        "char_offset": m.start(),
                    })
    return out


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "premature_reader_reveal", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "violation_count": 0, "samples": []}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    reader_known = _load_reader_known(project_root)
    fact_refs = _load_fact_refs(project_root)
    out["reader_known_count"] = len(reader_known)
    out["fact_ref_count"] = len(fact_refs)

    viols = _detect_premature(text, reader_known, fact_refs)
    out["violation_count"] = len(viols)
    out["samples"] = viols[:5]

    if viols:
        msg = (f"角色当公知使用读者未被 show 的 fact {len(viols)} 处："
               + "·".join(f"{v['signal']}+{v['fact_ref']}" for v in viols[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "premature_reader_reveal", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "samples": viols[:3],
                "_doc": "读者反向越权·补叙/外人视角可豁免·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] premature_reader_reveal: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="角色当公知用读者未被 show fact·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
