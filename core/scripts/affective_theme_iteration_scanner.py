#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""affective_theme_iteration_scanner.py — Kuiken self-modifying feeling 主题迭代再现
(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·Kuiken 2008 self-modifying feeling】Kuiken-Miall 实证:文学阅读中
读者的"self-modifying feeling"靠主题情感词 iteratively 再现 + 微调形成. cluster 草稿若主题
情感 marker 只出现一次/不出现/或重复但无 valence 移位 → 失"self-modifying"效应.

【输入】cluster 草稿(CLUSTER_MODE=1 env)·占位词典 core/data/affective_theme_lexicon_placeholder.json.

【探针】对每个主题 tag:
  1. mentions = 全文 marker 出现次数·分 segment(草稿三等分: 头/中/尾)统计 segment_hits.
  2. 迭代成立 = mentions ≥ 3 且至少在 ≥2 个 segment 出现.
  3. valence_shift 方向校验(简化版): 头 segment vs 尾 segment 的 marker 词
     valence_shift 标签是否一致(用同一 theme 的 valence_shift 标签作为参照).
  4. 主题 iteration_score = (segment_diversity) × (mentions/total_segments).

【告警】
  - 主题 mentions ≥ 3 但只出现单 segment → AFFECTIVE_THEME_NO_ITERATION
  - 主题 mentions == 1 且 brief.theme_priority 标记关键 → AFFECTIVE_THEME_FLAT

【北极星⑤】顾问非法官·全 advisory·env AFFECTIVE_THEME_ITERATION_MODE 默认 shadow·
  AFFECTIVE_THEME_ITERATION_DEGRADED 绝不 hard_gate.

【与既有 scanner 严格正交】
  - motif_recurrence       : 意象级·正交(本=主题情感)
  - thematic_argument      : 主题论证·正交
  - sentiment_arc_fractal  : 序列形状·正交
  - EC_vs_PD               : 共情 vs 个人痛苦·正交
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "AFFECTIVE_THEME_ITERATION_DEGRADED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800
_DEFAULT_LEXICON = Path(__file__).resolve().parents[1] / "data" / "affective_theme_lexicon_placeholder.json"


def _mode() -> str:
    m = (os.environ.get("AFFECTIVE_THEME_ITERATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_lexicon(path=None):
    p = Path(path) if path else _DEFAULT_LEXICON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    themes = data.get("themes") or {}
    vshift = data.get("valence_shift") or {}
    if not isinstance(themes, dict):
        themes = {}
    if not isinstance(vshift, dict):
        vshift = {}
    return themes, vshift


def _segment_text(text):
    """按字符等距切三段."""
    L = len(text)
    if L < 3:
        return [text, "", ""]
    one = L // 3
    return [text[:one], text[one:2 * one], text[2 * one:]]


def analyze_themes(text, themes_lex):
    """对每 theme 统计 mentions / segment_hits."""
    segs = _segment_text(text)
    out = {}
    for theme, markers in themes_lex.items():
        if not isinstance(markers, list):
            continue
        total = 0
        seg_hits = [0, 0, 0]
        for i, seg in enumerate(segs):
            cnt = 0
            for m in markers:
                if not isinstance(m, str) or not m:
                    continue
                cnt += seg.count(m)
            seg_hits[i] = cnt
            total += cnt
        if total == 0:
            continue
        seg_diversity = sum(1 for h in seg_hits if h > 0)
        out[theme] = {
            "mentions": total,
            "segment_hits": seg_hits,
            "segment_diversity": seg_diversity,
            "iteration_score": round(
                seg_diversity * (total / max(1, sum(1 for s in segs if s))), 3),
        }
    return out


def _load_brief(project_root, cluster_brief_path):
    data = None
    if cluster_brief_path and Path(cluster_brief_path).exists():
        try:
            data = json.loads(Path(cluster_brief_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        p = Path(project_root) / "_数据库" / "事件簇.json"
        if p.exists():
            try:
                ec = json.loads(p.read_text(encoding="utf-8"))
                clusters = (ec or {}).get("clusters") or []
                if clusters:
                    data = clusters[0]
            except (OSError, json.JSONDecodeError):
                data = None
    return data if isinstance(data, dict) else None


def scan(draft_path, project_root=None, cluster_brief_path=None,
         lexicon_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "affective_theme_iteration", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        out["note"] = "off·skip"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    themes_lex, _ = _load_lexicon(lexicon_path)
    if not themes_lex:
        out["note"] = "词典缺失·skip"
        return out
    theme_stats = analyze_themes(text, themes_lex)
    out["metrics"] = {"themes_present": len(theme_stats),
                      "per_theme": theme_stats}

    brief = _load_brief(project_root, cluster_brief_path)
    priorities = []
    if isinstance(brief, dict):
        tp = brief.get("theme_priority") or brief.get("themes") or []
        if isinstance(tp, list):
            priorities = [t for t in tp if isinstance(t, str)]

    findings = []
    for theme, st in theme_stats.items():
        # 关键主题但 mentions<2 → FLAT
        if theme in priorities and st["mentions"] < 2:
            findings.append(f"关键主题「{theme}」mentions={st['mentions']}<2·flat")
        # mentions>=3 但 segment_diversity==1 → 缺迭代
        if st["mentions"] >= 3 and st["segment_diversity"] == 1:
            findings.append(f"主题「{theme}」mentions={st['mentions']} 集中单段·缺 iteration")

    if findings:
        msg = " · ".join(findings[:5])
        if mode == "active":
            out["violations"].append({
                "kind": "affective_theme_iteration", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": out["metrics"],
                "_doc": "R19 W8 Batch-Y·P2·Kuiken self-modifying feeling·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] affective_theme_iteration[{ISSUE_CODE}]: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·Kuiken self-modifying feeling·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-brief", default=None)
    ap.add_argument("--lexicon", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.cluster_brief, args.lexicon)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
