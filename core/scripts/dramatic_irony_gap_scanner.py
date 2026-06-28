#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dramatic_irony_gap_scanner.py — 戏剧反讽缺口（桌下炸弹：读者知·角色不知）·advisory

【🔴 2026-06-29 孤儿scanner重接线(名字错配·指向真数据源)】
  旧版读 _数据库/读者信念账本.json + _数据库/角色信念账本.json —— 这两个中文文件【零
  producer，永远缺失 → scanner 永远 skip = 死码】。真数据在 character_belief_ledger.json
  （producer: apply_archive.apply_belief_updates·schema 见 subsystem_skeletons._belief_ledger_schema）：
    characters[char_id].known_facts[] = [{fact_id, content, learned_at_cluster, can_speak,
                                          reader_knows, is_red_herring, ...}]
    characters[char_id].unaware_of    = [fact_id ...]
    facts[fact_id] = {content, first_revealed_cluster, subject}

  重接线后激活戏剧反讽检测（只换数据源·检测语义不变）：
    - 角色信念 = characters[cid].known_facts（全角色已知 fact 集）
    - 读者信念 = known_facts[].reader_knows==true 的并集（读者已知 fact 集）
    - 戏剧反讽缺口（桌下炸弹）= 读者已知 ∩ 至少一角色 unaware_of（读者知·角色不知）
      ↑ schema._field_semantics 钉死：「reader_knows=true 且场上某角色 unaware → dramatic
        irony/桌下炸弹 advisory 张力点」

【两条 advisory】
  ① EMPTY：读者已知 fact >0 但全员角色都知道（无人 unaware）→ 无戏剧反讽张力（叙事张力流失）
  ② STALE：读者已知 fact 跨度 ≥STALE_THRESHOLD_CLUSTERS 仍有角色不知（未释放）→ 反讽张力浪费

【与既有 scanner 显式去重】
  - dramatic_irony_scanner：TELL 词（saying_doing/style_fact 反讽信号）·正交（信号 vs 账本差集）
  - character_belief_ledger_scanner：character 越权知识·正交（读者 vs character 信念差）

【北极星⑤】顾问非法官·全 advisory·env DRAMATIC_IRONY_GAP_MODE
  DRAMATIC_IRONY_GAP_* 绝不进 audit_hub.HARD_GATE_CODES。
  默认安全·向后兼容：character_belief_ledger.json 不存在（旧书）→ 优雅 skip（PASS·零上报）。

