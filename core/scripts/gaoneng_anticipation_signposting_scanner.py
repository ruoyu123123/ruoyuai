#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gaoneng_anticipation_signposting_scanner.py — 爆点前 2-3 段 signpost 密度 · R24 W12 Batch-JJ · P1

【缺口 · 高能 anticipation signposting / 直播弹幕预测】
高能/爆点研究：观众弹幕在爆点前 2-3 段就开始密度升高 → 5 类 signpost：
  · sensory_localization — 感官局部化（耳/眼/手/脸/...聚焦）
  · sudden_quiet        — 突然 quiet（声音消失/万籁/呼吸/...）
  · physiological       — 生理预兆（汗/凉/麻/酥/心跳/...）
  · object_closeup      — 客体特写（特写/那把/那扇/...）
  · time_distortion     — 时间扭曲（一秒/瞬间/仿佛/恍惚/...）

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 启发选 top-K 爆点段（标点 + 短句独行 score 复用 frisson_lead_window 启发）
  · 向前回溯 2-3 段窗口（约 200-400 CJK）
  · 算 signpost_density = 5 类命中总数 / (cjk/1000)
  · 与作者档 baseline z-band 比对（作者档无 baseline 时用默认 1.0/千 CJK）
  · < -1σ → BURST_LEAD_SIGNPOST_LOW advisory
  · writer prompt 软提示 lead window 5 类 signpost

【三 advisory】
  · BURST_LEAD_SIGNPOST_LOW    — signpost_density < -1σ（无预兆铺垫）
  · BURST_LEAD_SIGNPOST_HIGH   — signpost_density > +2σ（过度铺垫·钝化爆点）
  · BURST_LEAD_NO_PEAK_FOUND   — 找不到爆点段（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  BURST_LEAD_SIGNPOST_* 绝不进 audit_hub.HARD_GATE_CODES。

env SIGNPOST_MODE: off / shadow（默认） / active
用法: python gaoneng_anticipation_signposting_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_LOW = "BURST_LEAD_SIGNPOST_LOW"
ISSUE_CODE_HIGH = "BURST_LEAD_SIGNPOST_HIGH"
ISSUE_CODE_NO_PEAK = "BURST_LEAD_NO_PEAK_FOUND"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

TOP_K = 3
LEAD_WINDOW_PARAS = 3  # 向前最多回溯 3 段
LEAD_WINDOW_CJK_MIN = 200
LEAD_WINDOW_CJK_MAX = 400
DEFAULT_BASELINE_DENSITY = 1.0  # 默认 1.0 / 千 CJK
DEFAULT_BASELINE_STD = 0.6

_SIGNPOST_LEXICONS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-JJ·5 类 signpost 占位词典",
    "sensory_localization": [
        "耳畔", "耳边", "眼角", "鼻尖", "指尖", "手心", "舌尖", "唇齿",
        "脸颊", "后颈", "脊背",
    ],
    "sudden_quiet": [
        "万籁", "鸦雀", "无声", "寂静", "屏息", "呼吸停了", "停下了",
        "戛然", "戛然而止", "一瞬寂",
    ],
    "physiological": [
        "汗", "凉", "麻", "酥", "心跳", "心头", "颤抖", "起鸡皮疙瘩",
        "毛骨悚然", "倒吸", "屏气",
    ],
    "object_closeup": [
        "那把", "那扇", "那道", "那只", "特写", "聚焦", "目光落在",
        "视线锁在", "镜头",
    ],
    "time_distortion": [
        "一瞬", "一秒", "瞬间", "刹那", "霎时", "恍惚", "仿佛过了",
        "时间凝固", "时间停滞",
    ],
}

_EMOTION_PUNCT_RE = re.compile(r"[！？]+|……+|—{2,}")


