#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_performance_scanner.py — Echo Draft 4-Pass 朗读 performance 审计 · R23 W11 Batch-II · P2

【缺口】有声书 craft 文献 (Echo Draft 4-Pass) 指出三类朗读 friction：
  (a) 三分句堆率：单段含 ≥3 完整主谓子句 → 朗读时主语切换密 / 听众跟丢
  (b) 抽象名词堆：50 CJK 窗口含 {情况/状况/局面/状态/现实/局势} ≥3 次 → 空泛复述感
  (c) 括弧带过率：单括弧 > 25 CJK 段 → 朗读吞断 / 视觉跳读

全栈零检测。本 scanner 三探针 advisory shadow 补口子。

【北极星 ②④⑤】
  · cluster 单位 · 作者档第一权威 (长复合句作者档 relax) · advisory shadow · 绝不 hard_gate
  · 占位 lexicon `_placeholder=true` · 真版需要 BCC/RAS 朗读语料校准

【作者档 relax】读 作者风格.json 的 `audio_performance_relax` (bool) /
  `audio_performance_baseline.{tri_clause_density_target, abstract_noun_density_target, parenthesis_long_ratio_target}`
  · 锁长复合句作者 (e.g. 古典/严肃) → 三分句堆率 relax (target 上浮 0.5)
  · 无作者档 → 兜底硬阈值

env AUDIO_PERFORMANCE_MODE: off / shadow(默认) / active
用法: python audio_performance_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_TRI_CLAUSE = "AUDIO_PERF_TRI_CLAUSE_HEAVY"
ISSUE_ABSTRACT_NOUN = "AUDIO_PERF_ABSTRACT_NOUN_PILE"
ISSUE_PARENTHESIS = "AUDIO_PERF_PARENTHESIS_DRAG"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 探针 (a) 三分句堆：段落内完整主谓子句计数（粗略：句末 . ! ? 计 + 排除对话段）
TRI_CLAUSE_THRESHOLD_PER_PARA = 3       # 单段含 ≥3 完整主谓子句 → 候选
TRI_CLAUSE_DENSITY_DEFAULT = 0.20       # 段占比 > 20% → 报
TRI_CLAUSE_DENSITY_RELAX = 0.30         # 长复合句作者档 relax 上浮

# 探针 (b) 抽象名词堆
ABSTRACT_NOUNS = ("情况", "状况", "局面", "状态", "现实", "局势")
ABSTRACT_WINDOW_CJK = 50
ABSTRACT_HITS_THRESHOLD = 3              # 窗口 50 CJK 含 ≥3 次 → 候选
ABSTRACT_DENSITY_DEFAULT = 1.0           # 窗口命中数 / 1k CJK > 1.0 → 报
ABSTRACT_DENSITY_RELAX = 2.0

# 探针 (c) 括弧带过：单括弧 > 25 CJK 段
PARENTHESIS_LEN_CJK = 25
PARENTHESIS_DENSITY_DEFAULT = 0.10       # >10% 段含长括弧 → 报
PARENTHESIS_DENSITY_RELAX = 0.20

_PUNCT_END = "。！？!?"
_DIALOG_OPEN = '""“”\'「『（('
_PAREN_PAIRS = (("(", ")"), ("（", "）"))


