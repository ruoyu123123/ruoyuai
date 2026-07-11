#!/usr/bin/env python3
"""author_profile_util.py — 作者风格档注入共享小模块

提供 `build_author_profile_block()` 和作者档缺失护栏。模块仅依赖 json/pathlib，
可由创作链直接导入，不加载判断层。
"""
from __future__ import annotations

import json
from pathlib import Path

AUTHOR_PROFILE_MISSING_GUARD = """\
# ⚠️ 作者风格档缺失（硬约束）
本项目的作者风格档（作者风格.json / skill）未能读取。按「作者档第一权威·通用规则仅兜底
且不得僭越」纪律：本次审查**不得输出任何风格/文笔/节奏类 finding**（句长/段长/腔调/
对话风格等一律不评）。只允许输出一致性/事实/格式契约类发现。
输出 JSON 须含 "_author_profile_missing": true。
"""


def build_author_profile_block(project_root: Path, max_chars: int = 30000) -> str | None:
    """读项目作者风格档（作者风格.json + 可选 skill 文件）拼注入块。读不到 → None。

    全量注入不截断（feedback_no_token_saving）——max_chars 仅作极端兜底（30k 字符
    ≈ 正常作者档 2-3 倍体量，正常档案永远不会触发）。
    """
    # 两布局（真 distill e2e 抓出）：novels 项目档在 _数据库/·styles 风格库档在项目根
    db = project_root / "_数据库"
    profile_path = db / "作者风格.json"
    if not profile_path.exists():
        profile_path = project_root / "作者风格.json"
        db = project_root
    if not profile_path.exists():
        return None
    try:
        profile_text = profile_path.read_text(encoding="utf-8").strip()
        json.loads(profile_text)  # 校验是合法 JSON（防半截文件混进 prompt）
    except (OSError, json.JSONDecodeError):
        return None

    parts = [f"# 作者风格档（第一权威 · 凡此档规定的维度以此为准，通用规则让位）\n"
             f"```json\n{profile_text}\n```"]
    # 可选 skill（蒸馏写作指导）——项目常拷为 skill_FINAL.md / skill_vN.md
    for cand in sorted(db.glob("skill*.md")) + sorted(db.glob("*skill*.md")):
        try:
            skill_text = cand.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        parts.append(f"# 作者写作 skill（{cand.name} · 与风格档同级权威）\n{skill_text}")
        break
    block = "\n\n".join(parts)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n…[极端兜底截断·正常作者档不应触发]"
    return block
