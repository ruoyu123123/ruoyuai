#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""narrative_frequency_scanner.py — Genette frequency 三态检测（advisory · cluster · 2026-06-20）

【缺口】R9 联网调研：Genette《Narrative Discourse》(1980) 的 frequency 维度
（叙述频率·事件发生 N 次 vs 被叙述 M 次）是叙事学三大维度（order/duration/frequency）之三。
此前全系统：
  · L38 anachrony_order_scanner 查 order 维（同批落地）
  · L22 duration_mix_scanner 查 duration 维
  · 【frequency 维度零覆盖】——iterative（一次讲多次发生「每当…他都…」）/ singulative
    （一次讲一次发生·默认）/ repetitive（多次讲同一次发生·Rashomon 同事件复述）
    没有任何 scanner 测。LLM 默认全 singulative 写=单调线性叙述。
  本 scanner 补检测端·特别为 xianxia/cultivation/training_arc/slice_of_life 等需要 montage
  迭代笔法的题材服务。

【做法 · 确定性纯规则正则（不依赖 LLM）】：
  1. iterative 标志词（一次讲多次发生·montage 迭代）:
     "每当/总是/常常/经常/每次/每日/每天/每年/每到/每逢/日复一日/年复一年/每每/不时/时不时"
  2. repetitive 标志词（多次讲同一次发生·Rashomon 复述·"那一天又怎样了"重述）:
     "再次想起/又想到/又回想/反复浮现/再一次/又一次/再度/重新回到/再回想"
  3. singulative 默认状态（无 iterative/repetitive 标志）。
  4. 算 iterative_per_1k / repetitive_per_1k；
     iterative_ratio = iterative_per_1k / 总叙述密度（粗略 = 句号数/千字）。
  5. 作者档 frequency_baseline.iterative_per_1k_min 第一权威·无→通用兜底 0.08。
  6. iterative_per_1k < floor → advisory「frequency 维单调·缺 montage 迭代笔法」。

【题材包提示】xianxia/cultivation/training_arc/slice_of_life 修炼/日常题材天然需要 iterative
  笔法（"日复一日他打坐三年"·"每日辰时她去后山砍柴"）·这些题材作者档应设 iterative_per_1k_min
  ≥ 0.15（writer 端注入题材 prior 提示）。

【北极星② / ⑤ 顾问非法官】作者档 frequency_baseline 第一权威·爽文快节奏全 singulative 是合理
  风格选择 → 永远 advisory，code NARRATIVE_FREQUENCY_FLAT **绝不进 HARD_GATE_CODES**。
  env NARRATIVE_FREQUENCY_MODE: off / shadow(默认) / active。

用法：python narrative_frequency_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "NARRATIVE_FREQUENCY_FLAT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

ITERATIVE_TERMS = re.compile(
    r"每当|总是|常常|经常|每次|每日|每天|每年|每到|每逢|"
    r"日复一日|年复一年|每每|不时|时不时|时常|惯常|向来|从不")
REPETITIVE_TERMS = re.compile(
    r"再次想起|又想到|又回想|反复浮现|再一次|又一次|再度|"
    r"重新回到|再回想|又回到那一刻|反复看见")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 500
SAMPLE_LIMIT = 6
DEFAULT_ITERATIVE_FLOOR = 0.08   # per 1k CJK · 通用兜底
DEFAULT_REPETITIVE_FLOOR = 0.0   # 默认不查 repetitive 下限（Rashomon 笔法稀少正常）


def _mode() -> str:
    m = (os.environ.get("NARRATIVE_FREQUENCY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_baseline(project_root):
    """读作者档 frequency_baseline·无→None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "narrative_signature.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get("frequency_baseline")
        if isinstance(v, dict):
            return v
    return None


def detect_frequency(text: str) -> dict:
    """检测 iterative / singulative / repetitive 三态命中。

    返回 {iterative_count, repetitive_count, iterative_samples, repetitive_samples}。
    """
    text = _strip_changes(text)
    iter_hits = ITERATIVE_TERMS.findall(text)
    rep_hits = REPETITIVE_TERMS.findall(text)
    return {
        "iterative_count": len(iter_hits),
        "repetitive_count": len(rep_hits),
        "iterative_samples": list(dict.fromkeys(iter_hits))[:SAMPLE_LIMIT],
        "repetitive_samples": list(dict.fromkeys(rep_hits))[:SAMPLE_LIMIT],
    }


def scan(draft_path, project_root=None) -> dict:
    """Genette frequency 三态检测。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "narrative_frequency", "schema_version": "1.0", "mode": mode,
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
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    result = detect_frequency(draft)
    per_1k_base = cjk / 1000.0
    iter_per_1k = round(result["iterative_count"] / per_1k_base, 3)
    rep_per_1k = round(result["repetitive_count"] / per_1k_base, 3)

    baseline = _read_baseline(project_root)
    if baseline:
        iter_floor = float(baseline.get("iterative_per_1k_min", DEFAULT_ITERATIVE_FLOOR))
        baseline_source = "author_profile"
    else:
        iter_floor = DEFAULT_ITERATIVE_FLOOR
        baseline_source = "default_fallback"

    out["iterative_per_1k"] = iter_per_1k
    out["repetitive_per_1k"] = rep_per_1k
    out["iterative_samples"] = result["iterative_samples"]
    out["repetitive_samples"] = result["repetitive_samples"]
    out["baseline_source"] = baseline_source
    out["thresholds"] = {"iterative_floor": iter_floor}

    # 单向：仅报 iterative 过低（缺 montage 迭代笔法·全 singulative 单调）
    flat = iter_per_1k < iter_floor
    if flat:
        msg = (f"frequency 维单调：iterative {iter_per_1k}/千字 < {iter_floor}·"
               f"缺 montage 迭代笔法（每当/总是/日复一日…）·"
               f"叙述全 singulative 偏线性。建议引入迭代标志词压缩重复发生"
               f"（修炼/日常题材尤需）·爽文快节奏可豁免")
        if mode == "active":
            out["violations"].append({
                "kind": "narrative_frequency_flat", "severity": "minor",
                "message": msg,
                "iterative_per_1k": iter_per_1k,
                "repetitive_per_1k": rep_per_1k,
                "samples": result["iterative_samples"][:4],
                "_doc": "Genette Narrative Discourse 1980·frequency 三态(iterative/singulative/"
                        "repetitive)·xianxia/cultivation/training_arc/slice_of_life 题材尤需 montage·"
                        "全 singulative 是合理风格选择→advisory"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] narrative_frequency: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Genette frequency 三态检测（advisory）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 frequency_baseline 第一权威")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
