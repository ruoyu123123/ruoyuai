#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imageability_scanner.py — 句级具象度 z-band advisory · cluster · 2026-06-21 R20 W9 Batch-CC · P2

【缺口 · R20 Q3-Q4 id 20】Paivio 1968 dual-coding theory + Coltheart MRC
psycholinguistic database：高具象度词(桌椅刀杯)读者立刻可视化，低具象度词
(意义本质理念)需要思考。优秀网文/严肃文学具象度高(读者眼前有画面)；
LLM 默认偏抽象大词堆叠=塑料感/作文感。此前 narrative_short_sentence /
semantic_slop / repeat_noun_density 都不查这维。

【做法 · 确定性 · 占位词典】
  · core/data/imageability_zh.json 含 60 高具象 + 60 低具象 placeholder
  · 真版需 Paivio MRC 1500+ 中文 norms 授权或 distill 抽取·defer
  · 草稿匹配 high_count + low_count
  · imageability_index = (high - low) / (high + low) ∈ [-1, 1]
    - 1.0 全高具象
    - 0.0 平衡
    - -1.0 全低具象(抽象大词)
  · 作者档 imageability_baseline = {mean, std} → z-band
  · |z|>1.0 触发 IMAGEABILITY_OFF_BAND advisory

【与既有 scanner 严格正交】
  · repeat_noun_density 查重复名词·不查具象度
  · semantic_slop 查 AI 套话·不查抽象词比
  · scene_grounding 查感官接地·不查词汇具象度本身
  · 本 scanner = 具象度词典 z-band 唯一覆盖

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  IMAGEABILITY_OFF_BAND 绝不进 audit_hub.HARD_GATE_CODES。

env IMAGEABILITY_MODE: off / shadow(默认) / active
用法: python imageability_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "IMAGEABILITY_OFF_BAND"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "imageability_zh.json"

DEFAULT_BASELINE_MEAN = 0.0
DEFAULT_BASELINE_STD = 0.3
DRIFT_Z_THRESHOLD = 1.0
MIN_CJK = 500
MIN_HITS = 5


def _mode() -> str:
    m = (os.environ.get("IMAGEABILITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_lexicon() -> dict:
    try:
        return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "_placeholder": True,
            "high_imageability": ["桌子", "椅子", "茶杯", "门", "窗"],
            "low_imageability": ["意义", "本质", "真理", "理念", "概念"],
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
            mb = obj.get("imageability_baseline")
            if isinstance(mb, dict):
                return mb
    return None


def _count_terms(text: str, terms: list[str]) -> int:
    n = 0
    for t in terms:
        if not t:
            continue
        n += text.count(t)
    return n


def _z(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return round((value - mean) / std, 3)


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "imageability", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE,
           "gate_level": "advisory",
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
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    lex = _load_lexicon()
    high_terms = lex.get("high_imageability") or []
    low_terms = lex.get("low_imageability") or []
    high_count = _count_terms(text, high_terms)
    low_count = _count_terms(text, low_terms)
    total = high_count + low_count
    imageability_index = round((high_count - low_count) / total, 4) if total else 0.0

    out.update({
        "cjk": cjk,
        "high_count": high_count,
        "low_count": low_count,
        "imageability_index": imageability_index,
        "_placeholder": True,
        "_doc_placeholder": "60+60 placeholder · 真版 Paivio MRC 中文 norms defer",
    })

    if total < MIN_HITS:
        out["note"] = f"词典命中 {total} < {MIN_HITS}·样本不足跳过"
        out["violations_count"] = 0
        return out

    baseline = _read_author_baseline(project_root)
    use_mean = DEFAULT_BASELINE_MEAN
    use_std = DEFAULT_BASELINE_STD
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        m = baseline.get("mean")
        s = baseline.get("std")
        if isinstance(m, (int, float)):
            use_mean = float(m)
        if isinstance(s, (int, float)) and s > 0:
            use_std = float(s)

    z = _z(imageability_index, use_mean, use_std)
    out.update({
        "z_score": z,
        "baseline_source": baseline_source,
        "baseline": {"mean": use_mean, "std": use_std},
    })

    flags = []
    if abs(z) > DRIFT_Z_THRESHOLD:
        direction = "偏抽象" if z < 0 else "偏具象"
        flags.append({
            "code": ISSUE_CODE,
            "msg": (f"imageability_index={imageability_index} z={z}{direction} "
                    f"(vs author μ={use_mean}±σ={use_std})·"
                    f"高具象 {high_count} vs 低具象 {low_count}")
        })

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "imageability_off_band", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Paivio dual-coding imageability proxy · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] imageability: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="句级具象度 z-band advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
