#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frisson_lead_window_scanner.py — 战栗导入窗 vs 高潮锐化 advisory · R23 W11 Batch-HH · P1

【缺口 · 神经美学 frisson（音乐审美战栗 / SCANs Salimpoor 2011）】神经美学：
战栗（frisson / aesthetic chills）的诱发在「climax beat 前 1-2 段」才到峰值，
不在 climax 句本身（前置预期+延迟回报理论 Huron）。当前 writer 默认在 climax
句堆砌情绪标点 → 战栗信号已经被「平进式预期落空」吃掉 → 读者体感平。

【做法 · 确定性 · 零 LLM/零联网】
  · 切段落 → 找疑似 climax 段（情绪标点 / PE 词最密的段）
  · climax 段前 1-2 段（约 200-400 CJK）= frisson lead window
  · 测 lead vs climax 标点密度 + 单句独行占比
  · 期望 lead_density / climax_density >= 1.2x（前置锐化）
  · 否则 → FRISSON_LEAD_FLAT advisory

【build_manifest 注入 frisson_lead_window 字段】
  - mode=active 时给 writer prompt 注入 directive：lead window 200-400 CJK 节奏锐化
    （短句独行 + 标点高潮 + 感官聚焦），climax 句相对略钝（情绪降落式）。

【三 advisory】
  · FRISSON_LEAD_FLAT          — lead window 标点 / 独行率 < 1.2x climax
  · FRISSON_CLIMAX_OVERLOAD    — climax 段标点密度 > lead 2x（前置预期未铺满）
  · FRISSON_NO_CLIMAX_FOUND    — 全文标点平淡找不到 climax 段（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  FRISSON_* 绝不进 audit_hub.HARD_GATE_CODES。

env FRISSON_LEAD_MODE: off / shadow（默认） / active
用法: python frisson_lead_window_scanner.py <draft>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_FLAT = "FRISSON_LEAD_FLAT"
ISSUE_CODE_OVERLOAD = "FRISSON_CLIMAX_OVERLOAD"
ISSUE_CODE_NO_CLIMAX = "FRISSON_NO_CLIMAX_FOUND"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

LEAD_RATIO_MIN = 1.2          # lead/climax 期望比率
CLIMAX_OVERLOAD_RATIO = 2.0   # climax/lead 过载比率
LEAD_WINDOW_CJK_MIN = 200
LEAD_WINDOW_CJK_MAX = 400

_EMOTION_PUNCT_RE = re.compile(r"[！？]+|……+|—{2,}")
_SHORT_SOLO_RE = re.compile(r"^.{0,12}[。！？]$")


