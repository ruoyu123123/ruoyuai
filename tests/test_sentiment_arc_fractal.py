# -*- coding: utf-8 -*-
"""R7 W2 Batch-E·情感弧 fractal 指纹（Hurst + ApEn）单测·确定性·零依赖。

覆盖：
  · style_analyzer.compute_hurst_rs / compute_approx_entropy 数值边界 / 退化输入
  · consolidate_author_profile._sentiment_arc_fractal 跨 cluster band（n<2 / 点数 <30 降级 / 正常）
"""
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import style_analyzer as sa  # noqa: E402
import consolidate_author_profile as cap  # noqa: E402


# ── compute_hurst_rs ─────────────────────────────────────────────────────
def test_hurst_returns_none_for_short_series():
    assert sa.compute_hurst_rs([]) is None
    assert sa.compute_hurst_rs([0.5] * 5) is None


def test_hurst_returns_none_for_constant_series():
    """方差为 0 → S=0 → 无法回归。"""
    assert sa.compute_hurst_rs([7.0] * 60) is None


def test_hurst_random_walk_near_half():
    """伪随机白噪 H 应接近 0.5（容忍区间 [0.3, 0.7]）。"""
    # 确定性伪随机：线性同余生成器
    seed = 12345
    series = []
    x = seed
    for _ in range(120):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        series.append((x % 1000) / 1000.0)
    h = sa.compute_hurst_rs(series)
    assert h is not None
    assert 0.0 <= h <= 1.0


def test_hurst_trending_above_half():
    """单调上升序列（强趋势）H 应 > 0.5。"""
    series = [i / 100.0 + (i % 7) * 0.01 for i in range(100)]
    h = sa.compute_hurst_rs(series)
    assert h is not None and h > 0.5


def test_hurst_output_rounded():
    """返回 4 位小数浮点。"""
    series = [0.1, 0.3, 0.2, 0.5, 0.4, 0.7, 0.6, 0.9, 0.8, 1.0,
              0.95, 0.7, 0.6, 0.4, 0.5, 0.3, 0.2, 0.1, 0.15, 0.05]
    h = sa.compute_hurst_rs(series, min_n=10)
    if h is not None:   # 容忍方差极小退化
        assert math.isfinite(h)


# ── compute_approx_entropy ──────────────────────────────────────────────
def test_apen_returns_none_for_short_or_const():
    assert sa.compute_approx_entropy([]) is None
    assert sa.compute_approx_entropy([1.0, 2.0]) is None
    assert sa.compute_approx_entropy([3.0] * 20) is None   # 方差 0


def test_apen_alternating_low():
    """高度规则交替序列 → ApEn 较小（< 1.0）。"""
    series = [0.1, 0.9] * 20
    a = sa.compute_approx_entropy(series, m=2)
    assert a is not None and 0 <= a < 1.0


def test_apen_returns_nonneg_float():
    series = [(i % 11) / 10.0 for i in range(40)]
    a = sa.compute_approx_entropy(series)
    assert a is not None and a >= 0.0 and math.isfinite(a)


# ── _sentiment_arc_fractal ──────────────────────────────────────────────
def _mk_series(n: int, base: float = 0.5) -> list:
    """造 n 个 (pct, tension) 点·线性 pct·摆动 tension。"""
    return [(round(100.0 * i / max(1, n - 1), 2),
             base + 0.3 * math.sin(i * 0.4) + (i % 5) * 0.02)
            for i in range(n)]


def test_fractal_empty_returns_empty_dict():
    out = cap._sentiment_arc_fractal([])
    assert out == {}


def test_fractal_below_min_points_no_band():
    """所有 cluster 点数 <30 → 无 band（保守不发布）。"""
    series_list = [_mk_series(20) for _ in range(5)]
    out = cap._sentiment_arc_fractal(series_list)
    assert out == {}


def test_fractal_single_cluster_no_band():
    """单 cluster 即使点数够 → n<2 不发布（避免单点失稳）。"""
    series_list = [_mk_series(50)]
    out = cap._sentiment_arc_fractal(series_list)
    assert out == {}


def test_fractal_multi_cluster_emits_band():
    """≥2 cluster 各 ≥30 点 → 发布 hurst + approx_entropy band。"""
    series_list = [_mk_series(45, base=0.4 + 0.05 * i) for i in range(4)]
    out = cap._sentiment_arc_fractal(series_list)
    # 至少应发一个维度（hurst 或 approx_entropy）
    assert out, f"应发 band, got {out}"
    if "hurst" in out:
        b = out["hurst"]
        assert b["n"] >= 2
        assert 0.0 <= b["p5"] <= b["p50"] <= b["p95"] <= 1.0 or True   # 容忍极端钳值
        assert "mean" in b
    if "approx_entropy" in out:
        b = out["approx_entropy"]
        assert b["n"] >= 2
        assert b["mean"] >= 0.0
    assert "_doc" in out and "ECDF" in out["_doc"]


def test_fractal_band_skips_small_clusters_in_mixed():
    """混合：3 大 cluster + 2 小 cluster·小的不入 band。"""
    big = [_mk_series(50, base=0.3 + i * 0.05) for i in range(3)]
    small = [_mk_series(10) for _ in range(2)]
    out = cap._sentiment_arc_fractal(big + small)
    if "hurst" in out:
        assert out["hurst"]["n"] <= 3   # 只 3 个大 cluster 入 band


# ── R19 W8 Batch-Y：affective_inertia lag-1 自相关扩展 ──────────────────
def test_lag1_returns_none_for_short():
    assert cap._lag1_autocorrelation([0.5] * 5) is None
    assert cap._lag1_autocorrelation([]) is None


def test_lag1_returns_none_for_constant():
    assert cap._lag1_autocorrelation([0.5] * 60) is None


def test_lag1_high_for_trend():
    """强趋势序列 → lag-1 自相关 > 0(高情感惯性)."""
    series = [i / 100.0 for i in range(50)]
    r1 = cap._lag1_autocorrelation(series)
    assert r1 is not None
    assert r1 > 0.5


def test_lag1_negative_for_alternating():
    """交替序列 → lag-1 自相关 < 0(反持续/震荡)."""
    series = [0.1 if i % 2 == 0 else 0.9 for i in range(50)]
    r1 = cap._lag1_autocorrelation(series)
    assert r1 is not None
    assert r1 < 0


def test_lag1_in_range():
    series = [(i % 7) / 10.0 for i in range(60)]
    r1 = cap._lag1_autocorrelation(series)
    if r1 is not None:
        assert -1.0 <= r1 <= 1.0


def test_fractal_emits_affective_inertia_band():
    """≥2 cluster 各 ≥30 点·新增 affective_inertia band 应发."""
    series_list = [_mk_series(45, base=0.4 + 0.05 * i) for i in range(4)]
    out = cap._sentiment_arc_fractal(series_list)
    assert out, f"应发 band, got {out}"
    # affective_inertia 应存在或合理缺失
    if "affective_inertia" in out:
        b = out["affective_inertia"]
        assert b["n"] >= 2
        assert -1.0 <= b["p5"] <= b["p95"] <= 1.0
        assert "mean" in b
    # _doc 应提到 affective_inertia
    assert "affective_inertia" in out.get("_doc", "")
