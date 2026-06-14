#!/usr/bin/env python3
"""GUI 业务仪表盘 C2 测试（build_business_dashboard 聚合·真临时项目·零联网零写创作）。

复用 token_ledger + release_calendar 聚合「写到哪 + 烧多少 token + 发布节奏」给 GUI 只读面板。
"""
import json
import sys
from pathlib import Path

import core.gui.runner as gr

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))


def test_dashboard_empty_project(tmp_path):
    """空项目（无章节无账本）→ 全 0 不崩。"""
    d = gr.build_business_dashboard(tmp_path)
    assert d["chapter_count"] == 0
    assert d["total_chars"] == 0
    assert d["total_tokens"] == 0
    assert d["token_calls"] == 0
    assert d["risk_level"] == "critical"        # 0 囤稿


def test_dashboard_aggregates_chapters_and_tokens(tmp_path):
    """聚合章节字数 + token 账本 + 发布节奏（三数据源·复用 token_ledger + release_calendar）。"""
    for i in (1, 2):
        cd = tmp_path / "章节" / f"第{i:03d}章"
        cd.mkdir(parents=True)
        (cd / f"第{i:03d}章.txt").write_text("中文内容测试" * 1000, encoding="utf-8")
    db = tmp_path / "_数据库"
    db.mkdir(parents=True)
    (db / ".token_ledger.jsonl").write_text(
        json.dumps({"model": "gemini", "prompt_tokens": 1000, "output_tokens": 500,
                    "total_tokens": 1500}) + "\n", encoding="utf-8")
    d = gr.build_business_dashboard(tmp_path)
    assert d["chapter_count"] == 2
    assert d["total_chars"] == 12000            # 2 章 × 6 CJK × 1000
    assert d["token_calls"] == 1
    assert d["total_tokens"] == 1500
    assert d["output_tokens"] == 500
    assert d["days_of_buffer"] == 2.0           # 12000 / 6000 日更目标
    assert d["risk_level"] == "critical"        # <3 天囤稿
    assert d["release_advice"]


def test_dashboard_token_ledger_missing_safe(tmp_path):
    """有章节无 token 账本 → token 全 0·字数仍聚合（账本缺失不崩）。"""
    cd = tmp_path / "章节" / "第001章"
    cd.mkdir(parents=True)
    (cd / "第001章.txt").write_text("正文" * 30000, encoding="utf-8")  # 60000 CJK
    d = gr.build_business_dashboard(tmp_path)
    assert d["chapter_count"] == 1
    assert d["total_chars"] == 60000
    assert d["total_tokens"] == 0               # 无账本
    assert d["risk_level"] == "safe"            # 60000/6000 = 10 天 ≥7
