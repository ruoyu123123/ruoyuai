#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anadiplosis_scanner.py — 顶真(anadiplosis) advisory shadow · R22 W10 Batch-FF · P1

【缺口】陈望道 38 格之「顶针/顶真」(sentence_i 末 2-4 字 = sentence_i+1 首 2-4 字·首尾接续)
追逐/上升节奏的中文骨架修辞·古典感强·全系统零检测·rhetorical_balance 只查四类分布。

【做法 · 确定性 · 零 LLM/零联网】
  · 句对级扫·相邻句对 sent_i 末 k 字 == sent_i+1 首 k 字(k ∈ 2,3,4 取最长命中)
  · 跳过虚词头尾(避免「的」「了」「也」误判)
  · cluster 草稿统计 anadiplosis_per_kcj 密度
  · 作者档 anadiplosis_per_kcj 第一权威·无 → 兜底 z-band 兜底阈值

【三 advisory】
  · ANADIPLOSIS_DETECTED        — 命中数 ≥ N(便于回看)
  · ANADIPLOSIS_OVER_BASELINE   — 密度 > 作者档 + 2σ·堆叠
  · ANADIPLOSIS_UNDER_BASELINE  — 作者档高基线(>0.5/kCJK) 但本 cluster 0 命中·节奏签名丢失

【与既有 scanner 严格正交】
  · rhetorical_balance        → 四类分布层
  · rhetorical_inventory      → 全格命中清单层
  · zeugma_scanner            → 拈连单格(本者顶针单格)
  · prose_rhythm 等查句首流水账 → 本者查相邻句首尾重叠

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  ANADIPLOSIS_* 绝不进 audit_hub.HARD_GATE_CODES。

env ANADIPLOSIS_MODE: off / shadow(默认) / active
用法: python anadiplosis_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402

ISSUE_CODE_DETECTED = "ANADIPLOSIS_DETECTED"
ISSUE_CODE_OVER = "ANADIPLOSIS_OVER_BASELINE"
ISSUE_CODE_UNDER = "ANADIPLOSIS_UNDER_BASELINE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 句首/句末若以这些字符开始/结尾·剥掉再比对(虚词不算顶真)
_TRIM_FUNCTION_HEADS = set("的了着也就都还又便竟却倒可只把被给从在向")
_TRIM_FUNCTION_TAILS = set("的了着也呢吧吗啊呀哦哈嘛")
_PUNCT_DROP = set("，。！？；：、…—·,.!?;:")

# 顶针候选 k 字范围
K_MIN = 2
K_MAX = 4

DEFAULT_BASELINE = 0.0
DEFAULT_OVER_DELTA = 1.0   # 兜底 over 触发 (/kCJK)
DEFAULT_UNDER_MIN_BASELINE = 0.5
DEFAULT_DETECTED_REPORT_MIN = 1

# 顶针常用接续词（作者档可覆盖·占位顶 10）
DEFAULT_TOP_CONNECTORS = ["然后", "因此", "所以", "于是", "结果", "这样", "由此", "如此", "便是", "正是"]


