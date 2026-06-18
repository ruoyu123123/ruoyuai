"""阶段2 测试: skill_compactor 分类逻辑。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import skill_compactor  # noqa: E402


SAMPLE = """## 身份

你是一位写作者。

## 量化约束（v1 · 250 章基线）

| 句长 | 34 |

## v1 复刻校准（v0 实测差距）

校准 1: ...

## 铁律 1: 对话占比 ≥ 25%

具体说明...

## 标点节奏（per 1k 字）

逗号 62

## 章型识别 → 节奏映射

剧本类型...

## 风格演变

v0 → v1

## 开发者注释

设计说明
"""


def test_classifies_slow_sections():
    r = skill_compactor.compact_skill(SAMPLE)
    slow_titles = [s.title for s in r.slow]
    # 量化约束 + 标点节奏 应当进 SLOW
    assert any("量化约束" in t for t in slow_titles)
    assert any("标点节奏" in t for t in slow_titles)


def test_classifies_reference_sections():
    r = skill_compactor.compact_skill(SAMPLE)
    ref_titles = [s.title for s in r.reference]
    # v1 校准 + 风格演变 + 开发者 应当进 REFERENCE
    assert any("v1 复刻校准" in t for t in ref_titles)
    assert any("风格演变" in t for t in ref_titles)
    assert any("开发者" in t for t in ref_titles)


def test_default_to_fast():
    r = skill_compactor.compact_skill(SAMPLE)
    fast_titles = [s.title for s in r.fast]
    # 身份 + 铁律 + 章型识别 应当进 FAST
    assert any("身份" in t for t in fast_titles)
    assert any("铁律" in t for t in fast_titles)
    assert any("章型识别" in t for t in fast_titles)


def test_reference_priority_over_slow():
    """REFERENCE 关键词优先级 > SLOW: '校准'段含 v1 应进 REFERENCE 不是 SLOW。"""
    text = "## v1 校准 量化基线\n\n内容"
    r = skill_compactor.compact_skill(text)
    assert len(r.reference) == 1
    assert len(r.slow) == 0


def test_empty_skill_safe():
    r = skill_compactor.compact_skill("")
    assert r.stats["slow_chars"] == 0
    assert r.stats["fast_chars"] == 0


def test_no_headers_all_fast():
    """无标题正文 → 全归 FAST(单段 title=空)。"""
    text = "这是一段没有标题的正文。\n\n第二段。"
    r = skill_compactor.compact_skill(text)
    assert len(r.fast) >= 1
    assert all(s.title == "" for s in r.fast)


def test_emit_files_writes_three(tmp_path):
    r = skill_compactor.compact_skill(SAMPLE)
    paths = skill_compactor.emit_files(r, tmp_path)
    assert (tmp_path / "skill_FAST.md").exists()
    assert (tmp_path / "skill_SLOW.md").exists()
    assert (tmp_path / "skill_REFERENCE.md").exists()
    # SLOW 文件应带 PROTECTED 标记
    slow = (tmp_path / "skill_SLOW.md").read_text(encoding="utf-8")
    assert "PROTECTED" in slow


def test_real_skill_惊悚乐园_classification():
    """在真实大 skill 上至少能识别到几段 SLOW。"""
    real = REPO / "workspace" / "styles" / "惊悚乐园" / "skill_FINAL.md"
    if not real.exists():
        return  # CI 环境可能没有真实风格库,跳过
    text = real.read_text(encoding="utf-8")
    r = skill_compactor.compact_skill(text)
    # 至少抓到 5 段 SLOW (量化/标点/虚词/数值契约等)
    assert r.stats["slow_count"] >= 5
    # FAST 段不为空
    assert r.stats["fast_count"] >= 10


def test_total_chars_preserved():
    """三段 chars 之和 ≈ 原文 chars (允许极小差异因换行 strip)。"""
    r = skill_compactor.compact_skill(SAMPLE)
    total = r.stats["slow_chars"] + r.stats["fast_chars"] + r.stats["reference_chars"]
    # 允许少量损耗 (rstrip 段尾空白)
    assert abs(total - len(SAMPLE)) < 100
