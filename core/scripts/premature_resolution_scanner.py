#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""premature_resolution_scanner.py — 过早消解冲突检测（advisory · cluster · 2026-06-19）

【缺口】arXiv:2604.09854 (Spoiler Alert: Narrative Forecasting as a Metric for Tension)
实证 LLM 第一弱点：**过早消解冲突**（premature resolution）。
  - LLM 后段张力仅人类 1/3（no-rate 0.215 vs 0.607）
  - 峰值留存 23%（人类 52%）
  - 冲突/悬念提出后迅速消解 = 张力建立不起来 = 读者没有「担心」的时间

StoryScope (arXiv:2604.03136) 同时实证：AI 偏好「整洁单轨」情节 + Claude 产出 flat escalation。

【做法 · 确定性可算半边】：
  1. 检测冲突标志词（危机/困境/威胁/问题/矛盾等）
  2. 检测消解标志词（解决/化解/搞定/想通/迎刃而解等）
  3. 消解标志词在冲突标志词后 QUICK_WINDOW CJK 内出现 = 一次「快速消解」
  4. 快速消解占比超阈值 → advisory
  ⚠️ 只能测「显式标志词」的快速消解·隐含的冲突消解（靠情节展示）测不到·留 judge/作者。

【北极星⑤ 顾问非法官】消解节奏是创作选择（短打场景可能需要快速消解）·writer 有理由偏离
  → 永远 advisory，code PREMATURE_RESOLUTION **绝不进 audit_hub.HARD_GATE_CODES**。
  env PREMATURE_RESOLUTION_MODE: off / shadow(默认·只记不判) / active。

用法：python premature_resolution_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PREMATURE_RESOLUTION"

# 冲突/危机标志词（情况变糟·stakes 升高）
CONFLICT_MARKERS = re.compile(
    r"(危机|困境|威胁|矛盾|冲突|险境|麻烦|陷阱|危险|噩耗|坏消息|"
    r"遭到|被困|来不及|糟糕|完了|大祸|出事|不妙|紧迫|挑衅|"
    r"攻击|突袭|包围|逼近|倒下|受伤|中毒|失控|崩溃)")

# 消解/解决标志词（情况好转·stakes 降低）
RESOLUTION_MARKERS = re.compile(
    r"(解决了|化解了|搞定了|想通了|迎刃而解|问题解决|危机解除|"
    r"总算.*安全|终于.*脱险|松了.*口气|转危为安|有惊无险|"
    r"幸好|还好|好在|万幸|终于.*成功|终于.*明白|"
    r"原来如此.*这才|问题不大|没什么大碍|虚惊一场)")

QUICK_WINDOW = 500
QUICK_RATIO_MINOR = 0.50
QUICK_RATIO_MAJOR = 0.75
MIN_CONFLICTS = 3
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("PREMATURE_RESOLUTION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _cjk_distance(text: str, start: int, end: int) -> int:
    """计算 text[start:end] 中的 CJK 字符数。"""
    return sum(1 for ch in text[start:end] if "一" <= ch <= "鿿")


def detect_premature_resolutions(text: str) -> dict:
    """检测冲突-消解对的时间距离。返回 {conflicts, resolutions, quick_pairs, ratio}。"""
    text = _strip_changes(text)
    conflicts = [(m.start(), m.group(0)) for m in CONFLICT_MARKERS.finditer(text)]
    resolutions = [(m.start(), m.group(0)) for m in RESOLUTION_MARKERS.finditer(text)]

    if not conflicts:
        return {"conflict_count": 0, "resolution_count": len(resolutions),
                "quick_count": 0, "quick_ratio": 0.0, "pairs": []}

    quick_pairs = []
    for c_pos, c_word in conflicts:
        for r_pos, r_word in resolutions:
            if r_pos <= c_pos:
                continue
            dist = _cjk_distance(text, c_pos, r_pos)
            if dist <= QUICK_WINDOW:
                quick_pairs.append({
                    "conflict": c_word, "resolution": r_word,
                    "cjk_distance": dist})
                break

    ratio = len(quick_pairs) / len(conflicts) if conflicts else 0.0
    return {
        "conflict_count": len(conflicts),
        "resolution_count": len(resolutions),
        "quick_count": len(quick_pairs),
        "quick_ratio": round(ratio, 3),
        "pairs": quick_pairs[:6],
    }


def scan(draft_path, project_root=None) -> dict:
    """过早消解冲突检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "premature_resolution", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 2000:
        out["note"] = "草稿太短·跳过"
        return out

    result = detect_premature_resolutions(draft)
    out["metrics"] = result

    if result["conflict_count"] < MIN_CONFLICTS:
        out["note"] = f"冲突标志词不足{MIN_CONFLICTS}个·样本太少跳过"
        return out

    ratio = result["quick_ratio"]
    if ratio >= QUICK_RATIO_MAJOR:
        severity = "major"
        msg = (f"过早消解冲突严重（{ratio:.0%} 的冲突在 {QUICK_WINDOW} CJK 内被消解 > {QUICK_RATIO_MAJOR:.0%}）。"
               f"LLM 第一弱点（arXiv:2604.09854）：冲突提出后必须 simmer——"
               f"读者需要「担心」的时间。建议：冲突→挣扎→部分进展→遗留未解")
    elif ratio >= QUICK_RATIO_MINOR:
        severity = "minor"
        msg = (f"快速消解冲突偏多（{ratio:.0%} 的冲突在 {QUICK_WINDOW} CJK 内被消解 > {QUICK_RATIO_MINOR:.0%}）。"
               f"建议让冲突 simmer 更久——至少跨场景再有消解迹象")
    else:
        return out

    if mode == "active":
        out["violations"].append({
            "kind": "premature_resolution", "severity": severity,
            "message": msg, "quick_ratio": ratio,
            "sample_pairs": result["pairs"][:4],
            "_doc": "arXiv:2604.09854 LLM 第一弱点·消解节奏是创作选择·advisory"})
        out["verdict"] = f"FAIL_{severity.upper()}"
        out["warning"] = msg
    else:
        print(f"[SHADOW] premature_resolution: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="过早消解冲突检测(advisory)")
    ap.add_argument("draft", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    r = scan(args.draft, args.project)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
