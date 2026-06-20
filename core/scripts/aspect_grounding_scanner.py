#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aspect_grounding_scanner.py — 中文体貌前景-背景密度检测（首次落地·advisory · cluster · 2026-06-20 R13 W6 Batch-R · P1 STRONG · shadow）

【缺口 · R13 STRONG·全系统首次】Li 2014 Studies in Language 38:1 + Xiao&McEnery 2004
Benjamins corpus + perfective paradox-guo 跨语言 + Zai/Zhe 构式语法：
  中文体貌（aspect grounding）= 前景/背景比构成叙事节奏的底层指纹。
  - bare 「了」结句：完成相·推进
  - 「着/在/正/方/犹/兀自」：未完成相/进行/伴随·背景
  - 「将/就/即将」：prospective·前瞻
  - 「过」：experiential·经验回顾
  LLM 默认易写「裸 + 了。」结句 streak → 全是事件推进无背景层。本 scanner 补检测。

【与既有 scanner 严格正交】
  - R7 prose_rhythm           查 mean/std/段长
  - R8 duration_mix           查 scene/summary/ellipsis 占比
  - R9 anachrony_order        查 analepsis/prolepsis 锚词
  - R12 narrative_frequency   查单次/重复/反复事件
  本 scanner = 中文动词体貌层（aspect grammar）唯一覆盖维度。

【做法 · 确定性正则·零 LLM·零联网】
  1. bare_le_unbounded_streak: 连续 ≥4 个『…了。』结句且窗口内无 bounding 表达
     bounding 表达 = 时间量词(三天/一个月/半晌/良久)|完成补语(完/光/掉/尽)|地点终点(到/进/出+地点)
  2. background_marker_ratio: {着/在/正/方/犹/兀自} token 数 / 1k CJK
     与作者档 aspect_baseline.background_per_1k 做 z-band 比较（无作者档 → 兜底 [1.5, 9.0]/1k）
  3. prospective_overuse: 「将/就/即将」 token 数密度
     - 在 retro/回顾段（含「当年/史载/那时/那年/记得」语境）超 +2σ → 报
     - 默认兜底密度 > 6.0/1k CJK 报
  4. guo_experiential_misuse: 「过」 token 在主线推进段密度
     - 默认密度 > 5.5/1k CJK 报（warn 滥用经验体）

【北极星】②④⑤ 作者档第一权威·cluster 视野·advisory shadow·绝不 hard_gate
  ASPECT_GROUNDING_THIN / OVERUSE 绝不进 audit_hub.HARD_GATE_CODES。

  consolidate_author_profile 增 aspect_baseline 占位段（同批落地·确定性聚合·非空才写）。

env ASPECT_GROUNDING_MODE: off / shadow(默认) / active
用法: python aspect_grounding_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_THIN = "ASPECT_GROUNDING_THIN"
ISSUE_CODE_OVERUSE = "ASPECT_GROUNDING_OVERUSE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 兜底基线（无作者档时·shadow 默认稳健）
DEFAULT_BG_PER_1K_LOW = 1.5
DEFAULT_BG_PER_1K_HIGH = 9.0
DEFAULT_PROSPECTIVE_PER_1K_MAX = 6.0
DEFAULT_GUO_PER_1K_MAX = 5.5
DEFAULT_BARE_LE_STREAK_THRESHOLD = 4

# 背景标记字（构式语法）
BG_MARKERS = ("着", "在", "正", "方", "犹", "兀自")
# 前瞻标记
PROSPECTIVE_MARKERS = ("将", "就", "即将")
# 经验体
GUO_MARKER = "过"

# bounding 表达（含完成补语 + 时间量词 + 终点结构）
_BOUNDING_PATTERNS = re.compile(
    r"(完|光|掉|尽|净|遍|过去|完毕|结束|"  # 完成补语
    r"三天|两天|一日|一夜|半晌|良久|片刻|许久|很久|多年|"   # 时间量词
    r"到了|进了|出了|抵达|来到|走到)"
)

# 回顾语境锚（prospective_overuse 子检测）
RETRO_CONTEXT_MARKERS = ("当年", "史载", "那时", "那年", "记得", "回想", "彼时", "昔日", "从前")


def _mode() -> str:
    m = (os.environ.get("ASPECT_GROUNDING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？…])", text) if s.strip()]


