#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""action_mentalizing_balance_scanner.py — Motor-Mentalizing 脑网络竞争比 advisory
R21 W10 Batch-DD · R21-NB-01

【缺口·R21 P1】Nijhof & Willems 2015 PLOS ONE fMRI: action vs mentalizing brain
networks 互斥(r=-0.48 aMPFC vs motor)。LLM 通病：纯动作流(打斗白描)或纯心智流
(连篇内心活动)·节奏失衡。

【做法 · 确定性 · 零 LLM/零联网】
  · ACTION_LEX 22 词(撞/扑/踢/挥/抓/劈/拽/踹/按/压/扛/搬/跑/蹲/跃/横/擒/甩/砸/斩/刺/戳)
    + 身体名词 N5(手/脚/拳/肩/胸/腰/脊/肋)
  · MENTAL_LEX 18 词(想/觉得/猜/信/怀疑/担心/意识到/察觉/揣摩/默念/权衡/犹豫/笃定/
    相信/不解/疑惑/料想/寻思) + 信念动词 N5(知道/记得/明白/晓得/认得)
  · 按场景切分（双空行/标记）·每场景算 ratio = mentalizing / (action + mentalizing)
  · 偏离作者档基线 ±0.20 → ACTION_MENTAL_RATIO_DRIFT
  · 纯动作(ratio<0.10 且 scene_cjk>800) → ACTION_PURE_PHYSICAL
  · 纯心智(ratio>0.90 且 scene_cjk>800) → ACTION_PURE_MENTAL
  · 通用兜底 0.35-0.65 健康带

【与既有 scanner 严格正交】
  · interiority_mode_balance(R8) 查 narrating mode (telling/showing)
  · duration_mix(R12) 查时长比例·summary/scene/pause
  · narrating_distance(R7) 查叙事距离
  本者 = 动作 vs 心智词频比·脑网络竞争代理。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  ACTION_* 绝不进 HARD_GATE_CODES。

env ACTION_MENTAL_BALANCE_MODE: off / shadow(默认) / active
用法: python action_mentalizing_balance_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DRIFT = "ACTION_MENTAL_RATIO_DRIFT"
ISSUE_CODE_PURE_PHYSICAL = "ACTION_PURE_PHYSICAL"
ISSUE_CODE_PURE_MENTAL = "ACTION_PURE_MENTAL"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 占位词典（_placeholder=true）·真词典需中文动作/心智标注语料 defer
ACTION_LEX = [
    "撞", "扑", "踢", "挥", "抓", "劈", "拽", "踹", "按", "压",
    "扛", "搬", "跑", "蹲", "跃", "横", "擒", "甩", "砸", "斩",
    "刺", "戳",
]
BODY_NOUNS = ["手", "脚", "拳", "肩", "胸", "腰", "脊", "肋"]

MENTAL_LEX = [
    "想", "觉得", "猜", "信", "怀疑", "担心", "意识到", "察觉",
    "揣摩", "默念", "权衡", "犹豫", "笃定", "相信", "不解",
    "疑惑", "料想", "寻思",
]
BELIEF_VERBS = ["知道", "记得", "明白", "晓得", "认得"]

DEFAULT_BASELINE = 0.50
DEFAULT_DRIFT_BAND = 0.20
DEFAULT_PURE_PHYSICAL = 0.10
DEFAULT_PURE_MENTAL = 0.90
DEFAULT_PURE_MIN_CJK = 800
DEFAULT_HEALTHY_LOW = 0.35
DEFAULT_HEALTHY_HIGH = 0.65

# 场景切分：连续 ≥1 空行
_SCENE_SPLITTER = re.compile(r"\n\s*\n", re.MULTILINE)


