#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parrhesia_density_scanner.py — 冒险性直言密度

【缺口 · Foucault Fearless Speech / 网文打脸场景骨架】
parrhesia = 弱者对权者「冒险直言」。网文打脸名场面骨架 = (A) 权力反差词
+ (B) 主角对话引号 truth-claim 句式 + (C) 风险姿态身体描写。三者齐 hit
= 1 个 parrhesia 段（句段级）。密度 = parrhesia_hits / (cluster_cjk / 10000)。
作者档 parrhesia_signature.density_baseline.mean/sigma 第一权威；缺则用
全局兜底（mean=2.0 hits/10k · sigma=1.5）。

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 段拆 = `\n\n` 切；段内不再细切；三信号同段命中 = 1 hit
  · (A) 权力反差词：年轻人 / 废物 / 小子 vs 大人 / 家主 / 长老 / 陛下
        （前一类对应弱方称呼·后一类对应权者称谓 · 段内同存即 hit）
  · (B) truth-claim 句式：您说错了 / 恕我直言 / 请大人收回 / 此言差矣 /
        在下不敢苟同 …（出现在引号内段才算）
  · (C) 风险姿态：站起来 / 直视 / 不卑不亢 / 迎着 X 的目光 / 抬起头 /
        昂首 / 上前一步 / 拱手 / 直身

【三 advisory · 全 advisory shadow】
  · PARRHESIA_DENSITY_OVERFLOW    — > mean+2σ · 打脸服务疲劳
  · PARRHESIA_DENSITY_THIN        — < mean-2σ · 缺 payoff
  · PARRHESIA_DENSITY_OK          — 落 band · info

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  PARRHESIA_* 绝不进 audit_hub.HARD_GATE_CODES。

env PARRHESIA_DENSITY_MODE: off / shadow（默认） / active
用法: python parrhesia_density_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_OVERFLOW = "PARRHESIA_DENSITY_OVERFLOW"
ISSUE_CODE_THIN = "PARRHESIA_DENSITY_THIN"
ISSUE_CODE_OK = "PARRHESIA_DENSITY_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 占位 lexicon · _placeholder=true · 真版 = 打脸名场面聚类
# 外部词典 core/data/parrhesia_lexicon.json 优先·内嵌 _LEXICONS_FALLBACK 为缺失/损坏时兜底
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "parrhesia_lexicon.json"

_LEXICONS_FALLBACK = {
    "_placeholder": True,
    "_doc": "R25 W13 Batch-MM·parrhesia 三信号占位词典 (fallback · 外部 core/data/parrhesia_lexicon.json 缺失/损坏时启用)",
    "power_lower": [
        "年轻人", "废物", "小子", "毛头小子", "黄毛", "无名之辈",
        "不知天高地厚", "乳臭未干", "无知小儿", "愚徒", "蝼蚁",
    ],
    "power_upper": [
        "大人", "家主", "长老", "陛下", "尊主", "圣上", "宗主",
        "殿下", "阁下", "前辈", "上座", "总管",
    ],
    "truth_claim": [
        "您说错了", "恕我直言", "请大人收回", "此言差矣",
        "在下不敢苟同", "我以为不然", "并非如此", "您可知",
        "请收回", "您错了", "并不尽然", "未必如此",
    ],
    "risk_posture": [
        "站起来", "直视", "不卑不亢", "迎着", "抬起头",
        "昂首", "上前一步", "拱手", "直身", "正色道",
        "目光不躲", "迎上目光",
    ],
}


def _load_lexicons() -> dict:
    """外部 lexicon 优先·缺失/损坏 fallback 内嵌"""
    try:
        data = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
        # 必须含 4 个信号键，否则视为损坏走 fallback
        for k in ("power_lower", "power_upper", "truth_claim", "risk_posture"):
            if k not in data:
                return _LEXICONS_FALLBACK
        return data
    except (OSError, json.JSONDecodeError):
        return _LEXICONS_FALLBACK


_LEXICONS = _load_lexicons()

# 🔬 zero_shot_prototype 语义补召回（B 信号 truth_claim）
# 二分类判别原型：truth_claim 正类 + other 中性对照类（nearest-centroid 需负类才有判别力·
# 否则任何输入都归唯一质心）。措辞刻意区别于 lexicon 词表→抓"恕我直言"说成"把话挑明"的漏检。
# 内容后端不可用（测试默认）→ classify 返 None → 无补召回·纯 lexicon 判定。
_TRUTH_CLAIM_PROTOTYPES = {
    "truth_claim": ["我把话挑明了说吧", "恕我直言这里头有猫腻", "今天当着大家的面我把实话撂这儿",
                    "别怪我说得难听事实就是如此", "我今天就把这层窗户纸捅破"],
    "other": ["外面下起了小雨", "他走进房间坐下", "桌上摆着一杯凉茶", "天色渐渐暗了下来", "她翻开手里的书"],
}


def _truth_claim_semantic(para: str) -> bool:
    """zero_shot 判 para 是否语义上是 truth_claim（郑重直言/挑明真相）。
    内容后端不可用/异常/归 other/低置信 → False（调用方回退 lexicon）。"""
    try:
        import zero_shot_prototype
        res = zero_shot_prototype.classify(para, _TRUTH_CLAIM_PROTOTYPES, floor=0.5)
    except Exception:
        return False
    return bool(res) and res.get("label") == "truth_claim"


PARRHESIA_BASELINE_MEAN_DEFAULT = 2.0   # hits / 10k CJK · 占位
PARRHESIA_BASELINE_SIGMA_DEFAULT = 1.5  # σ · 占位