def _read_author_baseline(project_root) -> dict | None:
    """读 作者风格.json/作者风格_FINAL.json 的 aspect_baseline 段。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for p in (db / "作者风格.json", db / "作者风格_FINAL.json"):
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            ab = obj.get("aspect_baseline")
            if isinstance(ab, dict):
                return ab
    return None


def _is_bare_le_sentence(s: str) -> bool:
    """判断是否『…了。』结句且不含 bounding 表达。"""
    if not s:
        return False
    # 句末必须是 了 + 标点
    last = s.rstrip()
    if not last:
        return False
    # 移除句末标点
    while last and last[-1] in "。！？…":
        last = last[:-1]
    if not last.endswith("了"):
        return False
    # 含 bounding → 视为有界·不计 bare
    if _BOUNDING_PATTERNS.search(s):
        return False
    return True


def _bare_le_streaks(sentences: list[str]) -> tuple[int, int]:
    """返回 (最大连续 bare 了 streak, bare 了 总句数)。"""
    max_streak = streak = 0
    bare_total = 0
    for s in sentences:
        if _is_bare_le_sentence(s):
            streak += 1
            bare_total += 1
            if streak > max_streak:
                max_streak = streak
        else:
            streak = 0
    return max_streak, bare_total


def _count_markers(text: str, markers) -> int:
    n = 0
    for m in markers:
        n += text.count(m)
    return n


def _retro_segments(text: str) -> list[str]:
    """切回顾段：以 RETRO_CONTEXT_MARKERS 为锚 + 后续 200 CJK 窗口。"""
    out = []
    for marker in RETRO_CONTEXT_MARKERS:
        idx = 0
        while True:
            i = text.find(marker, idx)
            if i < 0:
                break
            seg = text[i: i + 200]
            out.append(seg)
            idx = i + len(marker)
    return out


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "aspect_grounding", "schema_version": "1.0",
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

    sentences = _split_sentences(text)
    bare_streak, bare_total = _bare_le_streaks(sentences)

    bg_count = _count_markers(text, BG_MARKERS)
    bg_per_1k = round(bg_count / (cjk / 1000.0), 3) if cjk else 0.0

    prospective_count = _count_markers(text, PROSPECTIVE_MARKERS)
    prospective_per_1k = round(prospective_count / (cjk / 1000.0), 3)

    # 经验体「过」：排除介词「过」等不到完美，简化为子串计数
    guo_count = text.count(GUO_MARKER)
    guo_per_1k = round(guo_count / (cjk / 1000.0), 3)

    # retro 段内 prospective 密度
    retro_text = "\n".join(_retro_segments(text))
    retro_cjk = _cjk_count(retro_text)
    retro_pro_per_1k = (round(_count_markers(retro_text, PROSPECTIVE_MARKERS) / (retro_cjk / 1000.0), 3)
                       if retro_cjk >= 100 else 0.0)

    baseline = _read_author_baseline(project_root)
    bg_band_low = DEFAULT_BG_PER_1K_LOW
    bg_band_high = DEFAULT_BG_PER_1K_HIGH
    pro_max = DEFAULT_PROSPECTIVE_PER_1K_MAX
    guo_max = DEFAULT_GUO_PER_1K_MAX
    bare_streak_thr = DEFAULT_BARE_LE_STREAK_THRESHOLD
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("background_per_1k_band"), list) and len(baseline["background_per_1k_band"]) == 2:
            bg_band_low, bg_band_high = float(baseline["background_per_1k_band"][0]), float(baseline["background_per_1k_band"][1])
        if isinstance(baseline.get("prospective_per_1k_max"), (int, float)):
            pro_max = float(baseline["prospective_per_1k_max"])
        if isinstance(baseline.get("guo_per_1k_max"), (int, float)):
            guo_max = float(baseline["guo_per_1k_max"])
        if isinstance(baseline.get("bare_le_streak_threshold"), int):
            bare_streak_thr = int(baseline["bare_le_streak_threshold"])

    out.update({
        "bare_le_streak_max": bare_streak,
        "bare_le_total": bare_total,
        "background_per_1k": bg_per_1k,
        "background_count": bg_count,
        "prospective_per_1k": prospective_per_1k,
        "guo_per_1k": guo_per_1k,
        "retro_prospective_per_1k": retro_pro_per_1k,
        "baseline_source": baseline_source,
        "baseline": {
            "background_per_1k_band": [bg_band_low, bg_band_high],
            "prospective_per_1k_max": pro_max,
            "guo_per_1k_max": guo_max,
            "bare_le_streak_threshold": bare_streak_thr,
        }
    })

    flags = []
    # 1. bare le streak
    if bare_streak >= bare_streak_thr:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"连续 {bare_streak} 句『…了。』结句无 bounding（≥{bare_streak_thr}）·完成相 streak·建议穿插着/在/正等背景层"})
    # 2. background marker low
    if bg_per_1k < bg_band_low:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"background_per_1k={bg_per_1k} < {bg_band_low}·背景层稀薄·过度前景推进"})
    # 3. background marker high（过度使用 → overuse）
    if bg_per_1k > bg_band_high:
        flags.append({"code": ISSUE_CODE_OVERUSE,
                      "msg": f"background_per_1k={bg_per_1k} > {bg_band_high}·背景标记滥用"})
    # 4. prospective overuse（retro 段 +2σ 近似为 retro 密度 > 通用 pro_max·1.5）
    if retro_cjk >= 100 and retro_pro_per_1k > pro_max * 1.5:
        flags.append({"code": ISSUE_CODE_OVERUSE,
                      "msg": f"retro 段内 prospective_per_1k={retro_pro_per_1k} 异常偏高（>{pro_max*1.5:.2f}）·回顾段不该频繁前瞻"})
    elif prospective_per_1k > pro_max:
        flags.append({"code": ISSUE_CODE_OVERUSE,
                      "msg": f"prospective_per_1k={prospective_per_1k} > {pro_max}·前瞻标记过用"})
    # 5. guo 经验体滥用
    if guo_per_1k > guo_max:
        flags.append({"code": ISSUE_CODE_OVERUSE,
                      "msg": f"guo_per_1k={guo_per_1k} > {guo_max}·经验体『过』主线推进位置异常密度"})

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "aspect_grounding", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Li 2014 / Xiao&McEnery 2004·中文体貌·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] aspect_grounding: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="中文体貌前景-背景密度 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