def _mode() -> str:
    m = (os.environ.get("ANADIPLOSIS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            for key in ("anadiplosis_per_kcj", "anadiplosis_baseline"):
                v = obj.get(key)
                if isinstance(v, (int, float)):
                    return {"baseline": float(v), "source": "author_profile"}
                if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
                    return {"baseline": float(v["mean"]),
                            "std": float(v.get("std", 0)) if isinstance(v.get("std"), (int, float)) else 0.0,
                            "connectors": v.get("top_connectors") if isinstance(v.get("top_connectors"), list) else None,
                            "source": "author_profile"}
    return None


def _trim_for_compare(s: str) -> str:
    """剥首尾标点·空白·让纯 CJK 字符串进入对比"""
    s = s.strip()
    while s and (s[0] in _PUNCT_DROP or s[0].isspace()):
        s = s[1:]
    while s and (s[-1] in _PUNCT_DROP or s[-1].isspace()):
        s = s[:-1]
    return s


def _is_pure_cjk(seg: str) -> bool:
    return bool(seg) and all("一" <= ch <= "鿿" for ch in seg)


def _max_overlap_len(tail: str, head: str) -> int:
    """tail 末 / head 首最长重叠 CJK 长度 ∈ [K_MIN, K_MAX]·无命中返回 0"""
    if not tail or not head:
        return 0
    max_k = min(K_MAX, len(tail), len(head))
    for k in range(max_k, K_MIN - 1, -1):
        seg_tail = tail[-k:]
        seg_head = head[:k]
        if seg_tail != seg_head:
            continue
        if not _is_pure_cjk(seg_tail):
            continue
        # 虚词头尾过滤：开头是助词 / 结尾是助词 → 跳过(无修辞意义)
        if seg_tail[0] in _TRIM_FUNCTION_HEADS:
            continue
        if seg_tail[-1] in _TRIM_FUNCTION_TAILS:
            continue
        return k
    return 0


def detect_anadiplosis_pairs(text: str) -> list[dict]:
    """扫文本·返回顶真候选列表 [{idx, k, seg, sent_a, sent_b}]"""
    sents = sa.split_sentences(text)
    if len(sents) < 2:
        return []
    pairs: list[dict] = []
    for i in range(len(sents) - 1):
        a = _trim_for_compare(sents[i])
        b = _trim_for_compare(sents[i + 1])
        k = _max_overlap_len(a, b)
        if k > 0:
            pairs.append({
                "idx": i,
                "k": k,
                "seg": a[-k:],
                "sent_a": sents[i],
                "sent_b": sents[i + 1],
            })
    return pairs


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "anadiplosis", "schema_version": "1.0",
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

    pairs = detect_anadiplosis_pairs(text)
    count = len(pairs)
    per_kcjk = round(count / (cjk / 1000.0), 3) if cjk else 0.0

    baseline_info = _read_author_baseline(project_root) or {}
    baseline = float(baseline_info.get("baseline", DEFAULT_BASELINE))
    sigma = float(baseline_info.get("std", 0.0))
    baseline_source = baseline_info.get("source", "fallback")
    over_thresh = baseline + (2 * sigma if sigma > 0 else DEFAULT_OVER_DELTA)
    connectors = baseline_info.get("connectors") or DEFAULT_TOP_CONNECTORS

    out.update({
        "cjk": cjk,
        "anadiplosis_count": count,
        "anadiplosis_per_kcjk": per_kcjk,
        "baseline": baseline,
        "baseline_source": baseline_source,
        "top_connectors": connectors[:10],
        "thresholds": {
            "over_per_kcjk": round(over_thresh, 3),
            "under_min_baseline_per_kcjk": DEFAULT_UNDER_MIN_BASELINE,
            "detected_report_min": DEFAULT_DETECTED_REPORT_MIN,
            "k_range": [K_MIN, K_MAX],
        },
        "samples": [{"k": p["k"], "seg": p["seg"]} for p in pairs[:5]],
    })

    flags = []
    if count >= DEFAULT_DETECTED_REPORT_MIN:
        flags.append({"code": ISSUE_CODE_DETECTED,
                      "msg": f"顶真命中 {count} 例·密度={per_kcjk}/kCJK"})
    if per_kcjk > over_thresh and count >= 2:
        flags.append({"code": ISSUE_CODE_OVER,
                      "msg": f"顶真密度={per_kcjk}/kCJK > 作者档+2σ={round(over_thresh, 3)}·堆叠"})
    if baseline >= DEFAULT_UNDER_MIN_BASELINE and count == 0:
        flags.append({"code": ISSUE_CODE_UNDER,
                      "msg": f"作者档顶真基线={baseline}/kCJK ≥ {DEFAULT_UNDER_MIN_BASELINE}·本 cluster 0 命中=节奏签名丢失"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "anadiplosis", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "陈望道《修辞学发凡》顶针格·R22 W10 Batch-FF·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] anadiplosis: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="顶真(anadiplosis)单格 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
