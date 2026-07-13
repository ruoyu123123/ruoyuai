#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""author_signature_preservation.py — 作者签名 slot 防篡改 · R24 W12 Batch-KK · P1

【缺口 · AGI 协作 · 不可篡改人手锚段】
scene_storyboard 加 author_signature_slots[{slot_id, text, preserve_policy,
anchor_hint}]·build_manifest 注入 MUST PRESERVE EXACTLY 指令·writer 在
有作者基线 / 重要金句段时必须**逐字保留**或**标点级近原文**。本扫描在
草稿落地后做 fuzzy match 阈值闸：
  · preserve_policy = "verbatim"           → Levenshtein 距 ≤ 5%
  · preserve_policy = "near_verbatim_punct_only" → 仅标点差异 (剥标点后等价)
  · 失配 → AUTHOR_SIGNATURE_MISMATCH advisory

【做法 · 确定性 · 零 LLM·零联网】
  · 读 事件簇.json.clusters[cluster_key].scene_storyboard[*].author_signature_slots
  · 对每 slot 在草稿中扫窗（窗长 = len(text) * 2.5 滑动 char）
  · 选最低 Lev 距 / 最大相似度窗口
  · 阈值 = preserve_policy 决定（verbatim 5% / near_verbatim 标点剥除后等价）
  · slot 未在草稿中找到 → AUTHOR_SIGNATURE_NOT_PLACED
  · 全部 OK → AUTHOR_SIGNATURE_OK（info）

【三 advisory】
  · AUTHOR_SIGNATURE_MISMATCH       — slot 出现但相似度 < 阈值
  · AUTHOR_SIGNATURE_NOT_PLACED     — slot 完全缺失（policy=verbatim 时 minor·否则 info）
  · AUTHOR_SIGNATURE_OK             — 全 slot 兑现（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  AUTHOR_SIGNATURE_* 绝不进 audit_hub.HARD_GATE_CODES。

env AUTHOR_SIGNATURE_MODE: off / shadow（默认） / active
用法: python author_signature_preservation.py <draft> --project <root> --cluster <key>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402 · 章号⇄cluster_id 唯一权威反查（北极星①·禁 endswith 模糊匹配）

ISSUE_CODE_MISMATCH = "AUTHOR_SIGNATURE_MISMATCH"
ISSUE_CODE_NOT_PLACED = "AUTHOR_SIGNATURE_NOT_PLACED"
ISSUE_CODE_OK = "AUTHOR_SIGNATURE_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

VERBATIM_LEV_RATIO_MAX = 0.05
NEAR_VERBATIM_PUNCT_RE = re.compile(
    r"[，。！？、；：「」『』" + "“”‘’" + r"…—《》〈〉,.!?;:\"'()（）·\s]+"
)

_VALID_POLICIES = ("verbatim", "near_verbatim_punct_only")


