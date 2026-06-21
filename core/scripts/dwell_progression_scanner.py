#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dwell_progression_scanner.py — iyashikei_healing dwell/progression 段级分类 · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 asmr_slow id 9】iyashikei (癒し系) 治愈系核心节奏：dwell（停留品味）
+ ambient_setup（环境铺垫）远多于 plot_beat（剧情推进）。LLM 默认每段都推进 →
没有「呼吸」。本 scanner 是 iyashikei_healing genre pack 的专属 scanner。

【三档段类型 · 确定性占位 · 真分类器 defer】
  · dwell        — 停留品味（动作内向 / 感官描写 / 心理沉浸）
  · ambient_setup — 环境铺垫（季节天气/光声温/物件描摹）
  · plot_beat    — 剧情推进（对话信息/事件转折/角色决策）

判定启发占位：
  · 句末感叹/问号 → 倾向 plot_beat
  · 含季节天气词 ≥ 2 → ambient_setup
  · 含五感+第一人称代词 → dwell
  · 含对话引号 → plot_beat

【两 advisory】（仅 iyashikei genre 激活）
  · DWELL_RATIO_LOW — dwell_ratio < 0.30
  · PROGRESSION_TOO_DENSE — progression_density (plot_beat 段/总段) > 0.50

【北极星】②④⑤ advisory shadow · 绝不 hard_gate
  · genre_tags ∋ iyashikei_healing / iyashikei → active
  · 其他题材 → shadow（仅算指标不报）

env DWELL_PROGRESSION_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_DWELL_LOW = "DWELL_RATIO_LOW"
ISSUE_CODE_PROG_DENSE = "PROGRESSION_TOO_DENSE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 占位词袋
_SEASON_WEATHER = ("春", "夏", "秋", "冬", "晨", "夜", "黄昏", "雨", "雪",
                   "风", "云", "霜", "雾", "霞", "晴", "阴", "月光", "日光",
                   "蝉", "蛙", "雀", "落叶", "残月")
_SENSE_WORDS = ("看", "听", "闻", "尝", "触", "感", "嗅", "凝视", "倾听")
_FIRST_PERSON = ("我", "她", "他", "自己")
_QUOTE_CHARS = "“”\"「」"

# 治愈系 genre
_IYASHIKEI_TAGS = {"iyashikei_healing", "iyashikei", "治愈", "癒し", "慢生活"}

DEFAULT_DWELL_RATIO_MIN = 0.30
DEFAULT_PROGRESSION_DENSITY_MAX = 0.50


def _mode() -> str:
    m = (os.environ.get("DWELL_PROGRESSION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _classify_paragraph(p: str) -> str:
    """占位启发分类。"""
    has_quote = any(q in p for q in _QUOTE_CHARS)
    sw_hits = sum(1 for w in _SEASON_WEATHER if w in p)
    sense_hits = sum(1 for w in _SENSE_WORDS if w in p)
    fp_hits = sum(1 for w in _FIRST_PERSON if w in p)
    excl = p.count("！") + p.count("？")

    if has_quote or excl >= 2:
        return "plot_beat"
    if sw_hits >= 2:
        return "ambient_setup"
    if sense_hits >= 1 and fp_hits >= 1:
        return "dwell"
    if sw_hits >= 1:
        return "ambient_setup"
    # 兜底：plot_beat
    return "plot_beat"


def _read_genre_tags(project_root) -> set[str]:
    if not project_root:
        return set()
    db = Path(project_root) / "_数据库"
    tags: set[str] = set()
    for fname in ("用户偏好.json", "作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        raw = obj.get("genre_tags") or obj.get("genre") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, str):
                    tags.add(t.strip())
                    tags.add(t.strip().lower())
    return tags


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = Path(project_root) / "_数据库" / fname
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("dwell_progression_baseline"), dict):
                return obj["dwell_progression_baseline"]
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "dwell_progression", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_classifier_placeholder": True}
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

    paras = _split_paragraphs(text)
    paras = [p for p in paras if _cjk_count(p) >= 8]
    if len(paras) < 6:
        out["note"] = "段数过少·跳过"
        return out

    counts = {"dwell": 0, "ambient_setup": 0, "plot_beat": 0}
    for p in paras:
        c = _classify_paragraph(p)
        counts[c] = counts.get(c, 0) + 1
    total = len(paras)
    dwell_ratio = round(counts["dwell"] / total, 3)
    ambient_ratio = round(counts["ambient_setup"] / total, 3)
    progression_density = round(counts["plot_beat"] / total, 3)

    genre_tags = _read_genre_tags(project_root)
    is_iyashikei = bool(genre_tags & _IYASHIKEI_TAGS) or any(
        t.lower() in {x.lower() for x in _IYASHIKEI_TAGS} for t in genre_tags)
    baseline = _read_author_baseline(project_root)
    dwell_min = DEFAULT_DWELL_RATIO_MIN
    prog_max = DEFAULT_PROGRESSION_DENSITY_MAX
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("dwell_ratio_min"), (int, float)):
            dwell_min = float(baseline["dwell_ratio_min"])
        if isinstance(baseline.get("progression_density_max"), (int, float)):
            prog_max = float(baseline["progression_density_max"])

    out.update({
        "cjk": cjk,
        "paragraphs_total": total,
        "type_counts": counts,
        "dwell_ratio": dwell_ratio,
        "ambient_ratio": ambient_ratio,
        "progression_density": progression_density,
        "is_iyashikei_genre": is_iyashikei,
        "genre_tags": sorted(genre_tags),
        "baseline_source": baseline_source,
        "baseline": {
            "dwell_ratio_min": dwell_min,
            "progression_density_max": prog_max,
        }
    })

    flags = []
    # 治愈系 genre 才报 advisory; 其他题材 silent
    if is_iyashikei:
        if dwell_ratio < dwell_min:
            flags.append({"code": ISSUE_CODE_DWELL_LOW,
                          "msg": f"dwell_ratio={dwell_ratio} < {dwell_min}·"
                                 f"治愈系应多停留品味·建议增 dwell 段"})
        if progression_density > prog_max:
            flags.append({"code": ISSUE_CODE_PROG_DENSE,
                          "msg": f"progression_density={progression_density} > {prog_max}·"
                                 f"治愈系不该每段都推进·允许「无事发生」"})
    else:
        out["genre_silent"] = True

    out["flags"] = flags
    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "dwell_progression", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "iyashikei_healing 治愈系节奏 · advisory · 绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] dwell_progression: {'·'.join(f['msg'] for f in flags)} — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="iyashikei dwell/progression scanner (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
