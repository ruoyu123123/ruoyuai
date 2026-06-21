#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dramatic_irony_gap_scanner.py — 读者信念账本·戏剧反讽缺口·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】reader belief ledger 双层架构：
  - 读者信念账本.json：读者作为隐含外部 ToM 持有人的 fact 集
  - 角色信念账本.json：每角色 fact 集（已存在 character_belief_ledger_scanner）

  戏剧反讽 = reader_known ∩ character_unknown ≠ ∅
  - 为空 → 无反讽（叙事张力流失）advisory
  - 过大且持续未释放（>N 个 fact 跨越≥3 cluster 无 reveal）→ 反讽张力浪费

【与既有 scanner 显式去重】
  - dramatic_irony_scanner：TELL 词（saying_doing/style_fact 反讽信号）
    本 scanner = 信念账本结构差·正交（一个查信号·一个查账本差集）
  - character_belief_ledger_scanner：character 越权知识
    本 scanner = 读者 vs character 信念差·正交

【做法 · 确定性占位（零 LLM）】
  1. 读 _数据库/读者信念账本.json（占位 schema 自动初始化）
     - reader_known: [fact_ref]
     - reveal_cluster: {fact_ref: cluster_id}（首次对读者展示的 cluster）
  2. 读 _数据库/角色信念账本.json（占位 schema 自动初始化）
     - by_character: {name: {known: [fact_ref], reveal_cluster: {f: c}}}
  3. 计算 dramatic_irony_gap：
     - empty = (reader_known - all_character_known) 为空？ → 无反讽 advisory
     - stale = reader_known 中跨度≥3 cluster 未被任何 character 知晓 → 浪费 advisory
  4. 草稿内 fact_ref 命中累积 → reader_known 自动滚动（占位简化）

【北极星⑤】顾问非法官·全 advisory·env DRAMATIC_IRONY_GAP_MODE
  DRAMATIC_IRONY_GAP_* 绝不进 audit_hub.HARD_GATE_CODES。

用法: python dramatic_irony_gap_scanner.py <draft> [--project <root>] [--cluster <id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_EMPTY = "DRAMATIC_IRONY_GAP_EMPTY"
ISSUE_STALE = "DRAMATIC_IRONY_GAP_STALE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 默认占位 fact_ref 词典（与 character_belief_ledger 对齐）
DEFAULT_FACT_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "facts": [
        "身世", "真名", "真相", "秘密", "暗号", "暗记",
        "下落", "藏身", "底细", "出身", "血脉", "宝藏",
        "阴谋", "计划", "病情", "婚事",
    ],
}

STALE_THRESHOLD_CLUSTERS = 3


def _mode() -> str:
    m = (os.environ.get("DRAMATIC_IRONY_GAP_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _reader_ledger_skeleton() -> dict:
    return {
        "_schema_version": "1.0",
        "_placeholder": True,
        "reader_known": [],
        "reveal_cluster": {},
        "cluster_history": [],
    }


def _character_ledger_skeleton() -> dict:
    return {
        "_schema_version": "1.0",
        "_placeholder": True,
        "by_character": {},
    }


def _load_or_init_reader_ledger(project_root) -> dict:
    if not project_root:
        return _reader_ledger_skeleton()
    p = Path(project_root) / "_数据库" / "读者信念账本.json"
    if not p.exists():
        return _reader_ledger_skeleton()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    return _reader_ledger_skeleton()


def _load_or_init_character_ledger(project_root) -> dict:
    if not project_root:
        return _character_ledger_skeleton()
    p = Path(project_root) / "_数据库" / "角色信念账本.json"
    if not p.exists():
        return _character_ledger_skeleton()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    return _character_ledger_skeleton()


def _all_character_known(char_ledger: dict) -> set:
    out: set = set()
    bc = char_ledger.get("by_character") or {}
    if not isinstance(bc, dict):
        return out
    for _, info in bc.items():
        if not isinstance(info, dict):
            continue
        known = info.get("known") or []
        if isinstance(known, list):
            for k in known:
                if isinstance(k, str):
                    out.add(k)
    return out


def _harvest_facts(text: str, fact_refs: list) -> list:
    """草稿命中 fact_ref 累积（占位简化：子串命中即视为读者已知）"""
    return [f for f in fact_refs if f in text]


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {"scanner": "dramatic_irony_gap", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
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

    reader_led = _load_or_init_reader_ledger(project_root)
    char_led = _load_or_init_character_ledger(project_root)

    fact_refs = list(DEFAULT_FACT_LEXICON_PLACEHOLDER["facts"])

    # 把本 cluster 新发现的 fact 加进 reader_known（占位累加）
    cur_facts = _harvest_facts(text, fact_refs)
    reader_known = set(reader_led.get("reader_known") or [])
    new_facts = [f for f in cur_facts if f not in reader_known]
    reader_known.update(new_facts)
    out["reader_known_count"] = len(reader_known)
    out["new_facts_this_cluster"] = new_facts

    # reveal_cluster 时间戳
    reveal_cluster = dict(reader_led.get("reveal_cluster") or {})
    cid = cluster_id or "current"
    for f in new_facts:
        reveal_cluster.setdefault(f, cid)

    all_char_known = _all_character_known(char_led)
    irony_gap = reader_known - all_char_known
    out["irony_gap_count"] = len(irony_gap)
    out["irony_gap_samples"] = sorted(list(irony_gap))[:10]

    # ① empty：reader 已知 >0 但 gap 为 0 → 无反讽
    if reader_known and not irony_gap:
        msg = "读者已知全部已被角色知晓·无戏剧反讽张力（reader_known 与 character_known 完全重合）"
        if mode == "active":
            out["violations"].append({
                "kind": "irony_gap_empty", "severity": "minor",
                "code": ISSUE_EMPTY, "message": msg,
                "_doc": "reader_known∩character_unknown=0·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dramatic_irony_gap: {msg} — 不上报", file=sys.stderr)

    # ② stale：reader 已知跨度≥STALE_THRESHOLD_CLUSTERS 未释放
    cluster_history = list(reader_led.get("cluster_history") or [])
    if cid not in cluster_history:
        cluster_history.append(cid)
    out["cluster_history"] = cluster_history
    stale_facts = []
    if len(cluster_history) >= STALE_THRESHOLD_CLUSTERS:
        cutoff = cluster_history[-STALE_THRESHOLD_CLUSTERS]
        for f in irony_gap:
            rc = reveal_cluster.get(f)
            if rc and rc in cluster_history:
                idx = cluster_history.index(rc)
                cutoff_idx = cluster_history.index(cutoff)
                if idx < cutoff_idx:
                    stale_facts.append(f)
    out["stale_facts"] = stale_facts
    if stale_facts:
        msg2 = (f"读者已知 fact 跨越≥{STALE_THRESHOLD_CLUSTERS} cluster 仍未被任何角色知晓·"
                f"反讽张力浪费 {len(stale_facts)} 处：" + "·".join(stale_facts[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "irony_gap_stale", "severity": "minor",
                "code": ISSUE_STALE, "message": msg2,
                "stale_facts": stale_facts,
                "_doc": "未释放反讽 fact 持续累积·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = (out["warning"] or "") + " | " + msg2 if out["warning"] else msg2
        else:
            print(f"[SHADOW] dramatic_irony_gap: {msg2} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    out["reader_ledger_updated"] = {
        "reader_known": sorted(reader_known),
        "reveal_cluster": reveal_cluster,
        "cluster_history": cluster_history,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="读者信念账本戏剧反讽缺口·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
