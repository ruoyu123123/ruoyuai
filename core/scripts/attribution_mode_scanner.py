#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attribution_mode_scanner.py — 信息源 5 桶认识论出处分布
(advisory · cluster · 2026-06-20 R11 W6 MODEST · shadow)

【缺口】Mark Kramer Creative Nonfiction + Tom Wolfe 四件套报道契约报告：
非虚构/报告文学/伪纪录文学题材每条非主角第一手信息应带出处标记。LLM 经常
全部走 inferred(全知幻觉)·5 桶分布单调。

【做法 · 触发词典识别 5 桶】
  direct: 我看到/亲眼/亲耳/亲口/听他说
  paraphrase: 据说/据报/据传/有人说/有人讲
  archived: 据档案/记载/笔记/史载/正史/野史/日记中
  reconstructed: 应当是/可以想见/不难推断/想必/料想
  inferred: (兜底·无显式词)
→ mode_entropy + 5 桶占比 + 长 streak(同模 ≥ 5 连续段落) advisory

与 R9 quotative 8 桶(词法)正交·本条认识论维。题材门控
(nonfiction_documentary_lit / reportage / 作者档明示全知叙述者豁免)

【北极星】② 作者档第一权威·shadow·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("ATTRIBUTION_MODE_MONOTONE", "ATTRIBUTION_DROUGHT_LONG",
               "THOUGHT_ATTRIBUTED_UNDISCLOSED")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_TRIGGER_GENRES = {"reportage", "documentary", "literary_journalism",
                   "historical_nonfiction_novel", "nonfiction_documentary_lit"}

ATTRIBUTION_LEXICON = {
    "direct": re.compile(r"(我亲眼|亲耳|亲口|当面|当场|现场|目睹|目击)"),
    "paraphrase": re.compile(r"(据说|据传|据报|据闻|有人说|有人讲|外间传|坊间传)"),
    "archived": re.compile(r"(史载|载入|档案|档号|笔记记|日记中|正史|野史|文献|"
                            r"载|《[一-鿿]{2,12}》记|《[一-鿿]{2,12}》载)"),
    "reconstructed": re.compile(r"(应当是|应该是|想必|大概|料想|不难推断|可以想见|料定)"),
}


def _mode() -> str:
    m = (os.environ.get("ATTRIBUTION_MODE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_settings(project_root):
    out = {"genres": set(), "omniscient": False}
    if not project_root:
        return out
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return out
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    if not isinstance(obj, dict):
        return out
    for k in ("author_genre_packs", "genre", "genres"):
        v = obj.get(k)
        if isinstance(v, list):
            out["genres"].update(str(x).lower() for x in v if isinstance(x, str))
        elif isinstance(v, str):
            out["genres"].add(v.lower())
    # 全知叙述者
    nv = obj.get("narrative_pov_mode") or obj.get("pov_mode")
    if isinstance(nv, str) and "omniscient" in nv.lower():
        out["omniscient"] = True
    if obj.get("omniscient_narrator"):
        out["omniscient"] = True
    return out


def classify_paragraph(para: str) -> str:
    """每段分桶·返回第一个命中的桶名·全无命中 → inferred"""
    for bucket, rx in ATTRIBUTION_LEXICON.items():
        if rx.search(para):
            return bucket
    return "inferred"


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "attribution_mode", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "violations": [], "verdict": "PASS",
           "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out
    settings = _read_settings(project_root)
    if not (_TRIGGER_GENRES & settings["genres"]):
        out["note"] = "非纪录文学题材·跳过(北极星② 题材门控)"
        return out
    if settings["omniscient"]:
        out["note"] = "作者档明示全知叙述者·自动豁免"
        return out
    paras = [p for p in text.split("\n") if _cjk_count(p) >= 30]
    if len(paras) < 5:
        out["note"] = "段落 <5·跳过"
        return out
    buckets = []
    for p in paras:
        buckets.append(classify_paragraph(p))
    counts = {b: buckets.count(b) for b in
              ("direct", "paraphrase", "archived", "reconstructed", "inferred")}
    total = len(buckets)
    shares = {k: round(v / total, 4) for k, v in counts.items()}
    entropy = 0.0
    for v in counts.values():
        if v > 0:
            p_ = v / total
            entropy -= p_ * math.log2(p_)
    entropy_norm = round(entropy / math.log2(5), 4)
    # 最长 streak
    max_streak = 1
    cur = 1
    for i in range(1, len(buckets)):
        if buckets[i] == buckets[i - 1]:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 1
    out["bucket_counts"] = counts
    out["bucket_shares"] = shares
    out["mode_entropy_norm"] = entropy_norm
    out["max_streak"] = max_streak

    flags = []
    if entropy_norm < 0.3:
        flags.append({"code": "ATTRIBUTION_MODE_MONOTONE",
                      "msg": f"5 桶熵 {entropy_norm} < 0.3·出处分布单调"})
    if max_streak >= 5:
        flags.append({"code": "ATTRIBUTION_DROUGHT_LONG",
                      "msg": f"最长同模 streak={max_streak} ≥ 5 段"})
    if shares.get("inferred", 0) > 0.7:
        flags.append({"code": "THOUGHT_ATTRIBUTED_UNDISCLOSED",
                      "msg": f"inferred 占比 {shares['inferred']:.2%} > 70%·全知幻觉"})
    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "attribution_mode", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Kramer/Wolfe 报道契约·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] attribution_mode: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="信息源 5 桶认识论分布 advisory(shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