def _mode() -> str:
    m = (os.environ.get("ACTION_MENTAL_BALANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _count_hits(text: str, terms: list[str]) -> int:
    n = 0
    for t in terms:
        if t:
            n += text.count(t)
    return n


def _split_scenes(text: str) -> list[str]:
    """按双空行切分场景·至少 1 场"""
    scenes = [s.strip() for s in _SCENE_SPLITTER.split(text) if s.strip()]
    return scenes or [text]


def _scene_ratio(scene: str) -> dict:
    """单场景的 action / mentalizing 计数 + ratio"""
    a_lex = _count_hits(scene, ACTION_LEX)
    a_body = _count_hits(scene, BODY_NOUNS)
    m_lex = _count_hits(scene, MENTAL_LEX)
    m_bel = _count_hits(scene, BELIEF_VERBS)
    action = a_lex + a_body
    mental = m_lex + m_bel
    denom = action + mental
    ratio = (mental / denom) if denom else 0.5
    return {
        "scene_cjk": _cjk_count(scene),
        "action_hits": action,
        "mentalizing_hits": mental,
        "ratio": round(ratio, 4),
    }


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            sig = obj.get("action_mental_baseline")
            if isinstance(sig, dict):
                return sig
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "action_mentalizing_balance", "schema_version": "1.0",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    baseline_data = _read_author_baseline(project_root)
    target_ratio = DEFAULT_BASELINE
    drift_band = DEFAULT_DRIFT_BAND
    pure_phys = DEFAULT_PURE_PHYSICAL
    pure_ment = DEFAULT_PURE_MENTAL
    pure_min_cjk = DEFAULT_PURE_MIN_CJK
    healthy_low = DEFAULT_HEALTHY_LOW
    healthy_high = DEFAULT_HEALTHY_HIGH
    baseline_source = "fallback"
    if isinstance(baseline_data, dict):
        baseline_source = "author_profile"
        if isinstance(baseline_data.get("target_ratio"), (int, float)):
            target_ratio = float(baseline_data["target_ratio"])
        if isinstance(baseline_data.get("drift_band"), (int, float)):
            drift_band = float(baseline_data["drift_band"])
        if isinstance(baseline_data.get("pure_physical_max"), (int, float)):
            pure_phys = float(baseline_data["pure_physical_max"])
        if isinstance(baseline_data.get("pure_mental_min"), (int, float)):
            pure_ment = float(baseline_data["pure_mental_min"])

    scenes = _split_scenes(text)
    scene_reports = [_scene_ratio(s) for s in scenes]
    # 总比
    total_a = sum(r["action_hits"] for r in scene_reports)
    total_m = sum(r["mentalizing_hits"] for r in scene_reports)
    overall = (total_m / (total_a + total_m)) if (total_a + total_m) else 0.5

    out.update({
        "cjk": cjk,
        "scene_count": len(scenes),
        "scene_reports": scene_reports,
        "overall_ratio": round(overall, 4),
        "total_action_hits": total_a,
        "total_mentalizing_hits": total_m,
        "baseline_source": baseline_source,
        "thresholds": {
            "target_ratio": target_ratio,
            "drift_band": drift_band,
            "pure_physical_max": pure_phys,
            "pure_mental_min": pure_ment,
            "pure_min_cjk": pure_min_cjk,
            "healthy_low": healthy_low,
            "healthy_high": healthy_high,
        },
    })

    flags = []
    # 整体漂移
    drift = abs(overall - target_ratio)
    if drift > drift_band:
        flags.append({"code": ISSUE_CODE_DRIFT,
                      "msg": (f"overall_ratio={overall:.3f} 偏离 baseline={target_ratio:.3f}"
                              f" 超 ±{drift_band:.2f}·脑网络竞争代理漂移")})
    # 纯动作 / 纯心智
    for i, r in enumerate(scene_reports):
        if r["scene_cjk"] >= pure_min_cjk:
            if r["ratio"] < pure_phys and (r["action_hits"] + r["mentalizing_hits"]) >= 4:
                flags.append({"code": ISSUE_CODE_PURE_PHYSICAL,
                              "msg": (f"scene#{i} ratio={r['ratio']}<{pure_phys} 且 cjk={r['scene_cjk']}"
                                      f"·纯动作流·缺心智活动")})
            elif r["ratio"] > pure_ment and (r["action_hits"] + r["mentalizing_hits"]) >= 4:
                flags.append({"code": ISSUE_CODE_PURE_MENTAL,
                              "msg": (f"scene#{i} ratio={r['ratio']}>{pure_ment} 且 cjk={r['scene_cjk']}"
                                      f"·纯心智流·缺动作落地")})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "action_mentalizing_balance", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Nijhof&Willems 2015 PLOS ONE r=-0.48·R21-NB-01·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] action_mentalizing_balance: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Motor-Mentalizing 脑网络竞争比 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
