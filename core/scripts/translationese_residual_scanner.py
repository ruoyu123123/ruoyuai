#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translationese_residual_scanner.py — 中文译文味 4 桶 advisory(T-index)
(advisory · cluster · 2026-06-20 R11 W6 MODEST)

【缺口】EMNLP 2025 arXiv:2507.12260 T-index + NAACL-W 2018 arXiv:1804.08756 句法集 F>90%
+ 余光中《论的的不休》报告：LLM 中文输出残留译文味四征：
  de_stack_depth (多层 NP+的嵌套深度)
  bei_passive_density (被动句被字密度)
  pre_modifier_long_ratio (长定语前置占比·吸收 feedback-inverted-modifier)
  name_overrepetition (同 cluster 复名密度)

【做法 · 纯正则·确定性】
  4 桶 z-score vs 作者档 translationese_baseline·无→兜底带·shadow 默认
  · 作者档若规定该桶则桶让位

【北极星】② 作者档第一权威·⑤ advisory·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "TRANSLATIONESE_RESIDUAL"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
# 多层"的"嵌套深度（X的Y的Z的W），4 段以上为深栈
_DE_STACK = re.compile(r"(?:[一-鿿]{1,6}的){3,}")
# 被字句 + 给字被动
_BEI_PASSIVE = re.compile(r"(被|被人|被她|被他|被我|被它)[一-鿿]")
# 长定语前置(长定语+的+短主语后置)·吸收 inverted_modifier 单点锁
_LONG_PRE_MOD = re.compile(r"[一-鿿]{8,}的[一-鿿]{1,4}[，。！？]")
# 名字短串识别(2-3 个 CJK 连续)
_NAME_CAND = re.compile(r"[一-鿿]{2,3}")

DEFAULT_BASELINE = {
    "de_stack_depth_per_1k": 0.8,        # 兜底带
    "bei_passive_per_1k": 1.5,
    "pre_modifier_long_per_1k": 1.2,
    "name_overrep_per_1k": 4.0,
    "sd": {
        "de_stack_depth_per_1k": 0.5,
        "bei_passive_per_1k": 0.7,
        "pre_modifier_long_per_1k": 0.6,
        "name_overrep_per_1k": 1.5,
    },
}


def _mode() -> str:
    m = (os.environ.get("TRANSLATIONESE_RESIDUAL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj.get("translationese_baseline")


def _bucket_metrics(text: str, project_root=None) -> dict:
    cjk = _cjk_count(text)
    k = max(1, cjk / 1000.0)
    de_stack_hits = _DE_STACK.findall(text)
    bei_hits = _BEI_PASSIVE.findall(text)
    long_pre_hits = _LONG_PRE_MOD.findall(text)
    # name overrepetition：复名密度（出现 ≥3 次的 2-3 字名字短串）
    counts = {}
    for m in _NAME_CAND.finditer(text):
        s = m.group(0)
        counts[s] = counts.get(s, 0) + 1
    # 用 known_names 过滤·无则取频次 ≥3 的 2-3 字 token 当 proxy
    known = set()
    if project_root:
        kp = Path(project_root) / "_数据库" / "人物卡.json"
        if kp.exists():
            try:
                ko = json.loads(kp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                ko = None
            if isinstance(ko, dict):
                for k_ in ("known_names", "characters", "entries"):
                    v = ko.get(k_)
                    if isinstance(v, list):
                        for it in v:
                            if isinstance(it, str):
                                known.add(it)
                            elif isinstance(it, dict):
                                n = it.get("name") or it.get("姓名")
                                if isinstance(n, str):
                                    known.add(n.strip())
    if known:
        name_hits = sum(counts.get(n, 0) for n in known)
    else:
        name_hits = sum(v for v in counts.values() if v >= 3)
    return {
        "de_stack_depth_per_1k": round(len(de_stack_hits) / k, 3),
        "bei_passive_per_1k": round(len(bei_hits) / k, 3),
        "pre_modifier_long_per_1k": round(len(long_pre_hits) / k, 3),
        "name_overrep_per_1k": round(name_hits / k, 3),
        "samples": {
            "de_stack": de_stack_hits[:4],
            "bei_passive": bei_hits[:4],
            "long_pre_modifier": long_pre_hits[:4],
        },
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "translationese_residual", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out
    metrics = _bucket_metrics(text, project_root)
    out["metrics"] = metrics
    baseline = _read_baseline(project_root) or DEFAULT_BASELINE
    out["baseline_source"] = "author_profile" if _read_baseline(project_root) else "default_fallback"
    sd = baseline.get("sd", DEFAULT_BASELINE["sd"])
    flags = []
    for bucket in ("de_stack_depth_per_1k", "bei_passive_per_1k",
                   "pre_modifier_long_per_1k", "name_overrep_per_1k"):
        mu = baseline.get(bucket, DEFAULT_BASELINE[bucket])
        bsd = max(sd.get(bucket, DEFAULT_BASELINE["sd"][bucket]), 0.1)
        z = (metrics[bucket] - mu) / bsd
        if z > 1.5:
            flags.append({"bucket": bucket, "value": metrics[bucket],
                          "mu": mu, "sd": bsd, "z": round(z, 3)})
    out["flagged_buckets"] = flags
    if flags:
        msg = f"译文味 4 桶 z>1.5：{[f['bucket'] for f in flags]}"
        if mode == "active":
            out["violations"].append({
                "kind": "translationese", "severity": "minor",
                "message": msg, "flagged_buckets": flags,
                "_doc": "T-index·advisory·作者档优先·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] translationese: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="译文味 4 桶 advisory(T-index)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
