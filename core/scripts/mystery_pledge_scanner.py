#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mystery_pledge_scanner.py — 章开谜题契约 (Cialdini) · R24 W12 Batch-JJ · P1

【缺口 · Cialdini 6 影响力之 commitment+consistency / K-12 教育叙事】
教师式叙事教育研究：开篇下谜题承诺（anomaly_seed + 契约承诺词）→ 末段回应
（reveal marker）= 一致性契约，读者完成率显著上升。当前 writer 偶尔开篇
平铺直叙无 anomaly，或开了 anomaly 末段不揭 → pledge_dangling。

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 首 200 CJK 检 anomaly_seed 词（异常/反常/奇怪/不对劲/...）
    + pledge 承诺词（究竟/到底/为何/必有/...）
  · 末 500 CJK 检 reveal marker（原来/真相/竟是/果然/答案/...）
  · 三状态签到：
    - pledge_kept   : anomaly 有 + reveal 有 → info
    - pledge_dangling: anomaly 有 + reveal 无 → advisory（开了契约不兑）
    - cold_open     : 无 anomaly 无 pledge → info（白叙事开场·可豁免）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  MYSTERY_PLEDGE_* 绝不进 audit_hub.HARD_GATE_CODES。

env MYSTERY_PLEDGE_MODE: off / shadow（默认） / active
用法: python mystery_pledge_scanner.py <draft>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DANGLING = "MYSTERY_PLEDGE_DANGLING"
ISSUE_CODE_COLD_OPEN = "MYSTERY_COLD_OPEN"
ISSUE_CODE_KEPT = "MYSTERY_PLEDGE_KEPT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

OPEN_CJK = 200
END_CJK = 500

_LEXICONS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-JJ·Cialdini commitment+consistency·占位词典",
    "anomaly_seed": [
        "异常", "反常", "奇怪", "诡异", "不对劲", "不寻常", "不合常理",
        "古怪", "怪异", "蹊跷", "诡谲", "出奇",
    ],
    "pledge": [
        "究竟", "到底", "为何", "为什么", "必有", "莫非", "难道",
        "究竟是", "却不知", "无人知晓", "不得而知",
    ],
    "reveal": [
        "原来", "真相", "竟是", "果然", "答案", "终于明白", "这才知",
        "这才发现", "破解", "揭开", "揭露", "真凶", "真相大白",
    ],
}


def _mode() -> str:
    m = (os.environ.get("MYSTERY_PLEDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _take_open(text: str, max_cjk: int = OPEN_CJK) -> str:
    """从开头取 max_cjk CJK 字符"""
    buf, c = [], 0
    for ch in text:
        buf.append(ch)
        if "一" <= ch <= "鿿":
            c += 1
        if c >= max_cjk:
            break
    return "".join(buf)


def _take_end(text: str, max_cjk: int = END_CJK) -> str:
    """从末尾取 max_cjk CJK 字符"""
    buf, c = [], 0
    for ch in reversed(text):
        buf.append(ch)
        if "一" <= ch <= "鿿":
            c += 1
        if c >= max_cjk:
            break
    return "".join(reversed(buf))


def _count_hits(text: str, words: list[str]) -> tuple[int, list[str]]:
    hits = []
    total = 0
    for w in words:
        n = text.count(w)
        if n > 0:
            hits.append(w)
            total += n
    return total, hits


def scan(draft_path) -> dict:
    mode = _mode()
    out = {
        "scanner": "mystery_pledge_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _LEXICONS.get("_placeholder", True),
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
    if cjk < 600:
        out["note"] = "草稿太短·跳过"
        return out

    open_seg = _take_open(text, OPEN_CJK)
    end_seg = _take_end(text, END_CJK)
    anomaly_n, anomaly_hits = _count_hits(open_seg, _LEXICONS["anomaly_seed"])
    pledge_n, pledge_hits = _count_hits(open_seg, _LEXICONS["pledge"])
    reveal_n, reveal_hits = _count_hits(end_seg, _LEXICONS["reveal"])

    has_anomaly = (anomaly_n + pledge_n) > 0
    has_reveal = reveal_n > 0

    state = "cold_open"
    code, severity, msg = None, "info", None
    if has_anomaly and has_reveal:
        state = "pledge_kept"
        code = ISSUE_CODE_KEPT
        msg = (f"开篇 anomaly+pledge={anomaly_n + pledge_n} → "
               f"末段 reveal={reveal_n}·契约兑现")
        severity = "info"
    elif has_anomaly and not has_reveal:
        state = "pledge_dangling"
        code = ISSUE_CODE_DANGLING
        msg = (f"开篇下了 anomaly+pledge={anomaly_n + pledge_n}"
               f"·末段无 reveal·契约 dangling")
        severity = "minor"
    else:
        state = "cold_open"
        code = ISSUE_CODE_COLD_OPEN
        msg = "首 200 CJK 无 anomaly/pledge·cold open 白叙事开场"
        severity = "info"

    out.update({
        "cjk": cjk,
        "open_cjk": _cjk_count(open_seg),
        "end_cjk": _cjk_count(end_seg),
        "anomaly_seed_hits": anomaly_hits,
        "pledge_hits": pledge_hits,
        "reveal_hits": reveal_hits,
        "anomaly_count": anomaly_n,
        "pledge_count": pledge_n,
        "reveal_count": reveal_n,
        "state": state,
    })

    if mode == "active" and code:
        out["violations"].append({
            "kind": "mystery_pledge",
            "severity": severity,
            "code": code, "message": msg,
            "_doc": "R24 W12 Batch-JJ·Cialdini commitment+consistency·advisory·绝不 hard_gate"})
        if severity == "minor":
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            out["warning"] = msg if state != "cold_open" else None
    elif mode == "shadow" and severity == "minor":
        print(f"[SHADOW] mystery_pledge: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="章开谜题契约 advisory shadow")
    ap.add_argument("draft_path")
    args = ap.parse_args()
    rep = scan(args.draft_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
