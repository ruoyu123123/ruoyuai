"""主题/母题/象征追踪 dim55 schema 测试（2026-06-15·解析 analyzer.md 断言·蒸馏端基建）。

记忆调研 critic 🔴最高优先：subsystem 无 theme/motif/symbol 追踪·冰山写法依赖母题贯穿。
dim55_象征意象:让 analyzer 产具体象征意象词（锚定抽象母题 B1_道德滤镜.母题）·补这个缺口。
这是【蒸馏端基建】——检测端（consolidate 聚合 + 复现 scanner）待 dim55 内容（gen-model 蒸馏跑）。
零依赖（读 .md）。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MD = (_ROOT / ".claude" / "agents" / "novel-distill-analyzer.md").read_text(encoding="utf-8")


def test_dim55_symbol_motif_schema():
    """dim55_象征意象:recurring_motif_images + motif_to_theme + image_recurrence 三字段。"""
    assert "dim55_象征意象" in _MD
    assert "recurring_motif_images" in _MD
    assert "motif_to_theme" in _MD
    assert "image_recurrence" in _MD


def test_dim55_anchors_abstract_motif_and_distinct():
    """dim55 锚定 B1_道德滤镜.母题（抽象母题→具体意象词）·且象征意象与物理道具/设定区分。"""
    assert "B1_道德滤镜" in _MD
    assert "非物理道具非设定" in _MD   # 与道具.json（物理）/世界观（设定）区分
