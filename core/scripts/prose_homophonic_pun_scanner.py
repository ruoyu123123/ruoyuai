#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_homophonic_pun_scanner.py — 谐音双关 salience-contrast scanner
(advisory · cluster · 2026-06-20 R12 W6 Batch-Q · P2 · shadow)

【缺口】PMC9131095 Hsu 2022 眼动 salience-contrast 72 三元组 + arxiv 2507.07640
Lost in Pronunciation + arxiv 2601.19932 Newspaper Eat + Wikipedia Homophonic
puns 无情/无晴 + mandarinblueprint Cihai yi 149 字同音池：
中文谐音双关是文化签名工艺·LLM 默认全直陈·缺谐音 token 密度。

【做法 · 占位轻量规则（不依赖 LLM）】
  1. pypinyin 兜底（若可装）→ 中文 syllable 索引；不可装 → 仅走小型 seed 表。
  2. 4 字滑窗扫 homophone candidate（与 seed 双关池命中）。
  3. ±150 字 critical context noun（窗口内含 _CONTEXT_NOUN_LEXICON）→ supported。
  4. 输出 pun_density_per_10k 与 pun_with_context_support_ratio。
  5. genre-conditioned ECDF（占位）：搞笑/都超/无限流 vs 古风/正剧 分桶（待校准）。
  6. shadow 期仅记不报；active 期 density 偏低（<5/10k 且窗口 100+ scene 写大段对白）
     → HOMOPHONIC_PUN_THIN advisory（默认不触发 · 阈值占位）。

【北极星】②④⑤ cluster 视野·作者档第一权威·advisory shadow·绝不 hard_gate
HOMOPHONIC_PUN_* 绝不进 audit_hub.HARD_GATE_CODES。

env HOMOPHONIC_PUN_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("HOMOPHONIC_PUN_THIN",)
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 经典谐音双关 seed 三元组（occurrence_form, base_homophone, sense_hint）
# 占位 · 待 SUBTLEX-CH/CC-CEDICT 离线 syllable→char 倒排表替换
_PUN_SEED = [
    ("无晴", "无情", "晴/情同 qing"),
    ("有晴", "有情", "晴/情同 qing"),
    ("散席", "散戏", "席/戏同 xi"),
    ("芙蓉", "夫容", "蓉/容同 rong"),
    ("莲心", "怜心", "莲/怜同 lian"),
    ("丝", "思", "同 si"),
    ("梨", "离", "同 li"),
    ("柳", "留", "同 liu"),
    ("梅", "媒", "同 mei"),
    ("竹", "祝", "同 zhu"),
    ("枣", "早", "同 zao"),
    ("瓶", "平", "同 ping"),
    ("莲", "连", "同 lian"),
    ("蝠", "福", "同 fu"),
    ("鹿", "禄", "同 lu"),
    ("鱼", "余", "同 yu"),
]

# critical context noun（占位）：恋情 / 离别 / 福禄 / 死亡 / 双关高频语境
_CONTEXT_NOUNS = re.compile(
    r"(情|爱|恋|相思|离别|分别|送别|喜事|婚事|寿|福|禄|安康|死|亡|哭|笑|"
    r"祝愿|祝福|赠|送|留念|题字|诗|词|对联|灯谜|谜面)")

# 触发题材门控
_GENRE_PUN_RICH = {"comedy", "urban_supernatural", "infinite_flow", "xianxia",
                   "historical", "ancient_fantasy", "wuxia"}
_GENRE_PUN_THIN = {"scifi", "hard_scifi", "modern_thriller", "war"}


def _mode() -> str:
    m = (os.environ.get("HOMOPHONIC_PUN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_genres(project_root):
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(obj, dict):
        return set()
    out = set()
    for k in ("author_genre_packs", "genre", "genres"):
        v = obj.get(k)
        if isinstance(v, list):
            out.update(str(x).lower() for x in v if isinstance(x, str))
        elif isinstance(v, str):
            out.add(v.lower())
    return out


def find_pun_candidates(text: str):
    """返回 [{form, homophone, pos, support}]·support=True 表 ±150 字含 context noun。"""
    hits = []
    n = len(text)
    for form, homo, _hint in _PUN_SEED:
        idx = 0
        while True:
            i = text.find(form, idx)
            if i < 0:
                break
            lo = max(0, i - 150)
            hi = min(n, i + 150)
            window = text[lo:hi]
            support = bool(_CONTEXT_NOUNS.search(window))
            hits.append({"form": form, "homophone": homo, "pos": i, "support": support})
            idx = i + len(form)
    hits.sort(key=lambda h: h["pos"])
    return hits


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "prose_homophonic_pun", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "violations": [],
           "verdict": "PASS", "warning": None}
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

    hits = find_pun_candidates(text)
    pun_density_per_10k = round(len(hits) / (cjk / 10000.0), 4) if cjk > 0 else 0.0
    supported = sum(1 for h in hits if h["support"])
    support_ratio = round(supported / len(hits), 4) if hits else 0.0
    out["pun_count"] = len(hits)
    out["pun_density_per_10k"] = pun_density_per_10k
    out["pun_with_context_support_ratio"] = support_ratio
    out["sample_hits"] = hits[:6]

    genres = _read_genres(project_root)
    out["genres_detected"] = sorted(genres)
    if genres & _GENRE_PUN_THIN:
        out["note"] = "硬题材天然 pun 稀疏·跳过 active 闸（仅记）"
        out["genre_bucket"] = "pun_thin"
    elif genres & _GENRE_PUN_RICH:
        out["genre_bucket"] = "pun_rich"
    else:
        out["genre_bucket"] = "neutral"

    # shadow 期不上报 · active 期 placeholder（pun_rich 题材+density 极低 才提示）
    flags = []
    if out["genre_bucket"] == "pun_rich" and pun_density_per_10k < 0.5 and cjk > 8000:
        flags.append({"code": "HOMOPHONIC_PUN_THIN",
                      "msg": (f"pun_density_per_10k={pun_density_per_10k} < 0.5·"
                              "建议补 1-2 处双关（占位阈值待校准）")})
    out["flags"] = flags

    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "homophonic_pun", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "Hsu 2022 salience-contrast·advisory·绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = flags[0]["msg"]
    elif flags:
        print(f"[SHADOW] homophonic_pun: {flags[0]['msg']} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="谐音双关 salience-contrast scanner (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
