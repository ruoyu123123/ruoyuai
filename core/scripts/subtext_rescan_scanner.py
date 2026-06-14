#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subtext_rescan_scanner.py — W1 潜台词/on-the-nose 情绪直陈回查（advisory · cluster · 2026-06-15）

【缺口】consolidate 产作者 subtext_instance_count（dim30 留白潜台词基线）+ style_injector 注入
subtext_per_chapter_target（默认 2/章）给 writer·但 writer 写完【零回查】草稿潜台词密度
（记忆调研 W1 实证：埋设端齐备·回查端 ZERO·全库 0 个 subtext/theme scanner）。本 scanner
补这个闭环的【可算半边】：检测 on-the-nose 情绪直陈（说透情绪 vs 动作侧写）密度。

【做法 · 确定性可算半边】（北极星守卫：潜台词质量是语义判断·只做可算的密度·裁决留 judge/作者）：
  on-the-nose = 情绪引导词（感到/心中/充满了）+ 情绪状态名词（愤怒/悲伤/绝望）紧邻。
  CLAUDE.md 反 AI 腔③「动作>情绪词：不写他感到愤怒·写他把杯子摔在地上」——高 on-the-nose
  密度 = 说透情绪 = 低潜台词。密度超阈值 → advisory。
  ⚠️ semantic_slop.ABSTRACT_NOUN 是【主题概念大词】(命运/真相)·与本 scanner 的【情绪状态名词】
  正交不重叠（金句体 vs 情绪直陈两种 AI 腔）。

【北极星⑤ 顾问非法官】直陈情绪有时合理（高潮爆发/快节奏短打）·writer 有理由可偏离 → 永远
  advisory，code ON_THE_NOSE_EMOTION_DENSITY **绝不进 audit_hub.HARD_GATE_CODES**。
  env SUBTEXT_RESCAN_MODE: off / shadow(默认·只记不判) / active。
  🔬 阈值 ON_THE_NOSE_PER_1K_FLOOR 待金标准校准（真作者原文喂自身 PASS·防矫枉过正）。

用法：python subtext_rescan_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ON_THE_NOSE_EMOTION_DENSITY"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
ON_THE_NOSE_PER_1K_FLOOR = 3.0               # 每千字 on-the-nose 超此 = 说透过多（金标准校准:真作者最大1.14/千字[惊悚乐园0.36/遮天1.14]·留2.6x余量不误伤）

# 情绪状态名词（被「说透」的情绪·vs 动作侧写）—— 与 semantic_slop 主题大词正交
EMOTION_STATE_NOUN = re.compile(
    r"(愤怒|悲伤|恐惧|喜悦|绝望|痛苦|激动|紧张|兴奋|失望|愧疚|羞耻|焦虑|"
    r"恐慌|欣慰|惊讶|震惊|无奈|委屈|孤独|嫉妒|愉悦|忧伤|惶恐|哀伤|愤恨|"
    r"怨恨|思念|眷恋|渴望|期待|不安|烦躁|郁闷|悲哀|惊恐|羞愧|悔恨|喜悦)"
)
# 情绪直陈引导词（把情绪说透·非侧写）
EMOTION_LEAD = re.compile(
    r"(感到|感觉到|觉得|心中|心里|内心|心头|充满了|充满着|涌起|涌上|"
    r"涌现|油然而生|溢满|满是|尽是|一阵|阵阵)"
)
_LEAD_WINDOW = 8   # 引导词后 N 字内出现情绪名词 = on-the-nose 直陈
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("SUBTEXT_RESCAN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_on_the_nose(text: str) -> list:
    """检测 on-the-nose 情绪直陈：引导词后 _LEAD_WINDOW 字内有情绪名词。返回 hit list。

    同一情绪名词位置只算 1 次（去重·防「感到一阵愤怒」被「感到」+「一阵」双引导重复计虚高密度）。
    """
    text = _strip_changes(text)
    hits = []
    seen_emotion_pos = set()   # 情绪名词绝对位置去重
    for m in EMOTION_LEAD.finditer(text):
        window_start = m.end()
        em = EMOTION_STATE_NOUN.search(text[window_start:window_start + _LEAD_WINDOW])
        if em:
            emo_pos = window_start + em.start()
            if emo_pos in seen_emotion_pos:
                continue   # 同一情绪名词已被前一引导词计 · 去重跳过
            seen_emotion_pos.add(emo_pos)
            hits.append({
                "lead": m.group(0),
                "emotion": em.group(0),
                "preview": text[max(0, m.start() - 4):window_start + _LEAD_WINDOW + 2],
            })
    return hits


def scan(draft_path, manifest_path=None) -> dict:
    """on-the-nose 情绪直陈密度回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "subtext_rescan",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],           # 对齐 audit_hub._parse_violations_scanner（drift→1条·shadow 空）
        "verdict": "PASS",
    }
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短（<500 CJK）·on-the-nose 密度不可估·跳过"
        return out
    hits = detect_on_the_nose(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["on_the_nose_count"] = len(hits)
    out["cjk_count"] = cjk
    out["on_the_nose_per_1k"] = per_1k
    out["sample_hits"] = hits[:6]

    over = per_1k > ON_THE_NOSE_PER_1K_FLOOR
    if over:
        msg = (f"on-the-nose 情绪直陈密度 {per_1k}/千字 > {ON_THE_NOSE_PER_1K_FLOOR}"
               f"（说透情绪 {len(hits)} 处·潜台词少·建议改动作/物件侧写）")
        if mode == "active":
            out["violations"].append({
                "kind": "on_the_nose_emotion", "severity": "minor",
                "message": msg, "per_1k": per_1k, "count": len(hits),
                "_doc": "潜台词质量是创作判断·直陈有时合理(高潮爆发/快节奏短打)→advisory 待裁决·"
                        "密度是可算半边粗糙哨兵·真潜台词质量留 judge/作者",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] subtext_rescan: {msg} — 不上报判决", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="W1 潜台词/on-the-nose 情绪直陈回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参（保留·作者基线对账下一轮接）")
    ap.add_argument("--project", default=None, help="兼容 audit_hub 传参（读作者 subtext 基线·下一轮接）")
    args = ap.parse_args()
    report = scan(args.draft_path, args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·恒 exit 0(不阻断·北极星⑤)·active 有 warning 才 exit 1
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
