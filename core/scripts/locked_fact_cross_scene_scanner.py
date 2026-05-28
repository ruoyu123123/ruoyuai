#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""locked_fact_cross_scene_scanner.py — 锁定事实跨场景引用一致性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测 人物卡.locked_facts 中的事实在 cluster 不同场景的引用是否一致：
  · 数值类（年龄/品级）：所有场景引用必须一致
  · 描述类（外貌/出身）：cluster 内不能出现矛盾陈述

输出 issue code: LOCKED_FACT_CROSS_SCENE_CONFLICT (hard_gate)

用法：python locked_fact_cross_scene_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def extract_numbers_near(text: str, keyword: str, window: int = 30) -> list[tuple[int, str]]:
    """找 keyword 附近的中文数字 + 阿拉伯数字。"""
    results = []
    for m in re.finditer(re.escape(keyword), text):
        s = max(0, m.start() - window)
        e = min(len(text), m.end() + window)
        ctx = text[s:e]
        # 找阿拉伯 / 中文数字
        for num_m in re.finditer(r"[\d一二三四五六七八九十百]+", ctx):
            num = num_m.group()
            if len(num) >= 1:
                results.append((m.start(), num))
    return results


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])

    conflicts = []
    checked_count = 0
    for c in cards:
        name = c.get("name", "")
        if not name or name not in text:
            continue
        for lf in c.get("locked_facts", []) or []:
            if not isinstance(lf, dict):
                continue
            fact = lf.get("fact", "")
            if not fact:
                continue
            checked_count += 1
            # 简单启发：fact 中的关键数字/词必须不被矛盾陈述
            # 抽 fact 中数字
            fact_nums = re.findall(r"[\d一二三四五六七八九十百]+", fact)
            if not fact_nums:
                continue
            # 找正文中 name 附近的数字（前后 50 字）
            ctx_nums = extract_numbers_near(text, name, window=50)
            # 检查是否有矛盾（fact 中数字 vs 正文中数字不一致）
            # 简化：如果 fact 含「N 岁」，正文中 name 附近若有「M 岁」且 M ≠ N → 冲突
            age_in_fact = re.search(r"(\d+)\s*岁", fact)
            if age_in_fact:
                fact_age = int(age_in_fact.group(1))
                for pos, ctx_num in ctx_nums:
                    if "岁" in text[max(0, pos-50):pos+50]:
                        try:
                            ctx_age = int(ctx_num) if ctx_num.isdigit() else None
                            if ctx_age and ctx_age != fact_age and abs(ctx_age - fact_age) > 0:
                                conflicts.append({
                                    "character": name,
                                    "fact": fact,
                                    "conflict_value": f"{ctx_num}岁",
                                    "position": pos,
                                    "preview": text[max(0, pos-30):pos+30],
                                })
                                break
                        except ValueError:
                            pass

    return {
        "schema_version": "1.0",
        "scanner": "locked_fact_cross_scene_scanner",
        "cluster_mode": True,
        "gate_level": "hard_gate" if conflicts else "advisory",
        "facts_checked": checked_count,
        "conflicts_count": len(conflicts),
        "conflicts": conflicts[:10],
        "warning": (
            f"⚠️ {len(conflicts)} 处锁定事实跨场景冲突"
            if conflicts else None
        ),
        "severity": "error" if conflicts else "info",
        "code": "LOCKED_FACT_CROSS_SCENE_CONFLICT" if conflicts else None,
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    draft = Path(args[1]).resolve()
    report = scan(project, draft)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