def _mode() -> str:
    m = (os.environ.get("FRISSON_LEAD_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_paragraphs(text: str) -> list[str]:
    """段切分：仅按空行（≥1 个 \\n + 可选空白 + 至少 1 个 \\n）切·段内 \\n 保留以让 short_solo 计算独行率"""
    parts = re.split(r"\n\s*\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _punct_density(text: str) -> float:
    cjk = _cjk_count(text)
    if cjk == 0:
        return 0.0
    return len(_EMOTION_PUNCT_RE.findall(text)) / (cjk / 1000)


def _short_solo_ratio(paragraph: str) -> float:
    lines = [l.strip() for l in paragraph.split("\n") if l.strip()]
    if not lines:
        return 0.0
    short = sum(1 for l in lines if _cjk_count(l) <= 12)
    return short / len(lines)


def _find_climax_paragraph_idx(paragraphs: list[str]) -> int:
    """启发：标点密度 + 短句独行率联合分最高的段

    12 CJK 下限：高密度 climax 段常很短（短句独行+情绪标点）·过滤太小防噪音。
    """
    best_idx = -1
    best_score = 0.0
    for i, p in enumerate(paragraphs):
        cjk = _cjk_count(p)
        if cjk < 12:
            continue
        score = _punct_density(p) * 0.7 + _short_solo_ratio(p) * 0.3 * 30
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _build_lead_window(paragraphs: list[str], climax_idx: int) -> tuple[str, list[int]]:
    """从 climax 前 1-2 段拼 lead window，目标 200-400 CJK。"""
    if climax_idx <= 0:
        return "", []
    indices = []
    accum = ""
    i = climax_idx - 1
    while i >= 0 and _cjk_count(accum) < LEAD_WINDOW_CJK_MIN:
        accum = paragraphs[i] + "\n" + accum
        indices.insert(0, i)
        i -= 1
        if _cjk_count(accum) >= LEAD_WINDOW_CJK_MIN:
            break
    # 钳到最多 LEAD_WINDOW_CJK_MAX
    while _cjk_count(accum) > LEAD_WINDOW_CJK_MAX and indices:
        first = indices[0]
        para = paragraphs[first]
        cut_len = max(0, len(para) - (_cjk_count(accum) - LEAD_WINDOW_CJK_MAX) * 2)
        para = para[cut_len:]
        accum = para + "\n" + "\n".join(paragraphs[j] for j in indices[1:])
        indices = indices[:]  # noop（保持索引）
        break
    return accum.strip(), indices


def scan(draft_path) -> dict:
    mode = _mode()
    out = {"scanner": "frisson_lead_window", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
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

    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 5:
        out["note"] = "段落太少·跳过"
        return out

    climax_idx = _find_climax_paragraph_idx(paragraphs)
    if climax_idx < 0:
        out["note"] = "未检出 climax 段"
        # info advisory only
        if mode == "active":
            out["violations"].append({
                "kind": "frisson_lead_window", "severity": "info",
                "code": ISSUE_CODE_NO_CLIMAX,
                "message": "标点平淡·找不到 climax 段",
                "_doc": "neural frisson·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["warning"] = "标点平淡·找不到 climax 段"
        out["violations_count"] = len(out["violations"])
        return out

    climax_para = paragraphs[climax_idx]
    lead_text, lead_indices = _build_lead_window(paragraphs, climax_idx)
    climax_density = _punct_density(climax_para)
    lead_density = _punct_density(lead_text) if lead_text else 0.0
    climax_solo = _short_solo_ratio(climax_para)
    lead_solo = _short_solo_ratio(lead_text) if lead_text else 0.0

    # 合成 frisson 锐化指标（lead vs climax 比率）
    if climax_density > 0.01:
        punct_ratio = lead_density / climax_density
    else:
        punct_ratio = 1.0
    if climax_solo > 0.01:
        solo_ratio = lead_solo / climax_solo
    else:
        solo_ratio = 1.0
    combined_ratio = max(punct_ratio, solo_ratio)  # 取较高方向

    out.update({
        "cjk": cjk,
        "paragraphs": len(paragraphs),
        "climax_idx": climax_idx,
        "lead_indices": lead_indices,
        "lead_cjk": _cjk_count(lead_text),
        "climax_cjk": _cjk_count(climax_para),
        "lead_punct_density": round(lead_density, 3),
        "climax_punct_density": round(climax_density, 3),
        "lead_solo_ratio": round(lead_solo, 3),
        "climax_solo_ratio": round(climax_solo, 3),
        "lead_to_climax_ratio": round(combined_ratio, 3),
        "thresholds": {"lead_min_ratio": LEAD_RATIO_MIN,
                       "climax_overload_ratio": CLIMAX_OVERLOAD_RATIO},
    })

    flags = []
    if combined_ratio < LEAD_RATIO_MIN:
        flags.append({
            "code": ISSUE_CODE_FLAT,
            "msg": (f"lead/climax 锐化比率 {combined_ratio:.2f} < {LEAD_RATIO_MIN}"
                    f"·frisson 路径前置预期未铺满"),
            "severity": "minor",
        })
    # climax 过载（lead 远低于 climax）
    if punct_ratio < (1.0 / CLIMAX_OVERLOAD_RATIO) and climax_density > 0.5:
        flags.append({
            "code": ISSUE_CODE_OVERLOAD,
            "msg": (f"climax/lead 密度 {(1.0 / max(punct_ratio, 0.001)):.2f}x"
                    f" > {CLIMAX_OVERLOAD_RATIO}·堆 climax 而没铺 lead"),
            "severity": "minor",
        })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "frisson_lead_window",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "neural frisson 时序前置·R23 W11 Batch-HH·advisory·绝不 hard_gate"})
            out["verdict"] = ("FAIL_MINOR" if any(
                v["severity"] == "minor" for v in out["violations"]) else "PASS")
            out["warning"] = msg
        else:
            print(f"[SHADOW] frisson_lead_window: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="frisson lead window advisory shadow")
    ap.add_argument("draft_path")
    args = ap.parse_args()
    rep = scan(args.draft_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