用法: python dramatic_irony_gap_scanner.py <draft> [--project <root>] [--cluster <id>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_EMPTY = "DRAMATIC_IRONY_GAP_EMPTY"
ISSUE_STALE = "DRAMATIC_IRONY_GAP_STALE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

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


# ──── 🔴 2026-06-29 接真 character_belief_ledger.json（Phase A/B 产）────
def _load_belief_ledger(project_root):
    """读持久化 character_belief_ledger.json。无文件 / 破损 / 无 characters → None
       （旧书 → 优雅 skip·向后兼容·同原 file-not-exist 行为）。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "character_belief_ledger.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("characters"), dict):
        return obj
    return None


def _fact_label(fid, facts_index) -> str:
    """fact_id → 可读短语（facts[fid].content 优先·缺则 fact_id 本身）·仅供展示。"""
    f = facts_index.get(fid) if isinstance(facts_index, dict) else None
    if isinstance(f, dict):
        return str(f.get("content") or fid)
    if isinstance(f, str):
        return f
    return str(fid)


def _reader_known_fact_ids(ledger) -> set:
    """读者信念 = known_facts[].reader_knows==true 的 fact_id 并集。"""
    out: set = set()
    for _cid, cl in (ledger.get("characters") or {}).items():
        if not isinstance(cl, dict):
            continue
        for kf in (cl.get("known_facts") or []):
            if isinstance(kf, dict) and kf.get("reader_knows") is True and kf.get("fact_id"):
                out.add(str(kf["fact_id"]))
    return out


def _all_character_known(ledger) -> set:
    """角色信念 = 全角色 known_facts[].fact_id 并集（任一角色知道的 fact）。"""
    out: set = set()
    for _cid, cl in (ledger.get("characters") or {}).items():
        if not isinstance(cl, dict):
            continue
        for kf in (cl.get("known_facts") or []):
            if isinstance(kf, dict) and kf.get("fact_id"):
                out.add(str(kf["fact_id"]))
    return out


def _all_unaware_fact_ids(ledger) -> set:
    """全角色 unaware_of[] 并集（至少一角色显式不知道的 fact）。"""
    out: set = set()
    for _cid, cl in (ledger.get("characters") or {}).items():
        if not isinstance(cl, dict):
            continue
        for fid in (cl.get("unaware_of") or []):
            if isinstance(fid, str):
                out.add(fid)
    return out


def _cluster_ord(cid):
    """从 'cluster_007' 抓数字序（7）。不可解析 → None。"""
    m = re.search(r"(\d+)", cid or "")
    return int(m.group(1)) if m else None


def _reader_learned_ord(fid, ledger, facts_index):
    """读者首次获知 fact 的 cluster 序数 = 该 fact 各 reader_knows 条目 learned_at_cluster 的最小序；
       缺则退回 facts[fid].first_revealed_cluster。无可解析 → None。"""
    ords = []
    for _cid, cl in (ledger.get("characters") or {}).items():
        if not isinstance(cl, dict):
            continue
        for kf in (cl.get("known_facts") or []):
            if (isinstance(kf, dict) and kf.get("reader_knows") is True
                    and str(kf.get("fact_id")) == fid):
                o = _cluster_ord(kf.get("learned_at_cluster"))
                if o is not None:
                    ords.append(o)
    if ords:
        return min(ords)
    fm = facts_index.get(fid) if isinstance(facts_index, dict) else None
    if isinstance(fm, dict):
        return _cluster_ord(fm.get("first_revealed_cluster"))
    return None


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

    # 🔴 2026-06-29 接真：character_belief_ledger.json 缺失（旧书）→ 优雅 skip（向后兼容）。
    ledger = _load_belief_ledger(project_root)
    if ledger is None:
        out["note"] = "无 character_belief_ledger.json·跳过（向后兼容）"
        out["reader_known_count"] = 0
        out["irony_gap_count"] = 0
        return out

    facts_index = ledger.get("facts") if isinstance(ledger.get("facts"), dict) else {}
    reader_known = _reader_known_fact_ids(ledger)
    all_known = _all_character_known(ledger)
    all_unaware = _all_unaware_fact_ids(ledger)

    # 戏剧反讽缺口（桌下炸弹）= 读者已知 ∩ 至少一角色 unaware（读者知·角色不知）
    irony_gap = reader_known & all_unaware
    out["reader_known_count"] = len(reader_known)
    out["character_known_count"] = len(all_known)
    out["irony_gap_count"] = len(irony_gap)
    out["irony_gap_samples"] = [_fact_label(f, facts_index) for f in sorted(irony_gap)][:10]

    # ① EMPTY：读者已知 >0 但 gap 为 0（无人 unaware）→ 无戏剧反讽张力
    if reader_known and not irony_gap:
        msg = ("读者已知 fact 全员角色都知道（无角色 unaware）·无戏剧反讽张力"
               "（reader_known 与 character_known 重合·桌下无炸弹）")
        if mode == "active":
            out["violations"].append({
                "kind": "irony_gap_empty", "severity": "minor",
                "code": ISSUE_EMPTY, "message": msg,
                "_doc": "读者已知∩角色unaware=0·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dramatic_irony_gap: {msg} — 不上报", file=sys.stderr)

    # ② STALE：读者已知跨度 ≥STALE_THRESHOLD_CLUSTERS 仍有角色不知（未释放）
    cur_ord = _cluster_ord(cluster_id)
    stale_facts = []
    if cur_ord is not None:
        for fid in irony_gap:
            learned = _reader_learned_ord(fid, ledger, facts_index)
            if learned is not None and (cur_ord - learned) >= STALE_THRESHOLD_CLUSTERS:
                stale_facts.append(fid)
    out["stale_facts"] = [_fact_label(f, facts_index) for f in sorted(stale_facts)]
    if stale_facts:
        labels = "·".join(_fact_label(f, facts_index) for f in sorted(stale_facts)[:3])
        msg2 = (f"读者已知 fact 跨越≥{STALE_THRESHOLD_CLUSTERS} cluster 仍有角色不知·"
                f"反讽张力浪费 {len(stale_facts)} 处：" + labels)
        if mode == "active":
            out["violations"].append({
                "kind": "irony_gap_stale", "severity": "minor",
                "code": ISSUE_STALE, "message": msg2,
                "stale_facts": out["stale_facts"],
                "_doc": "未释放反讽 fact 持续累积·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = (out["warning"] or "") + " | " + msg2 if out["warning"] else msg2
        else:
            print(f"[SHADOW] dramatic_irony_gap: {msg2} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="戏剧反讽缺口(桌下炸弹)·advisory·shadow")
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
