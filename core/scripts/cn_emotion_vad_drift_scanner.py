#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cn_emotion_vad_drift_scanner.py — 跨语言情感坐标漂移 · R24 W12 Batch-KK · P1

【缺口 · 跨语言情感坐标漂移 (Anglo-VAD vs CN-VAD)】
英语 NRC-VAD 与汉语 CVAW(v2) 在同一情感词上 valence/arousal 坐标不一致：
弱模型用英文 VAD 词典惯性把汉语词「悲愤」「窘迫」打到 Anglo 调性 → 失去
中式细颗粒情感，二分极化（要么 clear 大喜大悲、要么 ambivalent 模糊）。
本扫描三 signal 跨语言情感漂移：
  (A) Δv/Δa > 0.15 → ANGLO_DRIFT（CVAW vs NRC-VAD CN 坐标差异显著）
  (B) 文化特有情感词覆盖率 < 0.6× 作者基线 → UNDERUSE（缺中式细情感）
  (C) clear/ambivalent 比例 > 1.8× 基线 → BINARY_POLARIZATION（二分化）

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 维护双词典：_CVAW_V2 / _NRC_VAD_CN（占位坐标·真版蒸馏阶段灌大字典）
  · 维护 _CULTURAL_SPECIFIC（中式细情感·无英文 1:1 对应：含蓄/悻悻/惆怅...）
  · 维护 _CLEAR / _AMBIVALENT 词集（清晰极性 vs 模糊混合）
  · 三 signal 全 advisory，作者基线读 作者风格.json.cn_emotion_baseline
  · 缺基线时用默认（density=2.0/千CJK / clear_ambivalent_ratio=2.0）

【五 advisory · 全 advisory shadow】
  · CN_EMOTION_ANGLO_DRIFT       — Δv/Δa > 0.15
  · CN_EMOTION_CULTURAL_UNDERUSE  — 文化特有词覆盖率 < 0.6× 基线
  · CN_EMOTION_BINARY_POLARIZATION — clear/ambivalent > 1.8× 基线
  · CN_EMOTION_NO_EMOTION_WORDS    — 草稿无情感词命中（info）
  · CN_EMOTION_OK                 — 三 signal 全过（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  CN_EMOTION_* 绝不进 audit_hub.HARD_GATE_CODES。

env CN_EMOTION_VAD_MODE: off / shadow（默认） / active
用法: python cn_emotion_vad_drift_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_ANGLO_DRIFT = "CN_EMOTION_ANGLO_DRIFT"
ISSUE_CODE_UNDERUSE = "CN_EMOTION_CULTURAL_UNDERUSE"
ISSUE_CODE_POLARIZATION = "CN_EMOTION_BINARY_POLARIZATION"
ISSUE_CODE_NO_EMOTION = "CN_EMOTION_NO_EMOTION_WORDS"
ISSUE_CODE_OK = "CN_EMOTION_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DEFAULT_BASELINE_CULTURAL_DENSITY = 2.0   # 文化特有词 / 千 CJK
DEFAULT_BASELINE_CLEAR_AMBIVALENT_RATIO = 2.0
DRIFT_DELTA_THRESHOLD = 0.15
UNDERUSE_RATIO_THRESHOLD = 0.6
POLARIZATION_RATIO_THRESHOLD = 1.8

# CVAW v2 占位坐标（valence, arousal）· 真版蒸馏阶段灌完整 5K 词
# 坐标 1-9 区间归一到 [-1, 1] · 占位仅 12 词供 contract 测试
_CVAW_V2 = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-KK·CVAW v2 中文情感词典占位·真版用 5K 蒸馏灌",
    # word: (v, a) 都归一 [-1,1] · 占位 12 词
    "_words": {
        "悲愤": (-0.55, 0.65),
        "窘迫": (-0.35, 0.40),
        "惆怅": (-0.45, 0.10),
        "含蓄": (0.20, -0.20),
        "悻悻": (-0.30, 0.25),
        "释怀": (0.55, -0.10),
        "怔忡": (-0.20, 0.45),
        "怅惘": (-0.40, 0.05),
        "腼腆": (0.30, -0.25),
        "顿挫": (-0.15, 0.30),
        "忐忑": (-0.30, 0.55),
        "雀跃": (0.65, 0.50),
    },
}

