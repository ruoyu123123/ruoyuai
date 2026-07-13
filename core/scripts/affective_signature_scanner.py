#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""affective_signature_scanner.py — implied author 情感分布 KL 漂移
(advisory · cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (LLM congeniality bias documented · Plutchik 8 wheel): LLM 默认产
joy 通道膨胀+ disgust/anger 通道塌陷=亲和化偏置（congeniality skew）。此前全系统:
  · subtext_rescan_scanner / emotion_granularity / emotion_curve_rescan 查情绪侧
  · 【作者情感 signature KL 散度零检测】
本 scanner 补 Plutchik 8 类情感 lexicon → 草稿情感分布 → 作者档
author_affective_signature 标准分布 → KL 散度 → CONGENIALITY_SKEW flag。

【做法 · 确定性零依赖（lexicon 兜底）】:
  1. Plutchik 8 类：joy / trust / fear / surprise / sadness / disgust / anger / anticipation。
     每类 12-18 个中文情感词典(精选高确定性·剔除多义)。
  2. 草稿计每类命中数 → 归一化 → 草稿分布 P。
  3. 作者档 author_affective_signature{ 8 keys: probability } → 标准分布 Q。
     无作者档 → 通用兜底 Q(均匀分布 0.125 each)。
  4. KL(P||Q) 计算+ 通道偏移检测。
  5. flag CONGENIALITY_SKEW:
     · joy P/Q ratio > 1.5（joy 膨胀）+
     · anger 或 disgust P/Q ratio < 0.5（双通道塌陷）。

【北极星② / ⑤ 顾问非法官】情感分布是作者第一权威·无作者档不擅判·全 advisory，
  code CONGENIALITY_SKEW **绝不进 audit_hub.HARD_GATE_CODES**。
  env AFFECTIVE_SIGNATURE_MODE: off / shadow(默认) / active。

用法：python affective_signature_scanner.py <draft_path> [--project <root>]
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

ISSUE_CODE = "CONGENIALITY_SKEW"

# Plutchik 8 类情感 lexicon（精选中文情感词）
PLUTCHIK_LEXICON = {
    "joy": re.compile(
        r"高兴|愉快|喜悦|欣喜|开心|快活|畅快|欢乐|笑容|笑意|笑了|大喜|"
        r"舒畅|乐呵|惊喜|欢喜"),
    "trust": re.compile(
        r"信任|信赖|依赖|放心|安心|笃信|相信他|相信她|靠得住|可靠"
        r"|托付|交心|至诚"),
    "fear": re.compile(
        r"害怕|恐惧|恐慌|惊恐|惧怕|畏惧|惊惶|心惊|战栗|心慌|忐忑|发怵"
        r"|胆寒|惶恐"),
    "surprise": re.compile(
        r"惊讶|吃惊|愕然|惊愕|意外|没想到|怔住|怔了|猛地一惊|讶异"
        r"|目瞪|愣神|出乎"),
    "sadness": re.compile(
        r"悲伤|难过|悲痛|哀伤|忧伤|哀愁|凄凉|怅然|失落|落寞|垂泪|泪流"
        r"|心酸|心碎|惋惜"),
    "disgust": re.compile(
        r"厌恶|恶心|嫌弃|鄙夷|鄙视|不屑|讨厌|憎恶|腻烦|嫌恶|作呕|嫌怨"
        r"|轻蔑"),
    "anger": re.compile(
        r"愤怒|恼怒|气愤|愤恨|暴怒|怒火|怒目|怒气|怒喝|发怒|动怒|火冒"
        r"|盛怒|怒吼|怒视"),
    "anticipation": re.compile(
        r"期待|期盼|盼望|憧憬|向往|渴盼|盼着|希冀|寄望|心生期待|急切想"
        r"|翘首|拭目"),
}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("AFFECTIVE_SIGNATURE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    return load_json(p)


def _author_signature(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return None
    sig = obj.get("author_affective_signature")
    if not isinstance(sig, dict):
        return None
    cleaned = {}
    total = 0.0
    for k in PLUTCHIK_LEXICON:
        v = sig.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            cleaned[k] = float(v)
            total += v
    if total <= 0:
        return None
    return {k: cleaned.get(k, 0.0) / total for k in PLUTCHIK_LEXICON}


def _draft_distribution(text):
    counts = {}
    total = 0
    for k, rx in PLUTCHIK_LEXICON.items():
        c = len(rx.findall(text))
        counts[k] = c
        total += c
    if total == 0:
        return None, counts, 0
    return ({k: counts[k] / total for k in PLUTCHIK_LEXICON}, counts, total)


def _kl_divergence(P, Q, eps=1e-6):
    s = 0.0
    for k in P:
        p = P[k] + eps
        q = Q.get(k, 0.0) + eps
        s += p * math.log(p / q)
    return s


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "affective_signature", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    P, counts, total = _draft_distribution(text)
    out["plutchik_counts"] = counts
    out["plutchik_total_hits"] = total
    if total < 8:
        out["note"] = "情感词命中过少·样本不足"
        return out
    out["draft_distribution"] = {k: round(v, 4) for k, v in P.items()}

    Q = _author_signature(project_root)
    if Q is None:
        # 通用兜底（均匀）
        Q = {k: 1.0 / len(PLUTCHIK_LEXICON) for k in PLUTCHIK_LEXICON}
        out["baseline_source"] = "uniform_fallback"
    else:
        out["baseline_source"] = "author_profile"
    out["author_distribution"] = {k: round(v, 4) for k, v in Q.items()}

    kl = _kl_divergence(P, Q)
    out["kl_divergence"] = round(kl, 4)

    ratio = {}
    for k in PLUTCHIK_LEXICON:
        q = Q.get(k, 0.0)
        if q > 0:
            ratio[k] = P[k] / q if q else None
        else:
            ratio[k] = None
    out["channel_ratio"] = {k: round(v, 3) if v is not None else None
                            for k, v in ratio.items()}

    joy_inflated = (ratio.get("joy") or 0) > 1.5
    anger_collapsed = (ratio.get("anger") is not None
                       and ratio["anger"] < 0.5)
    disgust_collapsed = (ratio.get("disgust") is not None
                         and ratio["disgust"] < 0.5)

    msg = None
    if joy_inflated and (anger_collapsed or disgust_collapsed):
        collapsed = []
        if anger_collapsed:
            collapsed.append("anger")
        if disgust_collapsed:
            collapsed.append("disgust")
        msg = (f"congeniality skew: joy P/Q={ratio['joy']:.2f}(膨胀)+ "
               f"{','.join(collapsed)} 通道塌陷·亲和化偏置 KL={kl:.3f}")
    if msg:
        if mode == "active":
            out["violations"].append({"code": ISSUE_CODE, "kind": "congeniality_skew",
                                      "severity": "minor", "message": msg,
                                      "kl_divergence": round(kl, 4),
                                      "channel_ratio": out["channel_ratio"],
                                      "_doc": "advisory·作者档第一权威·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] affective_signature: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="implied author 情感 KL 漂移(advisory · shadow)")
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
