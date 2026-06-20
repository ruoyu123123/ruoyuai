#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_chaizi_ledger.py — 拆字/字谜 glyphic-decomposition scanner (genre-gated)
(advisory · cluster · 2026-06-20 R12 W6 Batch-Q · P2 · shadow)

【缺口】brill 学术专著 Chinese Character Manipulation + scholar.harvard Kelly
金瓶梅 chaizi + Wikipedia Tui bei tu 推背图 + tumblr chaizi 6 模板 + github
kfcd/chaizi 1.4 万字真值表：拆字/字谜是中国古典文学高签名工艺·LLM 零产出。

【做法 · 占位轻量规则（不依赖 LLM）】
  1. 复用 kfcd/chaizi 公开拆字字典思路（若可装 → 加载；否则用占位 30-50 字字典）。
  2. 模板匹配 6 类标志短语：
       split_left_right: "左X右Y的字"
       split_top_bottom: "上X下Y的字"
       remove_radical: "去掉X旁"
       add_radical: "加上X旁/部"
       riddle: "打一字" / "是字也" / "猜字"
       prophecy: "拆字" / "字谶" / "字解"
  3. 字典验证双层召回（命中模板 → 字典反查锚定字）。
  4. 输出 chaizi_density_per_10k / chaizi_self_consistent_ratio /
     chaizi_function_distribution（5 桶 prophecy/name_pun/secret_msg/divination/joke）。
  5. genre-gated：默认仅玄幻/仙侠/历史/古风/谍战启用·其他题材 skip。
  6. Volume-level 跨 cluster 兑付检（占位 · 留 hook）。

【北极星】②④⑤ cluster 视野·作者档第一权威·advisory shadow·绝不 hard_gate
CHAIZI_* 绝不进 audit_hub.HARD_GATE_CODES。

env CHAIZI_LEDGER_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("CHAIZI_DENSITY_THIN", "CHAIZI_INCONSISTENT")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 6 模板（占位）
_TEMPLATE_PATTERNS = {
    "split_left_right": re.compile(r"左[一-鿿]右[一-鿿]"),
    "split_top_bottom": re.compile(r"上[一-鿿]下[一-鿿]"),
    "remove_radical": re.compile(r"去掉?[一-鿿]{1,3}[旁部首]"),
    "add_radical": re.compile(r"(加上?|添)[一-鿿]{1,3}[旁部首]"),
    "riddle": re.compile(r"(打一字|猜一字|是字也|谜底|字谜)"),
    "prophecy": re.compile(r"(拆字|字谶|字解|分拆|拆开|破字)"),
}

# 功能分桶关键词（占位 · 待校准）
_FUNCTION_LEXICON = {
    "prophecy": re.compile(r"(谶|预|兆|签|断言|应验|应在)"),
    "name_pun": re.compile(r"(名|姓|字号|表字|讳)"),
    "secret_msg": re.compile(r"(暗|密|秘|传讯|传信|私下|藏字)"),
    "divination": re.compile(r"(卦|算|占|相|测|推算|算命|相术)"),
    "joke": re.compile(r"(笑|戏|玩笑|戏谑|逗|趣|顽笑)"),
}

# 占位拆字字典（30 字 · 待 kfcd/chaizi 替换）
_PLACEHOLDER_CHAIZI_DICT = {
    "好": ("女", "子"), "明": ("日", "月"), "林": ("木", "木"),
    "众": ("人", "从"), "森": ("木", "木", "木"), "晶": ("日", "日", "日"),
    "孬": ("不", "好"), "甭": ("不", "用"), "歪": ("不", "正"),
    "尖": ("小", "大"), "鲜": ("鱼", "羊"), "智": ("知", "日"),
    "葬": ("艹", "死", "廾"), "墨": ("黑", "土"), "想": ("相", "心"),
    "忐": ("上", "心"), "忑": ("下", "心"), "忌": ("己", "心"),
    "苦": ("艹", "古"), "茶": ("艹", "人", "木"), "李": ("木", "子"),
    "杏": ("木", "口"), "看": ("手", "目"), "灾": ("宀", "火"),
    "尘": ("小", "土"), "灭": ("一", "火"), "歪": ("不", "正"),
    "甜": ("舌", "甘"), "鸣": ("口", "鸟"), "众": ("人", "从"),
}

# 默认启用题材
_GENRE_ALLOW = {"xuanhuan", "xianxia", "historical", "ancient_fantasy",
                "wuxia", "ancient", "antique", "espionage", "spy",
                "scheming_politics"}


def _mode() -> str:
    m = (os.environ.get("CHAIZI_LEDGER_MODE") or "shadow").strip().lower()
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


def find_chaizi_events(text: str):
    """模板匹配 + 字典锚点。返回 [{pos, template, snippet, function_bucket, dict_verified}]。"""
    events = []
    for tname, rx in _TEMPLATE_PATTERNS.items():
        for m in rx.finditer(text):
            i = m.start()
            lo = max(0, i - 60)
            hi = min(len(text), i + 60)
            window = text[lo:hi]
            # 字典验证
            verified_chars = [c for c in window if c in _PLACEHOLDER_CHAIZI_DICT]
            # 功能分桶
            bucket = "joke"  # 默认 joke
            for fname, frx in _FUNCTION_LEXICON.items():
                if frx.search(window):
                    bucket = fname
                    break
            events.append({"pos": i, "template": tname,
                           "snippet": window.replace("\n", " "),
                           "function_bucket": bucket,
                           "dict_verified": bool(verified_chars),
                           "verified_chars": verified_chars[:3]})
    events.sort(key=lambda e: e["pos"])
    return events


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "prose_chaizi_ledger", "schema_version": "1.0",
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

    genres = _read_genres(project_root)
    out["genres_detected"] = sorted(genres)
    if not (genres & _GENRE_ALLOW):
        out["note"] = "题材未启用 chaizi（默认仅玄幻/仙侠/历史/古风/谍战）·跳过"
        return out

    events = find_chaizi_events(text)
    density = round(len(events) / (cjk / 10000.0), 4) if cjk > 0 else 0.0
    verified = sum(1 for e in events if e["dict_verified"])
    consistent_ratio = round(verified / len(events), 4) if events else 0.0
    buckets = {k: 0 for k in ("prophecy", "name_pun", "secret_msg", "divination", "joke")}
    for e in events:
        buckets[e["function_bucket"]] = buckets.get(e["function_bucket"], 0) + 1
    out["chaizi_density_per_10k"] = density
    out["chaizi_self_consistent_ratio"] = consistent_ratio
    out["chaizi_function_distribution"] = buckets
    out["event_count"] = len(events)
    out["sample_events"] = events[:5]

    flags = []
    # 占位阈值（待校准）
    if density < 0.3 and cjk > 8000:
        flags.append({"code": "CHAIZI_DENSITY_THIN",
                      "msg": (f"chaizi_density_per_10k={density} < 0.3·"
                              "古风/谍战题材建议补 1 处拆字（占位阈值待校准）")})
    if events and consistent_ratio < 0.4:
        flags.append({"code": "CHAIZI_INCONSISTENT",
                      "msg": (f"self_consistent_ratio={consistent_ratio} < 0.4·"
                              "拆字字典验证不足")})
    out["flags"] = flags

    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "chaizi_ledger", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "Brill/kfcd/Kelly·advisory·绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] chaizi: {flags[0]['msg']} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="拆字/字谜 ledger scanner (shadow)")
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
