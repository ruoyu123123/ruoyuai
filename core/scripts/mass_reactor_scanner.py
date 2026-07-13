#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mass_reactor_scanner.py — 群口段/弹幕式集体反应块
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L49 P1)

【缺口】R10 联网调研(Litreactor Storyville Chorus + Book Riot Greek chorus +
弹幕翻译大众人际传播 + 诡秘之主朝臣议论)：群口段(众人议论/弹幕/朝臣/路人)
是社会涟漪情节的高效推进手段。LLM 默认要么完全不写要么生硬塞独白。此前
【0 检测群口段密度】。

【做法 · 确定性纯规则】：
  1. 识别复数集合名词说话人：众人/朝臣/弟子们/网友/弹幕/路人/百姓/观众/
     吃瓜群众/系统消息/人群。
  2. 匿名引号串(无具名 attribution 的 "" 段)。
  3. 连续 ≥3 句反应聚簇 → 标 mass_reactor_block。
  4. 输出 {blocks, speaker_type, plot_advance_flag}。
  5. 作者档 author_mass_reactor_baseline 比例 → 偏离过大 advisory。

【北极星② / ⑤】纯 advisory · 阈值保守 · 绝不 hard_gate。
  env MASS_REACTOR_MODE: off / shadow(默认) / active。

用法：python mass_reactor_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE = "MASS_REACTOR_DENSITY_DRIFT"

MASS_SPEAKERS = re.compile(
    r"众人|众弟子|众臣|朝臣|弟子们|网友|网友们|弹幕|路人|百姓|观众|吃瓜群众|"
    r"听众|人群|众修|众仙|众官|围观[一-龥]{0,2}|围观者|群众|看客|"
    r"系统消息|系统提示")
DIALOGUE_LINE = re.compile(r"[“\"][^“”\"]{2,100}[”\"]")
PARA_SPLIT = re.compile(r"\n+")
MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("MASS_REACTOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    return load_json(p)


def _author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    return obj.get("author_mass_reactor_baseline")


def detect_blocks(text, min_lines=3):
    """连续 ≥3 句匿名/集合反应 → 1 block。

    扫所有行，找连续含引号的『dialogue run』+ 周围 ±2 行 mass speaker 锚点。
    """
    lines = text.splitlines()
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        # 寻找下一组连续 dialogue run
        if not DIALOGUE_LINE.search(lines[i]):
            i += 1
            continue
        run_start = i
        run_lines = []
        while i < n and DIALOGUE_LINE.search(lines[i]):
            run_lines.append(lines[i])
            i += 1
        if len(run_lines) < min_lines:
            continue
        # 检 ±2 行内是否有 mass speaker 标记
        ctx_start = max(0, run_start - 3)
        ctx_end = min(n, i + 3)
        ctx = "\n".join(lines[ctx_start:ctx_end])
        has_mass = bool(MASS_SPEAKERS.search(ctx))
        # 匿名比例 = run_lines 内没有具名 attribution 的占比
        named = 0
        for ln in run_lines:
            if re.search(r"[一-龥]{1,4}(说|道|笑道|低声道|沉声道|大喊|高呼)[:：]?[“\"]",
                         ln):
                named += 1
        anon_ratio = 1 - (named / len(run_lines))
        if has_mass or anon_ratio >= 0.6:
            blocks.append({"line_start": run_start,
                           "line_count": len(run_lines),
                           "has_mass_speaker": has_mass,
                           "anon_ratio": round(anon_ratio, 3)})
    return blocks


def scan(draft_path, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "mass_reactor", "schema_version": "1.0",
           "mode": mode_env, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    blocks = detect_blocks(text)
    per_1k = round(len(blocks) / (cjk / 1000.0), 3) if cjk else 0
    out["mass_reactor_blocks"] = len(blocks)
    out["mass_reactor_density"] = per_1k
    out["sample_blocks"] = blocks[:5]
    speaker_types = []
    for b in blocks:
        speaker_types.append("mass" if b["has_mass_speaker"] else "anon_chorus")
    out["speaker_types"] = speaker_types[:10]

    baseline = _author_baseline(project_root)
    msg = None
    if baseline is not None and isinstance(baseline.get("density_target"),
                                           (int, float)):
        target = baseline["density_target"]
        if target > 0 and per_1k > target * 2:
            msg = f"群口段密度 {per_1k}/千 超作者基线 {target} 的 2x"
        elif per_1k < 0.5 * target and target >= 0.05:
            msg = f"群口段密度 {per_1k}/千 < 作者基线 {target} 的 50%"
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "mass_reactor",
                "severity": "minor", "message": msg,
                "_doc": "advisory · 作者档第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] mass_reactor: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="群口段密度 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
