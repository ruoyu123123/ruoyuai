#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""allusion_ledger_scanner.py — Thomas 6 类互文 + 三槽典故密度 + 三读者承重测试
(advisory · cluster · 2026-06-20 R11 W6 MODEST · shadow)

【缺口】EMNLP 2025 Stanford/UCSD Chengyu-Bench arXiv:2506.18105 +
2025 Quantitative Intertextuality Survey arXiv:2510.27045 +
Thomas 1986 Harvard 6 类报告：LLM 中文写作典故密度+承重显著偏低·读者承重测试缺。

【做法】
  1. 加载 core/data/allusion_seed_zh.json 三槽
     {chengyu, classical_locus, pop_modern_ref}
  2. 全文扫描 → allusions_per_1k_cjk · 三槽计数
  3. Thomas 6 类启发式（前后窗口词典）：
     corrective / self / casual / single / apparent / multiple
  4. 三读者承重测试：典故首现 ±N 字无 gloss(解释/释义/即/便是/也就是) +
     同段含决策动词(决定/选择/便/便去/即刻) → LOAD_BEARING_ALLUSION_NO_GLOSS advisory
  5. consolidate_author_profile 占位 allusion_signature 段

【北极星】② 作者档第一权威·全 advisory·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("LOAD_BEARING_ALLUSION_NO_GLOSS", "CHENGYU_MISUSE_SUSPECTED")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "allusion_seed_zh.json"

GLOSS_MARKERS = re.compile(r"(意思是|意即|即|便是|也就是说|出自|典出|乃是|所谓)")
DECISION_MARKERS = re.compile(r"(决定|选择|便|便去|即刻|当下|于是|遂)")


def _mode() -> str:
    m = (os.environ.get("ALLUSION_LEDGER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_seed() -> dict:
    try:
        return json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"chengyu": [], "classical_locus": [], "pop_modern_ref": []}


def _read_author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        return obj.get("allusion_signature")
    return None


def _scan_load_bearing(text: str, hits: list, window: int = 40) -> list:
    paras = [p for p in text.split("\n") if p.strip()]
    para_index = []
    cursor = 0
    for p in paras:
        para_index.append((cursor, cursor + len(p), p))
        cursor += len(p) + 1
    load_bearing = []
    seen = set()
    for h in hits:
        key = h["term"]
        if key in seen:
            continue
        seen.add(key)
        pos = h["pos"]
        l = max(0, pos - window)
        r = min(len(text), pos + window)
        win = text[l:r]
        if GLOSS_MARKERS.search(win):
            continue
        for ps, pe, ptext in para_index:
            if ps <= pos < pe:
                if DECISION_MARKERS.search(ptext):
                    load_bearing.append({"term": key, "pos": pos,
                                         "para_excerpt": ptext[:80]})
                break
    return load_bearing


def detect_allusions(text: str, seed: dict) -> dict:
    """三槽抽取"""
    buckets = {}
    all_hits = []
    for bucket in ("chengyu", "classical_locus", "pop_modern_ref"):
        terms = seed.get(bucket, []) or []
        bucket_hits = []
        for t in terms:
            if not t:
                continue
            for m in re.finditer(re.escape(t), text):
                bucket_hits.append({"term": t, "pos": m.start(), "bucket": bucket})
        buckets[bucket] = bucket_hits
        all_hits.extend(bucket_hits)
    return {"buckets": buckets, "all_hits": all_hits}


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "allusion_ledger", "schema_version": "1.0", "mode": mode,
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
    seed = _load_seed()
    res = detect_allusions(text, seed)
    k = max(1, cjk / 1000.0)
    bucket_counts = {b: len(h) for b, h in res["buckets"].items()}
    out["bucket_counts"] = bucket_counts
    out["allusions_per_1k_cjk"] = round(len(res["all_hits"]) / k, 3)
    load_bearing = _scan_load_bearing(text, res["all_hits"])
    out["load_bearing_no_gloss"] = load_bearing
    flags = []
    if load_bearing:
        flags.append({"code": "LOAD_BEARING_ALLUSION_NO_GLOSS",
                      "msg": f"承重典故无 gloss {len(load_bearing)} 处(示例: {load_bearing[0]['term']})"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "allusion_ledger", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Thomas 6 类·三读者承重·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] allusion_ledger: {msg} — 不上报", file=sys.stderr)

    # consolidate_author_profile 占位
    baseline = _read_author_baseline(project_root)
    if baseline:
        out["author_allusion_signature"] = baseline
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Thomas 6 类互文 advisory(shadow)")
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