def _mode() -> str:
    m = (os.environ.get("AUTHOR_SIGNATURE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _strip_punct(text: str) -> str:
    return NEAR_VERBATIM_PUNCT_RE.sub("", text)


def _lev_distance(a: str, b: str) -> int:
    """标准 O(len(a)*len(b)) Levenshtein·占位实现·签名段 ≤ 200 char 性能足够。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                cur[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev = cur
    return prev[-1]


def _find_best_window(draft: str, signature: str, window_ratio: float = 2.5) -> tuple[int, int, str, float]:
    """在 draft 中滑窗找最小 Lev 距窗口。
    返回 (start, end, text, normalized_ratio)。
    normalized_ratio = lev_dist / max(len(signature), 1)。
    """
    if not signature:
        return -1, -1, "", 0.0
    if not draft:
        return -1, -1, "", 1.0
    sig_len = len(signature)
    # 候选起点 = signature 首字在 draft 的所有出现位置（剪枝）
    first = signature[0]
    starts: list[int] = []
    i = 0
    while True:
        idx = draft.find(first, i)
        if idx == -1:
            break
        starts.append(idx)
        i = idx + 1
    if not starts:
        step = max(1, sig_len // 4)
        starts = list(range(0, max(1, len(draft) - sig_len + 1), step))
    # 对每个候选起点试 3 个 cut length（sig 长度本身 + ±20%）找 Lev 最小
    cut_deltas = [0, max(sig_len // 5, 1), -max(sig_len // 5, 1)]
    best = (-1, -1, "", 1.0)
    for s in starts:
        for delta in cut_deltas:
            cut = sig_len + delta
            if cut <= 0:
                continue
            e = min(s + cut, len(draft))
            win = draft[s:e]
            d = _lev_distance(win, signature)
            ratio = d / max(sig_len, 1)
            if ratio < best[3]:
                # 在 best 里保留更大的窗口 (sig_len * window_ratio) 供下游 inspection
                disp_e = min(s + max(int(sig_len * window_ratio), sig_len + 4),
                             len(draft))
                best = (s, disp_e, draft[s:disp_e], ratio)
            if ratio == 0.0:
                return best
    return best


def _check_slot(draft: str, slot: dict) -> dict:
    text = (slot.get("text") or "").strip()
    policy = (slot.get("preserve_policy") or "verbatim").strip().lower()
    slot_id = slot.get("slot_id") or "unnamed"
    if policy not in _VALID_POLICIES:
        policy = "verbatim"
    rec = {
        "slot_id": slot_id,
        "text_len": len(text),
        "policy": policy,
        "found": False,
        "ratio": 1.0,
        "verdict": "miss",
    }
    if not text:
        rec["verdict"] = "empty"
        return rec
    # near_verbatim_punct_only：先剥标点找 strip 等价段
    if policy == "near_verbatim_punct_only":
        stripped_sig = _strip_punct(text)
        stripped_draft = _strip_punct(draft)
        if stripped_sig and stripped_sig in stripped_draft:
            rec["found"] = True
            rec["ratio"] = 0.0
            rec["verdict"] = "ok_punct_only"
            return rec
    # verbatim 路径 + fallback
    if text in draft:
        rec["found"] = True
        rec["ratio"] = 0.0
        rec["verdict"] = "ok_verbatim"
        return rec
    # fuzzy
    s, e, win, ratio = _find_best_window(draft, text)
    rec["found"] = (ratio < 1.0)
    rec["ratio"] = round(ratio, 4)
    rec["best_window"] = win[:200] if win else ""
    if policy == "verbatim":
        if ratio <= VERBATIM_LEV_RATIO_MAX:
            rec["verdict"] = "ok_fuzzy"
        elif rec["found"]:
            rec["verdict"] = "mismatch"
        else:
            rec["verdict"] = "miss"
    else:  # near_verbatim_punct_only
        # 剥标点后再算 Lev
        s_text = _strip_punct(text)
        s_win = _strip_punct(win) if win else ""
        s_ratio = _lev_distance(s_win, s_text) / max(len(s_text), 1)
        rec["stripped_ratio"] = round(s_ratio, 4)
        if s_ratio <= VERBATIM_LEV_RATIO_MAX:
            rec["verdict"] = "ok_stripped"
        elif rec["found"]:
            rec["verdict"] = "mismatch"
        else:
            rec["verdict"] = "miss"
    return rec


def _load_slots(project_root, cluster_key: str) -> list[dict]:
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "事件簇.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    target = cl.normalize_cluster_id(cluster_key)
    for c in data.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("cluster_id", "")
        if target and cl.normalize_cluster_id(cid) == target:  # 归一精确比对·禁 endswith 模糊
            slots = []
            # storyboard 级 slot
            for sb in c.get("scene_storyboard", []) or []:
                if not isinstance(sb, dict):
                    continue
                for s in sb.get("author_signature_slots", []) or []:
                    if isinstance(s, dict):
                        slots.append(s)
            # cluster 顶层 slot（兜底）
            for s in c.get("author_signature_slots", []) or []:
                if isinstance(s, dict):
                    slots.append(s)
            return slots
    return []


def scan(draft_path, project_root, cluster_key) -> dict:
    mode = _mode()
    out = {
        "scanner": "author_signature_preservation",
        "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(raw)

    slots = _load_slots(project_root, cluster_key) if project_root else []
    if not slots:
        out["note"] = "无 author_signature_slots·跳过"
        out["slot_count"] = 0
        return out

    results = []
    mismatch = []
    miss = []
    for slot in slots:
        rec = _check_slot(draft, slot)
        results.append(rec)
        if rec["verdict"] in ("mismatch",):
            mismatch.append(rec)
        elif rec["verdict"] == "miss":
            miss.append(rec)

    out.update({
        "cluster_key": cluster_key,
        "slot_count": len(slots),
        "slot_results": results,
    })

    flags = []
    for rec in mismatch:
        flags.append({
            "code": ISSUE_CODE_MISMATCH,
            "msg": (f"slot[{rec['slot_id']}] policy={rec['policy']}"
                    f"·ratio={rec['ratio']}·near_verbatim 失配"),
            "severity": "minor",
            "slot_id": rec["slot_id"],
            "policy": rec["policy"],
            "ratio": rec["ratio"],
        })
    for rec in miss:
        sev = "minor" if rec["policy"] == "verbatim" else "info"
        flags.append({
            "code": ISSUE_CODE_NOT_PLACED,
            "msg": (f"slot[{rec['slot_id']}] policy={rec['policy']}"
                    f"·未在草稿中找到"),
            "severity": sev,
            "slot_id": rec["slot_id"],
            "policy": rec["policy"],
        })
    if not flags:
        flags.append({
            "code": ISSUE_CODE_OK,
            "msg": f"{len(slots)} 个 slot 全兑现",
            "severity": "info",
        })

    if mode == "active":
        for f in flags:
            v = {
                "kind": "author_signature",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": ("R24 W12 Batch-KK·作者签名 slot 防篡改"
                         "·advisory·绝不 hard_gate"),
            }
            for k in ("slot_id", "policy", "ratio"):
                if k in f:
                    v[k] = f[k]
            out["violations"].append(v)
        any_minor = any(v["severity"] == "minor" for v in out["violations"])
        out["verdict"] = "FAIL_MINOR" if any_minor else "PASS"
        out["warning"] = ("·".join(f["msg"] for f in flags if f["severity"] == "minor")
                          or None)
    elif mode == "shadow":
        minors = [f for f in flags if f["severity"] == "minor"]
        if minors:
            print("[SHADOW] author_signature: "
                  + "·".join(f["msg"] for f in minors)
                  + " — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="作者签名 slot 防篡改 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", required=True, help="cluster_key (e.g. 001)")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