# NRC-VAD CN 占位坐标（英语 VAD 平移到中文翻译词）
_NRC_VAD_CN = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-KK·NRC-VAD CN 平移占位·真版用 NRC-VAD 1.0 中文映射",
    "_words": {
        "悲愤": (-0.75, 0.80),   # NRC 更 polar
        "窘迫": (-0.55, 0.30),
        "惆怅": (-0.65, 0.05),
        "含蓄": (0.05, -0.10),
        "悻悻": (-0.50, 0.40),
        "释怀": (0.70, 0.05),
        "怔忡": (-0.05, 0.30),
        "怅惘": (-0.55, 0.10),
        "腼腆": (0.45, -0.10),
        "顿挫": (-0.05, 0.20),
        "忐忑": (-0.45, 0.70),
        "雀跃": (0.80, 0.65),
    },
}

# 文化特有中式细颗粒情感词（无英文 1:1 对应）· _placeholder
_CULTURAL_SPECIFIC = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-KK·中式细颗粒情感（无英语 1:1 对应）占位",
    "_words": [
        "含蓄", "悻悻", "惆怅", "怅惘", "怔忡", "腼腆", "忐忑", "顿挫",
        "扼腕", "愀然", "悒郁", "蹙眉", "颔首", "汗颜", "忸怩", "讪讪",
        "踯躅", "彳亍", "怨怼", "悒悒", "悻然", "悻悻然", "怏怏",
    ],
}

# 清晰极性词（laugh/cry/joy/rage）
_CLEAR_WORDS = {
    "_placeholder": True,
    "_words": [
        "大笑", "狂笑", "痛哭", "嚎啕", "暴怒", "震怒", "狂喜", "狂怒",
        "怒吼", "哀嚎", "嚎叫", "尖叫", "癫狂", "失声痛哭",
    ],
}

# 模糊混合词（mixed-feeling）
_AMBIVALENT_WORDS = {
    "_placeholder": True,
    "_words": [
        "含蓄", "悻悻", "惆怅", "怅惘", "忐忑", "顿挫", "踟蹰", "踌躇",
        "踯躅", "犹豫", "纠结", "复杂", "百感交集", "五味杂陈",
        "啼笑皆非", "哭笑不得",
    ],
}