def _mode() -> str:
    m = (os.environ.get("PARRHESIA_DENSITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _split_paragraphs(text: str) -> list:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _load_baseline(project_root) -> tuple:
    """读 _数据库/作者风格.json · parrhesia_signature.density_baseline · 作者档第一权威。"""
    if not project_root:
        return (PARRHESIA_BASELINE_MEAN_DEFAULT, PARRHESIA_BASELINE_SIGMA_DEFAULT, False)
    try:
        p = Path(project_root) / "_数据库" / "作者风格.json"
        if not p.exists():
            return (PARRHESIA_BASELINE_MEAN_DEFAULT, PARRHESIA_BASELINE_SIGMA_DEFAULT, False)
        prof = json.loads(p.read_text(encoding="utf-8"))
        sig = (prof.get("parrhesia_signature") or {}).get("density_baseline") or {}
        mean = float(sig.get("mean", PARRHESIA_BASELINE_MEAN_DEFAULT))
        sigma = float(sig.get("sigma", PARRHESIA_BASELINE_SIGMA_DEFAULT))
        return (mean, max(0.1, sigma), True)
    except Exception:
        return (PARRHESIA_BASELINE_MEAN_DEFAULT, PARRHESIA_BASELINE_SIGMA_DEFAULT, False)


def _has_any(text: str, words: list) -> tuple:
    hits = [w for w in words if w in text]
    return (bool(hits), hits)


def _is_quoted_paragraph(para: str) -> bool:
    """检查段内是否含引号区段（U+201C ... U+201D 或「」）。"""
    return ("“" in para and "”" in para) or ("「" in para and "」" in para)


def _detect_parrhesia(para: str) -> dict:
    """三信号同段命中 = 1 hit。返回 {hit, A, B, C, hits_detail}。"""
    a_low, a_low_hits = _has_any(para, _LEXICONS["power_lower"])
    a_up, a_up_hits = _has_any(para, _LEXICONS["power_upper"])
    a_ok = a_low and a_up  # 权力反差 = 同段含弱+权
    # truth-claim 必须出现在引号段内（占位：仅检查段内含引号且 truth_claim 词命中）
    b_quoted = _is_quoted_paragraph(para)
    b_lex, b_hits = _has_any(para, _LEXICONS["truth_claim"])
    # zero_shot 补召回：lexicon 漏检时语义判 truth_claim（仍需引号段·并集非替换）
    b_semantic = (not b_lex) and _truth_claim_semantic(para)
    b_ok = (b_lex or b_semantic) and b_quoted
    c_ok, c_hits = _has_any(para, _LEXICONS["risk_posture"])
    hit = a_ok and b_ok and c_ok
    return {
        "hit": hit,
        "A_power_contrast": a_ok,
        "B_truth_claim_quoted": b_ok,
        "C_risk_posture": c_ok,
        "_A_hits": a_low_hits + a_up_hits,
        "_B_hits": b_hits if b_ok else [],
        "_B_semantic": b_semantic,
        "_C_hits": c_hits if c_ok else [],
    }


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "parrhesia_density_scanner", "schema_version": "1.0",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    paragraphs = _split_paragraphs(text)
    anchors = []
    for i, p in enumerate(paragraphs):
        rep = _detect_parrhesia(p)
        if rep["hit"]:
            anchors.append({"para_idx": i, "para_cjk": _cjk_count(p),
                            "A_hits": rep["_A_hits"], "B_hits": rep["_B_hits"],
                            "C_hits": rep["_C_hits"]})
    hits_per_10k = round(len(anchors) / max(1, cjk / 10000.0), 4)

    mean, sigma, author_owned = _load_baseline(project_root)
    upper = mean + 2 * sigma
    lower = max(0.0, mean - 2 * sigma)

    out.update({
        "cjk": cjk,
        "parrhesia_hits": len(anchors),
        "parrhesia_hits_per_10k_cjk": hits_per_10k,
        "anchors": anchors,
        "baseline": {"mean": mean, "sigma": sigma,
                     "upper": round(upper, 4), "lower": round(lower, 4),
                     "author_owned": author_owned},
    })

    code, severity, msg = None, "info", None
    if hits_per_10k > upper:
        code = ISSUE_CODE_OVERFLOW
        severity = "minor"
        msg = (f"parrhesia 密度 {hits_per_10k}/10k > {round(upper, 2)}"
               f"·打脸服务疲劳")
    elif hits_per_10k < lower and len(anchors) == 0:
        code = ISSUE_CODE_THIN
        severity = "minor"
        msg = (f"parrhesia 密度 {hits_per_10k}/10k < {round(lower, 2)}"
               f"·缺 payoff")
    else:
        code = ISSUE_CODE_OK
        severity = "info"
        msg = f"parrhesia 密度 {hits_per_10k}/10k 落 band [{round(lower,2)}, {round(upper,2)}]"

    if mode == "active":
        out["violations"].append({
            "kind": "parrhesia_density",
            "severity": severity,
            "code": code, "message": msg,
            "_doc": "R25 W13 Batch-MM·Foucault parrhesia·advisory·绝不 hard_gate",
        })
        out["verdict"] = "FAIL_MINOR" if severity == "minor" else "PASS"
        if severity == "minor":
            out["warning"] = msg
    elif mode == "shadow" and severity == "minor":
        print(f"[SHADOW] parrhesia_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="parrhesia 冒险直言密度 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
