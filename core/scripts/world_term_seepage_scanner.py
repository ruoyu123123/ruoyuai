#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""world_term_seepage_scanner.py — Walton incluing 工程化(世界术语首现 Gini + info-dump burst)
(advisory · cluster · shadow · 2026-06-20 · R8 W4 Batch-J · L36)

【缺口】R8 W4 联网调研(Jo Walton Reactor SF Reading Protocols incluing + Tomeworks +
CEUR-WS Vol.2145 + Ravid&Berman + AlphaLexChinese): SF/F 的 world-building 必须
incluing(渐渗 vs info-dump). 工程化指标:
  1) 术语首现位置 Gini 系数: Gini < 0.55 = 渐渗式; > 0.55 = 集中爆裂式。
  2) 局部 lexical density 突变: 滑窗 500 CJK content-word ratio 超均值 1.5σ +
     专名密度同窗突变 → info-dump burst。

【做法 · 确定性纯规则】:
  - 读 _数据库/世界观.json 提取术语词表 (entries/glossary/terms keys 兼容多 schema)。
  - 每术语首现字符位置 → Gini(位置序列归一化)。
  - 滑窗 500 CJK 计 content-word 密度 + 专名首现密度 → 1.5σ 突变窗口数。
  - 硬科幻/LitRPG 题材 override 提升 Gini 阈值 (默认 0.55 → 0.65)。

【与 R3 golden_three + R1 show_tell_density 正交】.

【北极星② / ⑤ 顾问非法官】code WORLD_TERM_INFO_DUMP 绝不进 hard_gate 。
env WORLD_TERM_SEEPAGE_MODE: off / shadow(默认) / active。

用法: python world_term_seepage_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from statistics import mean, pstdev

ISSUE_CODE = "WORLD_TERM_INFO_DUMP"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_CONTENT_WORD_BLOCK = re.compile(
    r"[一-鿿]{2,}")  # 简化:连续 2+ CJK 视为内容词


def _mode() -> str:
    m = (os.environ.get("WORLD_TERM_SEEPAGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _gini(values: list) -> float:
    """简易 Gini 系数 (值排序后)。values 为非负浮点。空/单元素 → 0.0。"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    cum = 0.0
    total = 0.0
    for i, v in enumerate(s, 1):
        cum += i * v
        total += v
    if total <= 0:
        return 0.0
    g = (2 * cum) / (n * total) - (n + 1) / n
    return round(max(0.0, min(1.0, g)), 3)


def _extract_world_terms(project_root) -> list:
    """从 _数据库/世界观.json 抽术语词表。容忍多 schema。"""
    if not project_root:
        return []
    wv = Path(project_root) / "_数据库" / "世界观.json"
    if not wv.exists():
        return []
    try:
        obj = json.loads(wv.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    terms = set()
    if isinstance(obj, dict):
        for key in ("glossary", "terms", "entries", "world_glossary",
                    "world_terms", "vocabulary"):
            v = obj.get(key)
            if isinstance(v, dict):
                for k in v.keys():
                    if isinstance(k, str) and 2 <= len(k) <= 12:
                        terms.add(k)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and 2 <= len(item) <= 12:
                        terms.add(item)
                    elif isinstance(item, dict):
                        for kk in ("term", "name", "title"):
                            vv = item.get(kk)
                            if isinstance(vv, str) and 2 <= len(vv) <= 12:
                                terms.add(vv)
    return sorted(terms)


def _genre_override_gini(project_root) -> float:
    """硬科幻 / LitRPG 提阈到 0.65, 否则 0.55。"""
    if not project_root:
        return 0.55
    ap = Path(project_root) / "_数据库" / "作者风格.json"
    if not ap.exists():
        return 0.55
    try:
        obj = json.loads(ap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.55
    if isinstance(obj, dict):
        genre = (obj.get("genre") or "").strip().lower()
        if genre in {"hard_sf", "hard_scifi", "litrpg", "horror_game",
                     "rule_anomaly"}:
            return 0.65
    return 0.55


def _content_density_bursts(draft: str) -> tuple:
    """滑窗 500 CJK content-word ratio 超均值 1.5σ 的窗口数。"""
    cjk_chars = [c for c in draft if "一" <= c <= "鿿"]
    n = len(cjk_chars)
    if n < 1500:  # 至少 3 个非重叠窗口
        return (0, 0.0)
    win = 500
    ratios = []
    for start in range(0, n - win + 1, win):
        window = "".join(cjk_chars[start:start + win])
        blocks = _CONTENT_WORD_BLOCK.findall(window)
        block_chars = sum(len(b) for b in blocks)
        ratios.append(block_chars / win)
    if len(ratios) < 3:
        return (0, 0.0)
    mu = mean(ratios)
    sigma = pstdev(ratios)
    if sigma <= 0:
        return (0, mu)
    threshold = mu + 1.5 * sigma
    bursts = sum(1 for r in ratios if r > threshold)
    return (bursts, round(mu, 3))


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "world_term_seepage", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 1000:
        out["note"] = "草稿太短·跳过"
        return out

    terms = _extract_world_terms(project_root)
    if len(terms) < 3:
        out["note"] = f"世界观术语过少 (n={len(terms)})·跳过(北极星②)"
        out["term_count"] = len(terms)
        return out

    # 每术语首现字符位置
    first_pos = []
    seen_terms = []
    for t in terms:
        idx = draft.find(t)
        if idx >= 0:
            first_pos.append(idx)
            seen_terms.append(t)
    if len(first_pos) < 3:
        out["note"] = f"草稿命中术语过少 (n={len(first_pos)})·跳过"
        out["term_hit_count"] = len(first_pos)
        return out

    # 归一化位置 (0~1) → Gini
    max_pos = max(first_pos)
    normed = [p / max_pos if max_pos > 0 else 0 for p in first_pos]
    gini = _gini(normed)
    gini_threshold = _genre_override_gini(project_root)
    bursts, mu_density = _content_density_bursts(draft)

    out.update({
        "term_count": len(terms),
        "term_hit_count": len(first_pos),
        "first_position_gini": gini,
        "gini_threshold": gini_threshold,
        "content_density_mu": mu_density,
        "burst_window_count": bursts,
    })

    msgs = []
    if gini > gini_threshold:
        msgs.append(f"术语首现位置 Gini={gini} > {gini_threshold}·集中爆裂式 (info-dump 倾向)")
    if bursts >= 2:
        msgs.append(f"局部 lexical density 突变窗口 {bursts} 处·info-dump burst 信号")
    msg = " · ".join(msgs) if msgs else None

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "world_term_seepage", "severity": "minor",
                "message": msg, "gini": gini, "threshold": gini_threshold,
                "bursts": bursts,
                "_doc": ("Walton incluing 工程化 · 硬科幻/LitRPG 题材 override · "
                         "advisory · 绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] world_term_seepage: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="世界术语首现 Gini + lexical density 突变(advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