def _mode() -> str:
    m = (os.environ.get("AUDIO_PERFORMANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _read_author_relax(project_root):
    """读作者档 audio_performance_relax / audio_performance_baseline。返回 dict。"""
    if not project_root:
        return {}
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        out = {}
        if isinstance(obj.get("audio_performance_relax"), bool):
            out["relax"] = obj["audio_performance_relax"]
        bl = obj.get("audio_performance_baseline")
        if isinstance(bl, dict):
            for k in ("tri_clause_density_target",
                      "abstract_noun_density_target",
                      "parenthesis_long_ratio_target"):
                if isinstance(bl.get(k), (int, float)):
                    out[k] = float(bl[k])
        return out
    return {}


def _count_clauses(paragraph: str) -> int:
    """粗略：句末标点 (。！？!?) 计数 = 主谓子句数。"""
    n = 0
    for ch in paragraph:
        if ch in _PUNCT_END:
            n += 1
    return n


def _is_dialog_para(paragraph: str) -> bool:
    head = paragraph.lstrip("　 \t")
    return bool(head) and head[:1] in _DIALOG_OPEN


def detect_tri_clause(paragraphs):
    """段落内主谓子句 ≥3 的段（排除对话段）。返回命中段索引列表。"""
    hits = []
    for i, p in enumerate(paragraphs):
        if _is_dialog_para(p):
            continue
        if _count_clauses(p) >= TRI_CLAUSE_THRESHOLD_PER_PARA:
            hits.append(i)
    return hits


def detect_abstract_pile(text: str):
    """滑窗 50 CJK · 含抽象名词命中数 ≥3 的窗口。返回 [{start_cjk, hits}]"""
    # 简化：只取 CJK 序列，按 50-CJK 窗口滑动 (step=10)
    cjk_chars = [c for c in text if "一" <= c <= "鿿"]
    n = len(cjk_chars)
    out = []
    if n < ABSTRACT_WINDOW_CJK:
        return out
    step = 10
    seen_starts = set()
    for start in range(0, n - ABSTRACT_WINDOW_CJK + 1, step):
        seg = "".join(cjk_chars[start:start + ABSTRACT_WINDOW_CJK])
        hits = sum(seg.count(w) for w in ABSTRACT_NOUNS)
        if hits >= ABSTRACT_HITS_THRESHOLD and start not in seen_starts:
            out.append({"start_cjk": start, "hits": hits})
            seen_starts.add(start)
    return out


def detect_parenthesis_drag(paragraphs):
    """单括弧 > 25 CJK 的段。返回 [{idx, len_cjk, sample}]"""
    out = []
    for i, p in enumerate(paragraphs):
        for op, cp in _PAREN_PAIRS:
            j = 0
            while True:
                k = p.find(op, j)
                if k < 0:
                    break
                e = p.find(cp, k + 1)
                if e < 0:
                    break
                inner = p[k + 1:e]
                inner_cjk = _cjk_count(inner)
                if inner_cjk > PARENTHESIS_LEN_CJK:
                    out.append({
                        "idx": i, "len_cjk": inner_cjk,
                        "sample": (inner[:30] + "…") if len(inner) > 30 else inner,
                    })
                j = e + 1
    return out


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "audio_performance",
        "schema_version": "1.0",
        "mode": mode,
        "gate_level": "advisory",
        "violations": [],
        "verdict": "PASS",
        "warning": None,
        "_doc": "Echo Draft 4-Pass 朗读 craft·R23 W11 Batch-II·_placeholder=true",
    }
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

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    body = [p for p in paragraphs if not re.match(r"^第\d+章", p) and not p.startswith("【")]
    para_n = len(body)

    relax_cfg = _read_author_relax(project_root)
    relax = bool(relax_cfg.get("relax"))

    # (a) 三分句堆率
    tri_target = (relax_cfg.get("tri_clause_density_target")
                  or (TRI_CLAUSE_DENSITY_RELAX if relax else TRI_CLAUSE_DENSITY_DEFAULT))
    tri_hits = detect_tri_clause(body)
    tri_density = round(len(tri_hits) / para_n, 4) if para_n else 0.0

    # (b) 抽象名词堆
    abs_target = (relax_cfg.get("abstract_noun_density_target")
                  or (ABSTRACT_DENSITY_RELAX if relax else ABSTRACT_DENSITY_DEFAULT))
    abs_windows = detect_abstract_pile(text)
    abs_density = round(len(abs_windows) / (cjk / 1000.0), 3) if cjk else 0.0

    # (c) 括弧带过率
    paren_target = (relax_cfg.get("parenthesis_long_ratio_target")
                    or (PARENTHESIS_DENSITY_RELAX if relax else PARENTHESIS_DENSITY_DEFAULT))
    paren_hits = detect_parenthesis_drag(body)
    paren_density = round(len(paren_hits) / para_n, 4) if para_n else 0.0

    flags = []
    if tri_density > tri_target and len(tri_hits) >= 3:
        flags.append({
            "code": ISSUE_TRI_CLAUSE,
            "msg": (f"三分句堆段占比 {round(tri_density * 100, 1)}% > target "
                    f"{round(tri_target * 100, 1)}% ({len(tri_hits)} 段含 ≥3 主谓子句)·朗读切换密"),
        })
    if abs_density > abs_target and len(abs_windows) >= 1:
        flags.append({
            "code": ISSUE_ABSTRACT_NOUN,
            "msg": (f"抽象名词窗口密度 {abs_density}/kCJK > target {abs_target}·"
                    f"{len(abs_windows)} 窗口含 ≥{ABSTRACT_HITS_THRESHOLD} 抽象名词·复述感"),
        })
    if paren_density > paren_target and len(paren_hits) >= 2:
        flags.append({
            "code": ISSUE_PARENTHESIS,
            "msg": (f"长括弧段占比 {round(paren_density * 100, 1)}% > target "
                    f"{round(paren_target * 100, 1)}% ({len(paren_hits)} 段含 >25CJK 括弧)·朗读吞断"),
        })

    out.update({
        "cjk": cjk,
        "para_count": para_n,
        "metrics": {
            "tri_clause_paragraphs": len(tri_hits),
            "tri_clause_density": tri_density,
            "abstract_windows": len(abs_windows),
            "abstract_per_kcjk": abs_density,
            "long_parenthesis_paragraphs": len(paren_hits),
            "long_parenthesis_density": paren_density,
        },
        "author_relax": relax,
        "thresholds": {
            "tri_target": tri_target,
            "abstract_target": abs_target,
            "parenthesis_target": paren_target,
        },
        "samples": {
            "abstract_windows_top": abs_windows[:3],
            "long_parenthesis_top": paren_hits[:3],
        },
    })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "audio_performance",
                    "severity": "minor",
                    "code": f["code"],
                    "message": f["msg"],
                    "_doc": "Echo Draft 4-Pass·R23 W11·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] audio_performance: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Echo Draft 4-Pass 朗读 performance advisory")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
