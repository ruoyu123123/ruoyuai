#!/usr/bin/env python3
# 🔴 2026-06-28 移除exe/gen-model梳理方向
"""author_profile_util.py 测试——承接原 test_judge_runner.py 对 build_author_profile_block
的覆盖（None 分支 / 损坏档当缺失 / 两布局 / skill 注入 / GUARD 常量），随符号迁出而迁移。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import author_profile_util as apu  # noqa: E402


def _mk_proj(tmp, *, in_db=True, with_profile=True, profile_obj=None):
    """造临时项目。in_db=True → novels 布局(_数据库/)·False → styles 布局(项目根)。"""
    proj = Path(tmp) / "proj"
    base = proj / "_数据库" if in_db else proj
    base.mkdir(parents=True)
    if with_profile:
        obj = profile_obj if profile_obj is not None else {"句长均值": 31, "段落均长": 52}
        (base / "作者风格.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj, base


# ============ None 分支（承接原 test_corrupt_author_profile_treated_as_missing）============
def test_missing_profile_returns_none():
    """作者档不存在 → None（绝不退回通用规则）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, _ = _mk_proj(tmp, with_profile=False)
        assert apu.build_author_profile_block(proj) is None


def test_corrupt_author_profile_treated_as_missing():
    """半截/非法 JSON 当缺失处理 → None（防半截文件混进 prompt）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, _ = _mk_proj(tmp, with_profile=False)
        (proj / "_数据库" / "作者风格.json").write_text("{半截", encoding="utf-8")
        assert apu.build_author_profile_block(proj) is None


# ============ 正常注入 ============
def test_db_layout_returns_block_with_authority_header():
    """novels 布局(_数据库/作者风格.json) → 注入块含第一权威头 + JSON 全文。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, _ = _mk_proj(tmp, in_db=True)
        block = apu.build_author_profile_block(proj)
        assert block is not None
        assert "作者风格档（第一权威" in block
        assert "句长均值" in block


def test_root_layout_returns_block():
    """styles 布局(项目根/作者风格.json) → 同样命中。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, _ = _mk_proj(tmp, in_db=False)
        block = apu.build_author_profile_block(proj)
        assert block is not None and "段落均长" in block


def test_skill_md_appended_when_present():
    """同目录有 skill*.md → 拼到注入块作同级权威。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, base = _mk_proj(tmp, in_db=True)
        (base / "skill_FINAL.md").write_text("# 作者写作指导\n短句连发。", encoding="utf-8")
        block = apu.build_author_profile_block(proj)
        assert "作者写作 skill" in block and "短句连发" in block


def test_max_chars_extreme_fallback_truncates():
    """超 max_chars 极端兜底截断（正常档不触发）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj, _ = _mk_proj(tmp, in_db=True, profile_obj={"x": "字" * 500})
        block = apu.build_author_profile_block(proj, max_chars=100)
        assert block.endswith("…[极端兜底截断·正常作者档不应触发]")
        assert len(block) <= 100 + len("\n…[极端兜底截断·正常作者档不应触发]")


# ============ GUARD 常量 ============
def test_missing_guard_constant_content():
    """缺失守卫常量含「不得输出风格 finding」核心约束 + _author_profile_missing 标记。"""
    assert "不得输出任何风格/文笔/节奏类 finding" in apu.AUTHOR_PROFILE_MISSING_GUARD
    assert "_author_profile_missing" in apu.AUTHOR_PROFILE_MISSING_GUARD


# 🔴 2026-06-28 移除exe/gen-model梳理方向：原 test_parity_with_judge_runner（断言
# author_profile_util 与迁出源 judge_runner 双份一致）已删——judge_runner 整模块删除，
# author_profile_util 成为 AUTHOR_PROFILE_MISSING_GUARD / build_author_profile_block 唯一权威。
