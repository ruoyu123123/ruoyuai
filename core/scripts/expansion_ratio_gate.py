#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expansion_ratio_gate.py — Genette 扩写率守门(brief→draft 字符比 z-band)
(advisory · cluster · 2026-06-20 · R8 W4 Batch-J · L32)

【缺口】R8 W4 联网调研(Hanwen Shen arXiv:2505.12572 Optimal Expansion + LongEval
arXiv:2502.19103 + arXiv:2309.06009 Content Reduction Surprisal): Genette duration
里的"摘要→场景"扩写率有作者基线。LLM 默认 1:5 到 1:30, 跨度大且无 z-band 校准 →
扩写不足(brief 抄写式) / 过度扩展(注水) 都是 advisory 信号。

【做法 · 确定性纯规则】:
  1. brief 字符数 = cluster.scope_summary + 各 scene_storyboard.summary 字符之和。
  2. draft 字符数 = 草稿 CJK 字符数。
  3. expansion_ratio = draft_chars / brief_chars (brief>=20 否则跳过)。
  4. 作者档 expansion_ratio_baseline = {mean, std, n}; band = mean ± 2 std。
  5. 落 band 外 advisory(偏低=brief 抄写感 / 偏高=注水嫌疑)。
  6. 无作者档基线 → 通用兜底 (4 ≤ ratio ≤ 60)。

【北极星② / ⑤ 顾问非法官】扩写率是工艺 advisory · 作者档第一权威 ·
code EXPANSION_RATIO_DRIFT 绝不进 hard_gate 。
env EXPANSION_RATIO_MODE: off / shadow(默认) / active。

用法: python expansion_ratio_gate.py <draft_path> [--project <root>] [--cluster-key <id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "EXPANSION_RATIO_DRIFT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("EXPANSION_RATIO_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _active_cluster_brief(project_root, cluster_key=None) -> dict | None:
    """读 active cluster 的 brief 信息 (scope_summary + scene_storyboard)。"""
    if not project_root:
        return None
    ec = Path(project_root) / "_数据库" / "事件簇.json"
    if not ec.exists():
        return None
    try:
        data = json.loads(ec.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    clusters = data.get("clusters") or []
    if cluster_key:
        for c in clusters:
            if isinstance(c, dict) and c.get("cluster_id") == cluster_key:
                return c
    for c in clusters:
        if not isinstance(c, dict):
            continue
        if (c.get("status") or "").strip().lower() in {"in_progress", "active"}:
            return c
    return None


def _brief_char_count(brief: dict) -> int:
    n = 0
    scope = brief.get("scope_summary") or ""
    if isinstance(scope, str):
        n += len(scope)
    sb = brief.get("scene_storyboard") or []
    if isinstance(sb, list):
        for sc in sb:
            if isinstance(sc, dict):
                for k in ("summary", "scene_summary", "description", "desc"):
                    v = sc.get(k)
                    if isinstance(v, str):
                        n += len(v)
            elif isinstance(sc, str):
                n += len(sc)
    return n


def _author_baseline(project_root):
    if not project_root:
        return None
    ap = Path(project_root) / "_数据库" / "作者风格.json"
    if not ap.exists():
        return None
    try:
        obj = json.loads(ap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(obj, dict):
        return None
    base = obj.get("expansion_ratio_baseline")
    if isinstance(base, dict) and isinstance(
            base.get("mean"), (int, float)) and isinstance(
            base.get("std"), (int, float)):
        return {"mean": float(base["mean"]), "std": float(base["std"]),
                "n": int(base.get("n", 0))}
    return None


def scan(draft_path, project_root=None, cluster_key=None) -> dict:
    mode = _mode()
    out = {"scanner": "expansion_ratio_gate", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    draft_chars = _cjk_count(draft)
    if draft_chars < 500:
        out["note"] = "草稿太短·跳过"
        return out

    brief = _active_cluster_brief(project_root, cluster_key)
    if not brief:
        out["note"] = "无 active cluster brief·跳过(北极星②)"
        return out
    brief_chars = _brief_char_count(brief)
    if brief_chars < 20:
        out["note"] = f"brief 字符数过少 (n={brief_chars})·跳过"
        out["brief_chars"] = brief_chars
        return out

    ratio = round(draft_chars / brief_chars, 2)
    out["brief_chars"] = brief_chars
    out["draft_chars"] = draft_chars
    out["expansion_ratio"] = ratio

    base = _author_baseline(project_root)
    if base and base["std"] > 0:
        lo = round(base["mean"] - 2 * base["std"], 2)
        hi = round(base["mean"] + 2 * base["std"], 2)
        source = f"author_baseline (mean={base['mean']} ± 2σ={base['std']})"
    else:
        lo, hi = 4.0, 60.0
        source = "fallback_generic (4 ≤ ratio ≤ 60)"
    out["band_low"] = lo
    out["band_high"] = hi
    out["band_source"] = source

    msg = None
    if ratio < lo:
        msg = (f"扩写率偏低: ratio={ratio} < {lo}({source})·"
               f"草稿可能 brief 抄写感(场景化展开不足)")
    elif ratio > hi:
        msg = (f"扩写率偏高: ratio={ratio} > {hi}({source})·"
               f"草稿可能注水(细节/铺陈过密)")

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "expansion_ratio", "severity": "minor",
                "message": msg, "ratio": ratio, "band": [lo, hi],
                "band_source": source,
                "_doc": ("扩写率是工艺 advisory·作者档第一权威·"
                         "题材/卷型差异天然合理·绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] expansion_ratio_gate: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Genette 扩写率守门(advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-key", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project, args.cluster_key)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
