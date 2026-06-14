"""D4/D6 analyzer schema 测试（2026-06-14·解析 analyzer.md 断言 schema·蒸馏端）。

D4：dim52 加 expectation_type(真/假期待) + continuity 加 ppp_triple(promise/progress/payoff)。
D6：dim54 叙述伦理(narrator_reliability/anachrony/focalization) 第一版只入档。
零依赖（读 .md）。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MD = (_ROOT / ".claude" / "agents" / "novel-distill-analyzer.md").read_text(encoding="utf-8")


def test_d4_dim52_expectation_type():
    """D4：dim52 加 expectation_type(真/假期待)。"""
    assert "expectation_type" in _MD
    assert "真期待" in _MD and "假期待" in _MD


def test_d4_ppp_triple_in_continuity():
    """D4：continuity 加 ppp_triple(promise/progress/payoff)。"""
    assert "ppp_triple" in _MD
    assert "promise" in _MD and "progress" in _MD and "payoff" in _MD


def test_d6_dim54_narrative_ethics():
    """D6：dim54 叙述伦理(narrator_reliability/anachrony/focalization)入档。"""
    assert "dim54_叙述伦理" in _MD
    assert "narrator_reliability" in _MD
    assert "anachrony_density" in _MD and "focalization" in _MD
