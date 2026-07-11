#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""failure_segment_prose_density_scanner.py — 失败段散文密度≥成功段·shadow

【缺口 · R22 W10 Batch-EE·P1 · 2026-06-21】failure prose enrichment theorem：
  成熟网文/严肃文学：try-fail 段落工艺密度（五感词/心理转折/段长方差/对话比）
  应≥同 cluster 同类成功段——读者要从失败里读出『代价感』。
  低工艺失败段 = 流水账受挫，读者无沉浸→工艺缺口 advisory。

【与既有 scanner 显式去重】
  - prose_rhythm_scanner：句长/段长 vs 作者基线
    本 scanner = 失败段 vs 成功段密度对比·正交（一个查整体节奏·一个查段内对比）
  - aspect_grounding_scanner：体貌词
    本 scanner = 五感/心理转折/方差·正交

【做法 · 确定性占位（零 LLM）】
  1. 从 changes/scope.try_fail_chain 标定失败段落范围（若 manifest 缺则用启发式
     失败词锚定：失败/搞砸/输/败/没成）
  2. 同 cluster 同类成功段（成功词：成功/赢/通过/达成）作对照
  3. 比较 4 指标：
     - 五感词密度（视觉/听觉/触觉/嗅觉/味觉 lexicon 占比）
     - 心理转折数（『但是/可是/然而』等转折词每千字）
     - 段长方差（参差度）
     - 对话比（含引号段占比）
  4. z<-0.5 → DENSITY_GAP advisory（失败段散文密度低于成功段）
  5. 作者档 _failure_segment_density_off=true 可关

【北极星⑤】顾问非法官·全 advisory·env FAILURE_SEGMENT_DENSITY_MODE
  FAILURE_SEGMENT_DENSITY_GAP 绝不进 audit_hub.HARD_GATE_CODES。

用法: python failure_segment_prose_density_scanner.py <draft> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

ISSUE_CODE = "FAILURE_SEGMENT_DENSITY_GAP"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 五感词典占位
SENSORY_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "tokens": [
        # 视觉
        "看", "望", "瞥", "亮", "黯", "色", "光", "影", "瞧",
        # 听觉
        "听", "响", "声", "鸣", "嘶", "嗡", "嚓",
        # 触觉
        "触", "摸", "冷", "热", "刺", "麻", "痛",
        # 嗅觉
        "闻", "嗅", "香", "臭", "腥",
        # 味觉
        "尝", "甜", "苦", "辣", "酸", "咸",
    ],
}

# 心理转折词占位
TRANSITION_LEXICON_PLACEHOLDER = {
    "_placeholder": True,
    "tokens": ["但是", "可是", "然而", "却", "竟", "不料", "偏偏", "反而", "倒是"],
}

# 失败/成功锚词占位
FAIL_ANCHORS = ("失败", "搞砸", "输了", "败北", "没成", "失利", "破灭", "崩了")
SUCC_ANCHORS = ("成功", "赢了", "通过", "达成", "胜利", "搞定", "完成了")


def _mode() -> str:
    m = (os.environ.get("FAILURE_SEGMENT_DENSITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _override_flag(project_root) -> bool:
    if not project_root:
        return False
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(obj.get("_failure_segment_density_off"))


def _split_paragraphs(text: str) -> list:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _segment_by_anchor(paras: list, anchors: tuple, halo: int = 2) -> list:
    """返回 [(start_idx, end_idx)] 包含锚词段及其 ±halo 段"""
    spans = []
    for i, p in enumerate(paras):
        if any(a in p for a in anchors):
            lo = max(0, i - halo)
            hi = min(len(paras), i + halo + 1)
            spans.append((lo, hi))
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _density_metrics(paras: list, spans: list) -> dict:
    if not spans:
        return {}
    sensory = SENSORY_LEXICON_PLACEHOLDER["tokens"]
    transitions = TRANSITION_LEXICON_PLACEHOLDER["tokens"]
    total_cjk = 0
    sens_hits = 0
    trans_hits = 0
    para_lens = []
    dialog_paras = 0
    n_paras = 0
    for lo, hi in spans:
        for p in paras[lo:hi]:
            c = _cjk_count(p)
            if c == 0:
                continue
            total_cjk += c
            para_lens.append(c)
            n_paras += 1
            if "“" in p or "「" in p or "\"" in p:
                dialog_paras += 1
            for w in sensory:
                sens_hits += p.count(w)
            for w in transitions:
                trans_hits += p.count(w)
    if total_cjk == 0:
        return {}
    return {
        "sensory_density": round(sens_hits / total_cjk * 1000, 3),
        "transitions_per_kcjk": round(trans_hits / total_cjk * 1000, 3),
        "para_length_stdev": round(statistics.pstdev(para_lens), 2) if len(para_lens) >= 2 else 0.0,
        "dialog_ratio": round(dialog_paras / n_paras, 3) if n_paras else 0.0,
        "total_cjk": total_cjk,
        "n_paras": n_paras,
    }


def _z_compare(fail_m: dict, succ_m: dict) -> dict:
    """成功段 metrics 当 baseline·失败段相对 z（简化：(fail-succ)/(succ+eps)）"""
    if not fail_m or not succ_m:
        return {}
    eps = 1e-6
    z = {}
    for k in ("sensory_density", "transitions_per_kcjk", "para_length_stdev", "dialog_ratio"):
        sv = succ_m.get(k, 0.0)
        fv = fail_m.get(k, 0.0)
        z[k] = round((fv - sv) / (abs(sv) + eps), 3)
    return z


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "failure_segment_prose_density", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _override_flag(project_root):
        out["note"] = "作者档 _failure_segment_density_off=true · 关闭"
        return out
    cjk = _cjk_count(text)
    if cjk < 1000:
        out["note"] = "草稿太短·跳过"
        return out

    paras = _split_paragraphs(text)
    out["paragraph_count"] = len(paras)

    fail_spans = _segment_by_anchor(paras, FAIL_ANCHORS)
    succ_spans = _segment_by_anchor(paras, SUCC_ANCHORS)
    out["fail_span_count"] = len(fail_spans)
    out["succ_span_count"] = len(succ_spans)

    if not fail_spans or not succ_spans:
        out["note"] = "失败段或成功段缺·无法对比·跳过"
        return out

    fail_m = _density_metrics(paras, fail_spans)
    succ_m = _density_metrics(paras, succ_spans)
    out["failure_metrics"] = fail_m
    out["success_metrics"] = succ_m

    z = _z_compare(fail_m, succ_m)
    out["z_relative"] = z

    low_dims = [k for k, v in z.items() if v < -0.5]
    out["low_density_dimensions"] = low_dims

    if low_dims:
        msg = (f"失败段散文密度低于成功段（{len(low_dims)} 维）："
               + "·".join(f"{k}(z={z[k]})" for k in low_dims[:3]))
        if mode == "active":
            out["violations"].append({
                "kind": "failure_segment_density_gap", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "low_dimensions": low_dims,
                "_doc": "failure prose enrichment theorem·作者档可关·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] failure_segment_prose_density: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="失败段散文密度 vs 成功段·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