def _mode() -> str:
    m = (os.environ.get("SIGNPOST_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p and p.strip()]


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


def _peak_score(paragraph: str) -> float:
    cjk = _cjk_count(paragraph)
    if cjk < 12:
        return 0.0
    return _punct_density(paragraph) * 0.7 + _short_solo_ratio(paragraph) * 0.3 * 30


def _find_top_k_peaks(paragraphs: list[str], k: int = TOP_K) -> list[int]:
    scored = [(i, _peak_score(p)) for i, p in enumerate(paragraphs)]
    scored.sort(key=lambda x: x[1], reverse=True)
    out = []
    for i, s in scored[:k]:
        if s > 0.0:
            out.append(i)
    return out


def _build_lead_window(paragraphs: list[str], peak_idx: int) -> str:
    if peak_idx <= 0:
        return ""
    indices = []
    accum = ""
    i = peak_idx - 1
    while i >= 0 and _cjk_count(accum) < LEAD_WINDOW_CJK_MIN and len(indices) < LEAD_WINDOW_PARAS:
        accum = paragraphs[i] + "\n" + accum
        indices.insert(0, i)
        i -= 1
    return accum.strip()


def _count_signposts(window_text: str) -> dict:
    out = {}
    total = 0
    for typ, words in _SIGNPOST_LEXICONS.items():
        if typ.startswith("_"):
            continue
        n = sum(window_text.count(w) for w in words)
        out[typ] = n
        total += n
    out["_total"] = total
    return out


def _load_baseline(project_root) -> tuple[float, float]:
    if not project_root:
        return DEFAULT_BASELINE_DENSITY, DEFAULT_BASELINE_STD
    cands = [
        Path(project_root) / "_数据库" / "作者风格.json",
        Path(project_root) / "_数据库" / "作者风格_FINAL.json",
    ]
    for c in cands:
        if not c.exists():
            continue
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            sp = data.get("signpost_density_baseline") or {}
            mean = sp.get("mean")
            std = sp.get("std")
            if isinstance(mean, (int, float)) and isinstance(std, (int, float)) and std > 0:
                return float(mean), float(std)
        except (OSError, json.JSONDecodeError):
            continue
    return DEFAULT_BASELINE_DENSITY, DEFAULT_BASELINE_STD


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "gaoneng_anticipation_signposting_scanner",
        "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _SIGNPOST_LEXICONS.get("_placeholder", True),
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

    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 5:
        out["note"] = "段落太少·跳过"
        return out

    peak_indices = _find_top_k_peaks(paragraphs, TOP_K)
    if not peak_indices:
        if mode == "active":
            out["violations"].append({
                "kind": "gaoneng_signposting", "severity": "info",
                "code": ISSUE_CODE_NO_PEAK,
                "message": "找不到爆点段·签 cluster 无 peak",
                "_doc": "R24 W12 Batch-JJ·advisory·绝不 hard_gate"})
            out["warning"] = "无 peak"
        out["note"] = "无爆点段"
        out["violations_count"] = len(out["violations"])
        return out

    baseline_mean, baseline_std = _load_baseline(project_root)

    peak_records = []
    flags = []
    for peak_idx in peak_indices:
        lead = _build_lead_window(paragraphs, peak_idx)
        if not lead:
            continue
        lead_cjk = _cjk_count(lead)
        if lead_cjk < 50:
            continue
        counts = _count_signposts(lead)
        density = counts["_total"] / max(lead_cjk / 1000.0, 0.001)
        z = (density - baseline_mean) / baseline_std if baseline_std > 0 else 0
        rec = {
            "peak_idx": peak_idx,
            "lead_cjk": lead_cjk,
            "signpost_counts": {k: v for k, v in counts.items() if not k.startswith("_")},
            "signpost_total": counts["_total"],
            "density_per_1k": round(density, 3),
            "z_score": round(z, 3),
        }
        peak_records.append(rec)
        if z < -1.0:
            flags.append({
                "code": ISSUE_CODE_LOW,
                "msg": (f"peak#{peak_idx} signpost 密度 {density:.2f}/千 z={z:.2f}"
                        f" < -1σ·爆点前缺铺垫"),
                "severity": "minor",
            })
        elif z > 2.0:
            flags.append({
                "code": ISSUE_CODE_HIGH,
                "msg": (f"peak#{peak_idx} signpost 密度 {density:.2f}/千 z={z:.2f}"
                        f" > +2σ·过度铺垫钝化爆点"),
                "severity": "info",
            })

    out.update({
        "cjk": cjk,
        "paragraphs": len(paragraphs),
        "peaks": peak_records,
        "baseline_mean": baseline_mean,
        "baseline_std": baseline_std,
    })

    if not flags:
        out["note"] = "全 peak signposts 在带内"

    if flags:
        msg = "·".join(f["msg"] for f in flags[:3])
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "gaoneng_signposting",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "R24 W12 Batch-JJ·anticipation signposting·advisory·绝不 hard_gate"})
            any_minor = any(v["severity"] == "minor" for v in out["violations"])
            out["verdict"] = "FAIL_MINOR" if any_minor else "PASS"
            out["warning"] = msg if any_minor else None
        else:
            print(f"[SHADOW] gaoneng_signposting: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="爆点 signposting advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