def _mode() -> str:
    m = (os.environ.get("CN_EMOTION_VAD_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _signal_a_drift(text: str) -> dict:
    """signal A：CVAW v2 vs NRC-VAD CN 平均 Δv/Δa。"""
    cvaw = _CVAW_V2["_words"]
    nrc = _NRC_VAD_CN["_words"]
    hits = []
    for w, (v_c, a_c) in cvaw.items():
        n = text.count(w)
        if n > 0 and w in nrc:
            v_n, a_n = nrc[w]
            hits.append({
                "word": w, "count": n,
                "dv": v_c - v_n, "da": a_c - a_n,
            })
    if not hits:
        return {"avg_dv": 0.0, "avg_da": 0.0, "drift_words": []}
    total = sum(h["count"] for h in hits)
    avg_dv = sum(h["dv"] * h["count"] for h in hits) / total
    avg_da = sum(h["da"] * h["count"] for h in hits) / total
    return {
        "avg_dv": round(avg_dv, 4),
        "avg_da": round(avg_da, 4),
        "drift_words": hits[:10],
    }


def _signal_b_cultural_underuse(text: str, baseline_density: float) -> dict:
    """signal B：文化特有词密度 vs 作者基线。"""
    cjk = _cjk_count(text)
    if cjk == 0:
        return {"density": 0.0, "baseline": baseline_density, "ratio": 0.0, "hits": []}
    words = _CULTURAL_SPECIFIC["_words"]
    hits = []
    total = 0
    for w in words:
        n = text.count(w)
        if n > 0:
            hits.append({"word": w, "count": n})
            total += n
    density = total / (cjk / 1000.0)
    ratio = density / baseline_density if baseline_density > 0 else 1.0
    return {
        "density": round(density, 3),
        "baseline": baseline_density,
        "ratio": round(ratio, 3),
        "hits": hits[:10],
    }


def _signal_c_polarization(text: str, baseline_ratio: float) -> dict:
    """signal C：clear/ambivalent 比例。"""
    clear_n = sum(text.count(w) for w in _CLEAR_WORDS["_words"])
    amb_n = sum(text.count(w) for w in _AMBIVALENT_WORDS["_words"])
    if amb_n == 0:
        # 无 ambivalent 视作满分极化（但需 clear 有命中）
        ratio = float(clear_n) if clear_n > 0 else 0.0
    else:
        ratio = clear_n / amb_n
    rel = ratio / baseline_ratio if baseline_ratio > 0 else 1.0
    return {
        "clear_count": clear_n,
        "ambivalent_count": amb_n,
        "ratio": round(ratio, 3),
        "baseline_ratio": baseline_ratio,
        "rel_to_baseline": round(rel, 3),
    }


def _load_baseline(project_root) -> dict:
    """读 作者风格.json.cn_emotion_baseline 或 _FINAL。"""
    out = {
        "cultural_density": DEFAULT_BASELINE_CULTURAL_DENSITY,
        "clear_ambivalent_ratio": DEFAULT_BASELINE_CLEAR_AMBIVALENT_RATIO,
        "_source": "default",
    }
    if not project_root:
        return out
    cands = [
        Path(project_root) / "_数据库" / "作者风格.json",
        Path(project_root) / "_数据库" / "作者风格_FINAL.json",
    ]
    for c in cands:
        if not c.exists():
            continue
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            base = data.get("cn_emotion_baseline") or {}
            cd = base.get("cultural_density")
            cr = base.get("clear_ambivalent_ratio")
            if isinstance(cd, (int, float)) and cd > 0:
                out["cultural_density"] = float(cd)
                out["_source"] = c.name
            if isinstance(cr, (int, float)) and cr > 0:
                out["clear_ambivalent_ratio"] = float(cr)
                out["_source"] = c.name
            return out
        except (OSError, json.JSONDecodeError):
            continue
    return out


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "cn_emotion_vad_drift_scanner",
        "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True,
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
    if cjk < 800:
        out["note"] = "草稿太短·跳过"
        return out

    baseline = _load_baseline(project_root)

    sig_a = _signal_a_drift(text)
    sig_b = _signal_b_cultural_underuse(text, baseline["cultural_density"])
    sig_c = _signal_c_polarization(text, baseline["clear_ambivalent_ratio"])

    out.update({
        "cjk": cjk,
        "baseline": baseline,
        "signal_a_anglo_drift": sig_a,
        "signal_b_cultural_underuse": sig_b,
        "signal_c_polarization": sig_c,
    })

    flags = []
    # 无任何情感词命中
    no_emotion = (not sig_a["drift_words"]
                  and not sig_b["hits"]
                  and sig_c["clear_count"] == 0
                  and sig_c["ambivalent_count"] == 0)
    if no_emotion:
        flags.append({
            "code": ISSUE_CODE_NO_EMOTION,
            "msg": "草稿无 CVAW/cultural/clear/ambivalent 情感词命中",
            "severity": "info",
        })
    else:
        # signal A
        if (abs(sig_a["avg_dv"]) > DRIFT_DELTA_THRESHOLD
                or abs(sig_a["avg_da"]) > DRIFT_DELTA_THRESHOLD):
            flags.append({
                "code": ISSUE_CODE_ANGLO_DRIFT,
                "msg": (f"CVAW v2 vs NRC-VAD CN 平均 Δv={sig_a['avg_dv']:+.2f}"
                        f" Δa={sig_a['avg_da']:+.2f}（阈 {DRIFT_DELTA_THRESHOLD}）"
                        f"·跨语言情感坐标漂移"),
                "severity": "minor",
            })
        # signal B
        if sig_b["ratio"] < UNDERUSE_RATIO_THRESHOLD:
            flags.append({
                "code": ISSUE_CODE_UNDERUSE,
                "msg": (f"文化特有情感词密度 {sig_b['density']:.2f}/千 ="
                        f" {sig_b['ratio']*100:.0f}% 作者基线"
                        f"（<{int(UNDERUSE_RATIO_THRESHOLD*100)}% 阈）"
                        f"·中式细情感被英文化平推"),
                "severity": "minor",
            })
        # signal C
        if sig_c["rel_to_baseline"] > POLARIZATION_RATIO_THRESHOLD:
            flags.append({
                "code": ISSUE_CODE_POLARIZATION,
                "msg": (f"clear/ambivalent={sig_c['ratio']:.2f} ="
                        f" {sig_c['rel_to_baseline']*100:.0f}% 作者基线"
                        f"（>{int(POLARIZATION_RATIO_THRESHOLD*100)}% 阈）"
                        f"·二分极化·缺中式混合情感"),
                "severity": "minor",
            })
        if not flags:
            flags.append({
                "code": ISSUE_CODE_OK,
                "msg": "三 signal 全在带内",
                "severity": "info",
            })

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "cn_emotion_vad_drift",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": ("R24 W12 Batch-KK·CVAW v2 vs NRC-VAD CN 跨语言情感漂移"
                         "·advisory·绝不 hard_gate")})
        any_minor = any(v["severity"] == "minor" for v in out["violations"])
        out["verdict"] = "FAIL_MINOR" if any_minor else "PASS"
        out["warning"] = "·".join(f["msg"] for f in flags if f["severity"] == "minor") or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] cn_emotion_vad_drift: "
                  + "·".join(f["msg"] for f in minor)
                  + " — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="跨语言情感坐标漂移 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
