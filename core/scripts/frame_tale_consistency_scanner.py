#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frame_tale_consistency_scanner.py — Frame-Tale 嵌套叙事一致性卡
(advisory · cluster · shadow · 2026-06-20 · R8 W4 Batch-J · L29)

【缺口】R8 W4 联网调研(LHN Narrative Levels Genette + BookishBay Mise en Abyme +
Hearth.sh Story Within a Story + DMovies Rashomon): 谋略/重生/谍战类常用嵌套叙事
(故事中的故事/罗生门视角). LLM 默认不维护"谁讲/讲给谁/为何此时讲"三件套→ 嵌套
开闭不闭合、镜像不识别、叙述者悬空。

【做法 · 确定性纯规则(轻量版·占位 schema)】:
  1. 作者档 nested_narrative_profile 门控: allowed_max_depth(默认 2),
     entry_exit_required(默认 True), mise_en_abyme_target(默认 0=不要求)。
  2. 识别嵌套入口 (标志词: 讲起一个故事 / 我给你讲讲 / 想起当年 / 当年 / 那是N年前 /
     传说 / 据说 / 听说过 / 记得有一次 / 据传)。
  3. 识别嵌套出口 (回到当下 / 言归正传 / 说回 / 回到现实 / 思绪回笼 / 拉回神思)。
  4. depth 估算: 嵌套入口数 - 嵌套出口数; entry_exit_required=True 时 depth!=0 报错。
  5. allowed_max_depth: 入口累计数 > max_depth 报 advisory。

【与 L37 location 关联但独立】.

【北极星② / ⑤ 顾问非法官】门控严: 无 nested_narrative_profile → skip。
code FRAME_TALE_DRIFT 绝不进 hard_gate 。
env FRAME_TALE_MODE: off / shadow(默认) / active。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "FRAME_TALE_DRIFT"

ENTRY_MARKERS = re.compile(
    r"(讲起一个故事|讲一个故事|我给你讲讲|我来讲|想起当年|当年我|当年他|"
    r"那是N年前|那是\d+年前|那是很多年前|传说|据说|听说过|记得有一次|"
    r"据传|说起|话说|从前|往事重提|忆往昔|忆当年)")
EXIT_MARKERS = re.compile(
    r"(回到当下|言归正传|说回|回到现实|思绪回笼|拉回神思|拉回思绪|"
    r"回过神来|从回忆中醒来|说完这段|讲完这段|故事讲完)")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("FRAME_TALE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _resolve_profile(project_root) -> dict | None:
    if not project_root:
        return None
    ap = Path(project_root) / "_数据库" / "作者风格.json"
    if not ap.exists():
        return None
    try:
        obj = json.loads(ap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        prof = obj.get("nested_narrative_profile")
        if isinstance(prof, dict):
            return prof
        # 题材默认启用 (轻量 fallback)
        genre = (obj.get("genre") or "").strip().lower()
        if genre in {"scheming_politics", "regression", "espionage"}:
            return {"allowed_max_depth": 2, "entry_exit_required": True,
                    "_default_for_genre": genre}
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "frame_tale_consistency", "schema_version": "1.0",
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
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    profile = _resolve_profile(project_root)
    if not profile:
        out["note"] = "无 nested_narrative_profile 且非默认题材·跳过(北极星②)"
        return out

    entries = ENTRY_MARKERS.findall(draft)
    exits = EXIT_MARKERS.findall(draft)
    entry_n = len(entries)
    exit_n = len(exits)
    depth_imbalance = entry_n - exit_n
    max_depth = int(profile.get("allowed_max_depth", 2))
    exit_required = bool(profile.get("entry_exit_required", True))

    out.update({
        "entry_count": entry_n,
        "exit_count": exit_n,
        "depth_imbalance": depth_imbalance,
        "allowed_max_depth": max_depth,
        "entry_exit_required": exit_required,
        "profile_source": "author_profile" if "_default_for_genre" not in profile
        else f"genre_default ({profile['_default_for_genre']})",
    })

    msgs = []
    if exit_required and entry_n > 0 and depth_imbalance != 0:
        msgs.append(f"嵌套开闭不闭合: entry={entry_n} exit={exit_n}·"
                    f"剩余 {depth_imbalance} 层未回收")
    if entry_n > max_depth:
        msgs.append(f"嵌套深度 {entry_n} > allowed_max_depth={max_depth}")
    msg = " · ".join(msgs) if msgs else None

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "frame_tale", "severity": "minor",
                "message": msg, "entry_count": entry_n, "exit_count": exit_n,
                "max_depth": max_depth,
                "_doc": ("嵌套叙事一致性是工艺 advisory · 作者档 override · "
                         "绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] frame_tale_consistency: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Frame-Tale 嵌套叙事一致性(advisory · cluster · shadow)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
