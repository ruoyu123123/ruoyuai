# -*- coding: utf-8 -*-
"""rhetorical_inventory R22 W10 Batch-DD · P0 STRONG · 陈望道 38 格四类清单
确定性·零依赖·零 LLM/零联网。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import rhetorical_inventory as ri  # noqa: E402


def test_load_inventory_has_4_categories():
    inv = ri.load_inventory()
    figs = inv.get("figures") or {}
    for c in ("material", "imagery", "wording", "syntax"):
        assert c in figs


def test_figures_have_terms_each():
    inv = ri.load_inventory()
    figs = inv.get("figures") or {}
    # 每格至少 4 词（占位）
    for cat, cat_figs in figs.items():
        for fig_name, terms in cat_figs.items():
            assert isinstance(terms, list)
            assert len(terms) >= 4, f"{cat}.{fig_name} 词太少 {len(terms)}"


def test_count_figure_hits_basic():
    text = "他像猛虎一样扑过去。她仿佛见鬼。"
    hits = ri.count_figure_hits(text)
    # 材料类的譬喻应有命中（像/仿佛）
    assert hits["material"]["譬喻"] >= 2


def test_category_totals_sum():
    text = "他像猛虎扑过去。万丈光芒。" * 5
    per_fig = ri.count_figure_hits(text)
    totals = ri.category_totals(per_fig)
    assert totals["material"] > 0
    assert totals["imagery"] > 0


def test_category_distribution_normalizes():
    text = "他像猛虎一样扑过去。万丈光芒。" * 10
    dist = ri.category_distribution(text)
    total = sum(dist.values())
    assert abs(total - 1.0) < 1e-6


def test_distribution_empty_text_uniform():
    dist = ri.category_distribution("xxx no chinese")
    # 全 0 hits → 均匀分布
    for c in ri.CATEGORIES:
        assert abs(dist[c] - 0.25) < 1e-6


def test_shannon_entropy_uniform_is_2():
    dist = {c: 0.25 for c in ri.CATEGORIES}
    H = ri.shannon_entropy(dist)
    assert abs(H - 2.0) < 1e-3


def test_shannon_entropy_one_hot_zero():
    dist = {"material": 1.0, "imagery": 0.0, "wording": 0.0, "syntax": 0.0}
    H = ri.shannon_entropy(dist)
    assert H == 0.0


def test_kl_divergence_zero_when_equal():
    a = {c: 0.25 for c in ri.CATEGORIES}
    b = {c: 0.25 for c in ri.CATEGORIES}
    assert ri.kl_divergence(a, b) < 1e-3


def test_kl_divergence_positive_when_different():
    a = {"material": 0.7, "imagery": 0.1, "wording": 0.1, "syntax": 0.1}
    b = {c: 0.25 for c in ri.CATEGORIES}
    kl = ri.kl_divergence(a, b)
    assert kl > 0.1


def test_match_subset_by_genre_known():
    inv = ri.load_inventory()
    subset = ri.match_subset_by_genre(inv, "仙侠")
    assert isinstance(subset, list) and len(subset) >= 1


def test_match_subset_by_genre_unknown_fallback():
    inv = ri.load_inventory()
    subset = ri.match_subset_by_genre(inv, "_unknown_")
    assert subset == ["比拟", "排比"]


def test_per_kcjk_normalizes():
    text = "他像猛虎扑过去。" * 50  # 6+6=12 CJK 一份 × 50 = ~ 多
    per_fig = ri.count_figure_hits(text)
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    out = ri.per_kcjk(per_fig, cjk)
    # 起码材料类的譬喻 per_kcjk 是浮点
    assert isinstance(out["material"]["譬喻"], float)


def test_per_kcjk_zero_cjk_safe():
    out = ri.per_kcjk({c: {"x": 1} for c in ri.CATEGORIES}, 0)
    assert out["material"]["x"] == 0.0
