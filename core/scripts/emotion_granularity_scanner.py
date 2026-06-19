#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotion_granularity_scanner.py — 情绪颗粒度粗（粗类情绪大词裸用）检测（advisory · cluster · 2026-06-19）

【缺口】SAGE (Emotional Granularity) 实证：高情绪颗粒度 = 用细分情绪词（不甘/讪讪/悻悻/
怅惘/悸动…）精准命名感受；低颗粒度 = 用四大类粗情绪大词（愤怒/悲伤/高兴/害怕）直陈。
弱模型/AI 草稿偏好低颗粒度大词。全库已有 subtext_rescan 查「引导词+情绪」on-the-nose 结构，
但【没有】scanner 查【裸粗情绪大词词频】本身——维度不同（词汇颗粒度 vs 直陈结构）。本 scanner 补这一格。

【与 subtext_rescan 去重 · 维度正交】：
  · subtext_rescan = 「引导词 + 情绪名词」紧邻（感到愤怒 / 心中充满悲伤）= on-the-nose **结构**。
  · 本 scanner = 粗情绪大词【裸词频】（不论有无引导词·愤怒/悲伤/高兴/害怕 等四大类粗类裸用）
    = 词汇**颗粒度**。同一句「他愤怒」无引导词 → subtext_rescan 不计 · 本 scanner 计。
  两者交集（感到愤怒）会被各自按各自维度计 · 但判据不同（结构密度 vs 颗粒度词频）·不重复门禁。

【做法 · 确定性可算半边】（北极星守卫：情绪表达质量是语义判断·只做可算的裸词频·裁决留 judge/作者）：
  统计四大类粗情绪大词（COARSE_EMOTION）裸用次数 → coarse_emotion_per_1k。超 floor = 情绪靠
  粗大词直陈·缺细分词（不甘/讪讪/悻悻/怅惘/悸动）→ advisory「建议换细分词或结构化呈现」。
  单向检测（只报偏高·偏低永不报·北极星③）。有作者档读基线 z-band·无档用通用 floor。

【北极星⑤ 顾问非法官】粗大词有时合理（高潮直给/快节奏短打）·writer 有理由可偏离 → 永远
  advisory，code EMOTION_GRANULARITY_COARSE **绝不进 audit_hub.HARD_GATE_CODES**。
  env EMOTION_GRANULARITY_MODE: off / shadow(默认·只记不判) / active。
  🔬 阈值 COARSE_EMOTION_PER_1K_FLOOR 待金标准校准（真作者原文喂自身 PASS·防矫枉过正）。

用法：python emotion_granularity_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "EMOTION_GRANULARITY_COARSE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 四大类粗情绪大词（怒/悲/喜/惧 + 痛苦/绝望/愤恨等强度变体）—— 低颗粒度直陈
# vs 细分词（不甘/讪讪/悻悻/怅惘/悸动…·这些是高颗粒度·本 scanner 不计=正向）
COARSE_EMOTION = re.compile(
    r"(愤怒|恼怒|生气|悲伤|伤心|难过|高兴|开心|快乐|喜悦|"
    r"害怕|恐惧|惊恐|愤恨|痛苦|绝望|happy|sad)"
)
# 🔬 待金标准校准（真作者原文喂自身）：粗情绪大词裸词频超此/千字 = 颗粒度偏粗。
# 保守占位（宁可漏报不误报）·真作者基线实测后下调收紧。
COARSE_EMOTION_PER_1K_FLOOR = 3.0
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("EMOTION_GRANULARITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_coarse_emotions(text: str) -> list:
    """检测粗情绪大词裸用 hits（不论有无引导词·与 subtext_rescan 结构维度正交）。"""
    text = _strip_changes(text)
    return [{"word": m.group(0), "pos": m.start()} for m in COARSE_EMOTION.finditer(text)]


def _author_floor(project_root):
    """读作者档 emotion_granularity_profile 的 z-band 上沿作 floor。无档/无字段 → None。

    参考模板：作者档若有 coarse_emotion_per_1k_mean / _std → floor = mean + 2σ（z-band 上沿）。
    无作者档则调用方回退通用 COARSE_EMOTION_PER_1K_FLOOR。
    """
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(prof, dict):
        return None
    eg = prof.get("emotion_granularity_profile")
    if not isinstance(eg, dict):
        return None
    mean = eg.get("coarse_emotion_per_1k_mean")
    std = eg.get("coarse_emotion_per_1k_std")
    if not isinstance(mean, (int, float)):
        return None
    sigma = std if isinstance(std, (int, float)) else 0.0
    return round(float(mean) + 2.0 * float(sigma), 3)


def scan(draft_path, project_root=None) -> dict:
    """情绪颗粒度粗（粗类情绪大词裸用密度）检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "emotion_granularity", "schema_version": "1.0", "mode": mode,
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    hits = detect_coarse_emotions(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["coarse_emotion_count"] = len(hits)
    out["cjk_count"] = cjk
    out["coarse_emotion_per_1k"] = per_1k
    out["sample_words"] = [h["word"] for h in hits[:8]]

    # 有作者档读 z-band 上沿作 floor·无档用通用 floor
    author_floor = _author_floor(project_root)
    floor = author_floor if author_floor is not None else COARSE_EMOTION_PER_1K_FLOOR
    out["floor_used"] = floor
    out["author_baseline"] = author_floor

    msg = None
    if per_1k > floor:
        msg = (f"情绪颗粒度偏粗：粗情绪大词裸用 {per_1k}/千字 > {floor}"
               f"（{len(hits)} 处·愤怒/悲伤/高兴/害怕 等四大类直陈）·"
               f"缺细分词（不甘/讪讪/悻悻/怅惘/悸动）·建议换细分词或结构化呈现")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "emotion_granularity_coarse", "severity": "minor",
                "message": msg, "per_1k": per_1k, "count": len(hits),
                "floor_used": floor,
                "_doc": "情绪颗粒度是创作判断·粗大词有时合理(高潮直给/快节奏短打)→advisory 待裁决·"
                        "裸词频是可算半边粗糙哨兵·真情绪表达质量留 judge/作者",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] emotion_granularity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="情绪颗粒度粗(粗类情绪大词裸用)检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者 emotion_granularity 基线 z-band")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·active 有 warning 才 exit 1(不阻断·北极星⑤)
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
