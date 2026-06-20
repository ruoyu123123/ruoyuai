#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paratext_interpolation_scanner.py — 文档单元插入密度
(advisory · cluster · 2026-06-20 R11 W6 MODEST · shadow)

【缺口】Creative Nonfiction Mark Kramer 标记 + Design Observer Sebald
"Writing with Pictures" 30% 虚构 paratext 报告：reportage/literary_journalism/
historical_nonfiction_novel 题材文档单元插入是叙事节奏锚点·LLM 一般 skip。

【做法 · 启发式触发标记】
  1. 识别引用块(连续 ≥3 行带"")、报道引文段(『据…记录』『摄于…』『据…报道』)
  2. 算 paratext_unit_density / paratext_type_entropy / register_gap
     (文档段平均句长 vs 散文段平均句长)
  3. 仅在作者档/题材匹配 (reportage/documentary/literary_journalism/historical_nonfiction_novel)
     时激活·否则 skip

【北极星】② 题材门控·shadow·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("PARATEXT_INTERPOLATION_THIN", "PARATEXT_TYPE_MONOTONE")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_TRIGGER_GENRES = {"reportage", "documentary", "literary_journalism",
                   "historical_nonfiction_novel", "nonfiction_documentary_lit"}

PARATEXT_PATTERNS = [
    ("citation_quote", re.compile(r"[""].{6,200}[""]")),
    ("recorded_per", re.compile(r"(据[^，。]{2,12}(?:记录|记载|报道|档案|笔记))")),
    ("photo_caption", re.compile(r"(摄于[^，。]{2,16})")),
    ("archived_doc", re.compile(r"(档案编号|档号|档案[0-9]+)")),
    ("interview", re.compile(r"(采访[^，。]{2,12}|访谈[^，。]{2,12})")),
]


def _mode() -> str:
    m = (os.environ.get("PARATEXT_INTERPOLATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_genre_signals(project_root) -> set:
    """读 作者档 author_genre_packs 与 manifest.genre·返回小写集合"""
    sig = set()
    if not project_root:
        return sig
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            obj = None
        if isinstance(obj, dict):
            for k in ("author_genre_packs", "genre", "genres"):
                v = obj.get(k)
                if isinstance(v, list):
                    sig.update(str(x).strip().lower() for x in v if isinstance(x, str))
                elif isinstance(v, str) and v.strip():
                    sig.add(v.strip().lower())
    return sig


def detect_paratext(text: str) -> list:
    units = []
    for kind, rx in PARATEXT_PATTERNS:
        for m in rx.finditer(text):
            units.append({"kind": kind, "pos": m.start(),
                          "surface": m.group(0)[:60]})
    units.sort(key=lambda u: u["pos"])
    return units


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "paratext_interpolation", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
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
    sig = _read_genre_signals(project_root)
    out["genre_signals"] = sorted(sig)
    if not (_TRIGGER_GENRES & sig):
        out["note"] = "非报告文学/纪录文学题材·跳过(北极星② 题材门控)"
        return out
    units = detect_paratext(text)
    k = max(1, cjk / 1000.0)
    density = round(len(units) / k, 3)
    kinds = {}
    for u in units:
        kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
    # 类型熵(归一)
    total = sum(kinds.values())
    entropy = 0.0
    if total > 0:
        import math
        for v in kinds.values():
            p = v / total
            if p > 0:
                entropy -= p * math.log2(p)
        max_e = math.log2(len(PARATEXT_PATTERNS))
        if max_e > 0:
            entropy = round(entropy / max_e, 4)
    out["paratext_unit_density_per_1k"] = density
    out["paratext_type_entropy_norm"] = entropy
    out["paratext_kinds"] = kinds

    flags = []
    if density < 0.5:
        flags.append({"code": "PARATEXT_INTERPOLATION_THIN",
                      "msg": f"文档单元密度 {density}/千字 偏低(报告文学题材一般 ≥ 0.5)"})
    if total > 0 and entropy < 0.3:
        flags.append({"code": "PARATEXT_TYPE_MONOTONE",
                      "msg": f"文档单元类型熵 {entropy} < 0.3·单调"})
    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "paratext_interpolation", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "报告文学题材·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] paratext_interpolation: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="文档单元插入密度 advisory(shadow)")
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
