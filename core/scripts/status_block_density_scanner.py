#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_block_density_scanner.py — LitRPG 状态框/系统提示密度
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L59 P1 · 二次元/系统流)

【缺口】R10 联网调研(World Anvil Academy LitRPG Storyteller's Guide 40% stat
sheet + Dr.O Ludochemist 密度甜区 + NamuWiki Status Window + 起点游戏异界完本榜)
：LitRPG / 系统流核心质感【状态框 / 系统提示密度甜区】。LLM 默认要么塞爆每段
要么完全不写。此前【0 检测】。

【做法 · 确定性正则】：
  1. genre 门控：仅 litrpg / system_isekai / game_anime / horror_game /
     rule_anomaly 启用。
  2. 正则识别 【...】 / 〖...〗 / 『Status:』 / 『系统提示:』 / 『任务面板』 /
     『+N 经验』/ 『技能等级』.
  3. 输出 block_per_kCJK + block_to_prose_ratio + block_avg_cjk +
     interrupt_position_distribution。
  4. 作者档 status_block_baseline 比对 → 偏离 advisory。
  5. 作者档 0 status_block 反向 advisory(防 LLM 自作主张引入)。

【北极星② / ⑤】纯 advisory · genre 不匹配 skip · 绝不 hard_gate。
  env STATUS_BLOCK_MODE: off / shadow(默认) / active。

用法：python status_block_density_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "STATUS_BLOCK_DENSITY_DRIFT"
ISSUE_CODE_INTRO = "STATUS_BLOCK_UNAUTHORIZED_INTRODUCTION"
MIN_CJK = 500

GENRE_ALLOW = {"litrpg", "system_isekai", "game_anime", "horror_game",
               "rule_anomaly", "infinite_flow", "system_flow"}

BLOCK_PATTERNS = [
    (re.compile(r"【[^】]{1,80}】"), "square_bracket"),
    (re.compile(r"〖[^〗]{1,80}〗"), "fancy_bracket"),
    (re.compile(r"Status[:：][^\n]{1,80}", re.IGNORECASE), "status_label"),
    (re.compile(r"系统提示[:：][^\n]{1,80}"), "system_prompt"),
    (re.compile(r"任务[面板栏][:：]?[^\n]{1,80}"), "quest_panel"),
    (re.compile(r"\+\s*\d+\s*[一-龥]{1,4}"), "exp_gain"),
    (re.compile(r"技能\s*[一-龥]{1,6}\s*(等级|Lv)\s*\d+"), "skill_level"),
    (re.compile(r"血量[:：]\s*\d+|HP\s*[:：]?\s*\d+"), "hp_meter"),
]


def _mode() -> str:
    m = (os.environ.get("STATUS_BLOCK_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _cjk_count(text):
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _genre(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if isinstance(obj, dict):
        g = obj.get("genre") or obj.get("genre_pack")
        if isinstance(g, str):
            return g.strip().lower()
    return None


def _author_baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    return obj.get("status_block_baseline")


def detect_blocks(text):
    hits = []
    for rx, kind in BLOCK_PATTERNS:
        for m in rx.finditer(text):
            hits.append({"kind": kind, "match": m.group(0)[:60],
                         "pos": m.start(), "len": len(m.group(0))})
    hits.sort(key=lambda x: x["pos"])
    return hits


def scan(draft_path, project_root=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "status_block_density", "schema_version": "1.0",
           "mode": mode_env, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    g = _genre(project_root)
    out["genre"] = g
    if g not in GENRE_ALLOW:
        out["note"] = f"题材 {g} 非 LitRPG/系统流 · 跳过"
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
    hits = detect_blocks(text)
    per_kCJK = round(len(hits) / (cjk / 1000.0), 3) if cjk else 0
    block_total_cjk = sum(_cjk_count(h["match"]) for h in hits)
    block_to_prose_ratio = round(
        block_total_cjk / cjk, 4) if cjk else 0
    block_avg_cjk = round(
        block_total_cjk / len(hits), 2) if hits else 0
    # interrupt 位置：把全文分 4 段
    positions = [h["pos"] / max(len(text), 1) for h in hits]
    quarters = [0, 0, 0, 0]
    for pos in positions:
        idx = min(int(pos * 4), 3)
        quarters[idx] += 1
    out["block_per_kCJK"] = per_kCJK
    out["block_to_prose_ratio"] = block_to_prose_ratio
    out["block_avg_cjk"] = block_avg_cjk
    out["interrupt_position_distribution"] = quarters
    out["block_count"] = len(hits)
    out["sample_blocks"] = hits[:5]

    baseline = _author_baseline(project_root)
    msgs = []
    if baseline is not None:
        target = baseline.get("per_kCJK")
        if isinstance(target, (int, float)):
            if target == 0 and per_kCJK > 0:
                msgs.append({"code": ISSUE_CODE_INTRO,
                             "message": (f"作者基线 0 状态框但草稿出现 "
                                         f"{len(hits)} 块 · 防 LLM 自作主张引入")})
            elif target > 0 and per_kCJK < 0.4 * target:
                msgs.append({"code": ISSUE_CODE,
                             "message": (f"状态框密度 {per_kCJK}/千 < 作者基线 "
                                         f"{target} 的 40%")})
            elif target > 0 and per_kCJK > 2.5 * target:
                msgs.append({"code": ISSUE_CODE,
                             "message": (f"状态框密度 {per_kCJK}/千 > 作者基线 "
                                         f"{target} 的 2.5x · 塞爆")})
    if msgs:
        if mode_env == "active":
            for m in msgs:
                out["violations"].append({
                    "code": m["code"], "kind": "status_block",
                    "severity": "minor", "message": m["message"],
                    "_doc": "advisory · 作者档第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "; ".join(m["message"] for m in msgs)
        else:
            for m in msgs:
                print(f"[SHADOW] status_block: {m['message']} — 不上报",
                      file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="LitRPG 状态框密度 (advisory · shadow · 系统流)")
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
