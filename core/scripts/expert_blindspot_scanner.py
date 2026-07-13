#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07 实体追踪（术语再提及识别）接入 nn_coref_bridge
"""expert_blindspot_scanner.py — 专家盲点 (Nathan-Koedinger) · R24 W12 Batch-JJ · P1

【缺口 · Nathan-Koedinger 专家盲点 / K-12 教育叙事】
教育心理学：专家会高估读者对术语的熟悉度（expert blindspot）·一次锚定后
长距离不再具体化 → 新读者跟丢。当前 writer 偶尔引入术语后只在第一次具体
锚定（动作/物件/感官描述），之后裸用 → 跟读断层。

【做法 · 确定性 · 零 LLM/零联网】
  · 候选术语启发：双引号包裹词「XX」/4-char CJK 复合名词候选
  · 维护首次引登记 first_intro_pos
  · 术语出现位置 = 字面重复匹配 ∪ nn_coref_bridge 解析出的指代该术语的代词位置
    （辅助实体追踪：判断"它"/"那个人"等指代的是哪个已提及术语，弥补字面匹配的盲区；
    RUOYU_NN_COREF 门控关闭默认状态 / 桥无结果 → 100% 回退纯字面位置列表）
  · 每次后续出现计算距上次「具体锚定」距离（CJK chars）
    具体锚定 = 同段内出现具体名词/动作动词/感官词（占位词典）
  · 阈值（占位）：base 2000 CJK / 复杂规则（标 _is_complex 候选）800 CJK
  · POV 内心独白不计（POV 段过滤）
  · 输出 top-5 drift_unanchored 术语（max gap）+ 建议 anchor 时机

【三 advisory】
  · EXPERT_BLINDSPOT_DRIFT       — 术语后续出现 gap > 阈值（无新锚）
  · EXPERT_BLINDSPOT_DRIFT_COMPLEX — 复杂规则术语 gap > 800
  · EXPERT_BLINDSPOT_OK          — 无 drift 命中（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  EXPERT_BLINDSPOT_* 绝不进 audit_hub.HARD_GATE_CODES。

env EXPERT_BLINDSPOT_MODE: off / shadow（默认） / active
用法: python expert_blindspot_scanner.py <draft>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DRIFT = "EXPERT_BLINDSPOT_DRIFT"
ISSUE_CODE_DRIFT_COMPLEX = "EXPERT_BLINDSPOT_DRIFT_COMPLEX"
ISSUE_CODE_OK = "EXPERT_BLINDSPOT_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

GAP_BASE = 2000
GAP_COMPLEX = 800
TOP_K = 5

# 具体锚定词（占位）：感官/动作/物件
_ANCHOR_WORDS = {
    "_placeholder": True,
    "sensory": ["看", "听", "闻", "触", "尝", "感到", "握", "推", "拉",
                "敲", "响", "声", "光", "气味", "凉", "热", "冷"],
    "concrete": ["桌", "椅", "门", "窗", "杯", "刀", "剑", "纸", "墨",
                 "灯", "石", "木", "铁", "布", "线", "墙"],
    "action": ["走", "跑", "坐", "起", "蹲", "停", "转", "抬", "低头",
               "伸手", "退", "拍", "摸"],
}

# 标 _is_complex 候选：含数字/规则/特殊符号的术语
_COMPLEX_PATTERNS = (
    re.compile(r"[\d一二三四五六七八九十]条|[\d]+阶|规则|条款|定律|协议|公约"),
)

# 双引号包裹词（中文）
_QUOTED_RE = re.compile(r"[「『]([^「」『』]{2,12})[」』]|[“]([^“”]{2,12})[”]")
_CJK4_RE = re.compile(r"[一-龥]{4}")

# POV 内心独白启发：包裹在引号外的「他想」「她想」段
_POV_MARKER_RE = re.compile(r"(他|她|我)(想|心想|暗道|寻思|忖度)")


def _mode() -> str:
    m = (os.environ.get("EXPERT_BLINDSPOT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _cjk_positions(text: str) -> list[int]:
    """返回所有 CJK 字符在 raw text 的索引（用于按 CJK 距离查窗口）"""
    return [i for i, ch in enumerate(text) if "一" <= ch <= "鿿"]


def _extract_term_candidates(text: str) -> list[str]:
    """候选术语：双引号包裹词 + 高频 4-CJK 字组合（频次 >= 3 视作概念术语）"""
    cand = set()
    for m in _QUOTED_RE.finditer(text):
        term = m.group(1) or m.group(2)
        if term and 2 <= len(term) <= 12:
            cand.add(term)
    # 4-CJK 高频
    counter = {}
    for m in _CJK4_RE.finditer(text):
        t = m.group(0)
        counter[t] = counter.get(t, 0) + 1
    for t, n in counter.items():
        if n >= 3:
            cand.add(t)
    return list(cand)


def _term_is_complex(term: str) -> bool:
    for pat in _COMPLEX_PATTERNS:
        if pat.search(term):
            return True
    return False


def _find_term_positions(text: str, term: str) -> list[int]:
    """返回 term 在 text 中的所有起点 raw index"""
    if not term:
        return []
    positions = []
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            break
        positions.append(i)
        start = i + 1
    return positions


def _resolve_coref_for_terms(text: str, terms: list[str]) -> dict[str, list[int]]:
    """调用 nn_coref_bridge 解析 text 中指代 → {术语: [代词指代该术语的位置...]}。

    辅助实体追踪：判断"它"/"那个人"等指代的是哪个已提及术语/实体，弥补
    _find_term_positions 只认字面重复字符串的盲区。
    RUOYU_NN_COREF 门控关闭（默认）/ 无候选术语 / 任何异常 → 返回空 dict，
    上层 scan() 100% 回退现有纯字符串距离词典逻辑（positions 只用字面匹配）。
    """
    if not terms:
        return {}
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from nn_coref_bridge import resolve_coreferences
        results = resolve_coreferences(text, terms)
    except Exception as e:  # noqa: BLE001 — 桥失败绝不影响本 scanner 主流程
        print(f"[expert_blindspot_scanner] 共指消解失败·回退字符串距离词典："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return {}
    out: dict[str, list[int]] = {}
    for r in (results or []):
        span = r.get("span")
        resolved = r.get("resolved_to")
        if not span or resolved not in terms:
            continue
        out.setdefault(resolved, []).append(span[0])
    return out


def _is_pov_window(text: str, pos: int, window: int = 60) -> bool:
    """位置 pos 附近 ±window CJK 是否在 POV 内心独白段·POV 段不计 gap"""
    lo = max(0, pos - window)
    hi = min(len(text), pos + window)
    snippet = text[lo:hi]
    return bool(_POV_MARKER_RE.search(snippet))


def _has_anchor_in_window(text: str, lo: int, hi: int) -> bool:
    """窗口 [lo, hi] 内是否有具体锚定词（任一感官/动作/物件命中）"""
    snippet = text[lo:hi]
    for key in ("sensory", "concrete", "action"):
        for w in _ANCHOR_WORDS[key]:
            if w in snippet:
                return True
    return False


def _cjk_gap(text: str, pos_a: int, pos_b: int) -> int:
    """text[pos_a:pos_b] 之间 CJK 字符数"""
    if pos_b <= pos_a:
        return 0
    return _cjk_count(text[pos_a:pos_b])


def scan(draft_path) -> dict:
    mode = _mode()
    out = {
        "scanner": "expert_blindspot_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _ANCHOR_WORDS.get("_placeholder", True),
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
    if cjk < 2000:
        out["note"] = "草稿太短·跳过"
        return out

    candidates = _extract_term_candidates(text)
    if not candidates:
        out["note"] = "无术语候选"
        out["term_count"] = 0
        return out

    # 🔴 NN共指桥（2026-07）：辅助实体追踪·门控关闭(默认)/无结果/异常 → 空 dict
    #    下面逐术语从中并入指代位置·为空时 positions 100% 回退纯字面匹配
    coref_by_term = _resolve_coref_for_terms(text, candidates)

    drift_records = []  # (term, max_gap, is_complex, last_pos)
    for term in candidates:
        positions = _find_term_positions(text, term)
        coref_positions = coref_by_term.get(term)
        if coref_positions:
            positions = sorted(set(positions) | set(coref_positions))
        if len(positions) < 2:
            continue
        is_complex = _term_is_complex(term)
        threshold = GAP_COMPLEX if is_complex else GAP_BASE
        first_pos = positions[0]
        last_anchor_pos = first_pos  # 首引算锚定一次
        max_gap_this = 0
        flagged = False
        for pos in positions[1:]:
            if _is_pov_window(text, pos):
                continue
            # 窗口 = [last_anchor_pos, pos]
            if _has_anchor_in_window(text, last_anchor_pos, pos):
                last_anchor_pos = pos
                continue
            gap = _cjk_gap(text, last_anchor_pos, pos)
            if gap > max_gap_this:
                max_gap_this = gap
            if gap > threshold:
                flagged = True
                last_anchor_pos = pos
        if flagged:
            drift_records.append({
                "term": term,
                "max_gap_cjk": max_gap_this,
                "is_complex": is_complex,
                "occurrences": len(positions),
                "threshold": threshold,
            })

    # 按 max_gap 排序 top-K
    drift_records.sort(key=lambda r: r["max_gap_cjk"], reverse=True)
    top_drift = drift_records[:TOP_K]

    out.update({
        "cjk": cjk,
        "term_candidates": len(candidates),
        "drift_term_count": len(drift_records),
        "top_drift": top_drift,
        # 🔴 NN 共指桥集成（2026-07）：source 区分术语位置是否用到桥解析出的指代
        "coref_resolution_source": "nn_coref_bridge" if coref_by_term else "regex_fallback",
        "coref_resolved_mentions": sum(len(v) for v in coref_by_term.values()),
    })

    flags = []
    for rec in top_drift:
        code = ISSUE_CODE_DRIFT_COMPLEX if rec["is_complex"] else ISSUE_CODE_DRIFT
        flags.append({
            "code": code,
            "msg": (f"术语「{rec['term']}」gap={rec['max_gap_cjk']} CJK"
                    f" > 阈值 {rec['threshold']}·建议补具体锚定"),
            "severity": "minor",
        })
    if not flags:
        flags.append({
            "code": ISSUE_CODE_OK,
            "msg": "无 expert blindspot drift 命中",
            "severity": "info",
        })

    msg = "·".join(f["msg"] for f in flags[:3])
    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "expert_blindspot",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": "R24 W12 Batch-JJ·Nathan-Koedinger·advisory·绝不 hard_gate"})
        any_minor = any(v["severity"] == "minor" for v in out["violations"])
        out["verdict"] = "FAIL_MINOR" if any_minor else "PASS"
        out["warning"] = msg if any_minor else None
    elif mode == "shadow" and any(f["severity"] == "minor" for f in flags):
        print(f"[SHADOW] expert_blindspot: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="专家盲点 advisory shadow")
    ap.add_argument("draft_path")
    args = ap.parse_args()
    rep = scan(args.draft_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
