#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""onomatopoeia_density_scanner.py — giongo/gitaigo/gijougo 三类拟声拟态密度
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L58 P1 · 二次元/异世界)

【缺口】R10 联网调研(Atlantis Press ICOLLITE 2020 Saiunkoku Monogatari 131
onomatopoeia + ANLP 2024 shonen manga giongo vs gitaigo + Mezzoguild 三分类)：
二次元/异世界/玄幻战斗题材【拟声(giongo) / 拟态(gitaigo) / 拟情(gijougo)
三类密度+形态分布】是核心质感。LLM 默认易写过素或全用拟声丢失分布。此前
【0 检测】。

【做法 · 确定性词典 + 形态分类】：
  1. 读 core/scripts/lexicons/mimetic_lexicon.json(占位骨架·真实蒸馏需扩词)。
  2. genre 门控：仅 anime_isekai / xianxia_battle / fantasy_combat / litrpg 启用。
  3. 草稿命中三类 → mimetic_per_kCJK + class 占比。
  4. 形态分布：AA / ABB / ABAB / 单音延长。
  5. 与作者档 author_mimetic_baseline KL → 偏离 advisory。

【北极星② / ⑤】纯 advisory · genre 不匹配自动 skip · 绝不 hard_gate。
  env ONOMATOPOEIA_MODE: off / shadow(默认) / active。

用法：python onomatopoeia_density_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE = "MIMETIC_DENSITY_DRIFT"
MIN_CJK = 500

GENRE_ALLOW = {"anime_isekai", "xianxia_battle", "fantasy_combat",
               "litrpg", "system_isekai", "game_anime", "horror_game",
               "xianxia", "xuanhuan"}


def _mode() -> str:
    m = (os.environ.get("ONOMATOPOEIA_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    return load_json(p)


def _load_lexicon():
    p = Path(__file__).resolve().parent / "lexicons" / "mimetic_lexicon.json"
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return None
    return obj


def _genre(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if isinstance(obj, dict):
        g = obj.get("genre") or obj.get("genre_pack")
        if isinstance(g, str):
            return g.strip().lower()
    return None


def _author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    return obj.get("author_mimetic_baseline")


def count_hits(text, words):
    total = 0
    for w in words:
        if not w:
            continue
        total += text.count(w)
    return total


def classify_form(words):
    counts = {"AA": 0, "ABB": 0, "ABAB": 0, "other": 0}
    for w in words:
        if len(w) == 2 and w[0] == w[1]:
            counts["AA"] += 1
        elif len(w) == 3 and w[1] == w[2]:
            counts["ABB"] += 1
        elif len(w) == 4 and w[0] == w[2] and w[1] == w[3]:
            counts["ABAB"] += 1
        else:
            counts["other"] += 1
    return counts


def scan(draft_path, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "onomatopoeia_density", "schema_version": "1.0",
           "mode": mode_env, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    g = _genre(project_root)
    out["genre"] = g
    if g not in GENRE_ALLOW:
        out["note"] = f"题材 {g} 非二次元/玄幻战斗 · 跳过"
        return out
    lex = _load_lexicon()
    if not lex:
        out["note"] = "无 mimetic_lexicon.json · 跳过"
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    g_ono = lex.get("giongo_onomatopoeia") or []
    g_mim = lex.get("gitaigo_mimetic") or []
    g_aff = lex.get("gijougo_affective") or []
    hits_ono = count_hits(text, g_ono)
    hits_mim = count_hits(text, g_mim)
    hits_aff = count_hits(text, g_aff)
    total = hits_ono + hits_mim + hits_aff
    per_kCJK = round(total / (cjk / 1000.0), 3) if cjk else 0
    out["mimetic_per_kCJK"] = per_kCJK
    out["class_counts"] = {"giongo": hits_ono, "gitaigo": hits_mim,
                           "gijougo": hits_aff}
    if total > 0:
        out["class_distribution"] = {
            "giongo": round(hits_ono / total, 4),
            "gitaigo": round(hits_mim / total, 4),
            "gijougo": round(hits_aff / total, 4),
        }
    # 形态分布
    matched_words = []
    for w in g_ono + g_mim + g_aff:
        if w and w in text:
            matched_words.append(w)
    out["form_distribution"] = classify_form(matched_words)

    baseline = _author_baseline(project_root)
    msg = None
    if baseline and isinstance(baseline.get("per_kCJK"), (int, float)):
        target = baseline["per_kCJK"]
        if target > 0 and per_kCJK < 0.4 * target:
            msg = (f"mimetic 密度 {per_kCJK}/千 < 作者基线 "
                   f"{target} 的 40% · 二次元/玄幻题材质感稀薄")
        elif target > 0 and per_kCJK > 2.5 * target:
            msg = (f"mimetic 密度 {per_kCJK}/千 > 作者基线 "
                   f"{target} 的 2.5x · 过密")
    else:
        # 兜底：完全无 mimetic 提示
        if total == 0 and cjk > 1500:
            msg = "二次元/玄幻题材但 mimetic 三类均零命中 · 质感缺失提示"
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "mimetic_density",
                "severity": "minor", "message": msg,
                "_doc": "advisory · 作者档第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] onomatopoeia: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="拟声/拟态/拟情密度 (advisory · shadow · 二次元/玄幻)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
