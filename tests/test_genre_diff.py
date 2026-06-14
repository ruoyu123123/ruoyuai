"""C4 genre 方向性 diff 测试（2026-06-14·consolidate G6-GENREBASE）。

守护（北极星⑤·兜底非权威·只产方向不产精确值·作者档第一权威）：
  1. 作者维度 vs 兜底基线 → 方向词(higher/lower)+粗档(明显/略)·无裸 ratio 数值键；
  2. ratio∈[0.85,1.15] 的维不进 diff(避免噪声)；
  3. 无 baseline 文件 → None(不崩·零回归)；
  4. 标点维摊平映射正确(excl_k 等对齐 _author_baseline)。
零依赖（stdlib）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


def test_diff_produces_direction_not_value():
    """作者 sentence_mean=33 vs base 22(ratio 1.5)→ higher·明显·无裸 ratio 键。"""
    d = cap._genre_baseline_diff({"sentence_length": {"mean": 33}})
    assert d is not None
    sm = d["dims"]["sentence_mean"]
    assert sm["direction"] == "higher"
    assert sm["magnitude"] == "明显"
    assert "ratio" not in sm  # 不暴露精确值
    assert d["_authority"] == "FALLBACK_NOT_AUTHORITATIVE"


def test_diff_skips_near_baseline():
    """ratio∈[0.85,1.15] 的维不进 dims（接近基线·无方向）。"""
    d = cap._genre_baseline_diff({"sentence_length": {"mean": 22}})  # ratio=1.0
    assert d is None or "sentence_mean" not in d.get("dims", {})


def test_diff_magnitude_buckets():
    """ratio≥1.4 → 明显·1.15<ratio<1.4 → 略。"""
    assert cap._genre_baseline_diff({"sentence_length": {"mean": 33}})["dims"]["sentence_mean"]["magnitude"] == "明显"   # 1.5
    assert cap._genre_baseline_diff({"sentence_length": {"mean": 27}})["dims"]["sentence_mean"]["magnitude"] == "略"    # 1.227


def test_diff_returns_none_when_no_baseline():
    """monkeypatch resource_path 指不存在 → None（不崩·蒸馏强制路径安全）。"""
    import frozen_util
    real = frozen_util.resource_path
    frozen_util.resource_path = lambda *p: Path("/__nonexistent__/genre_baseline.json")
    try:
        assert cap._genre_baseline_diff({"sentence_length": {"mean": 33}}) is None
    finally:
        frozen_util.resource_path = real


def test_diff_punctuation_mapping():
    """标点维摊平映射正确（excl_k 对齐 punctuation_density_per_1000.exclamation.mean）。"""
    d = cap._genre_baseline_diff(
        {"punctuation_density_per_1000": {"exclamation": {"mean": 12.0}}})  # base excl_k=4 → ratio 3.0
    assert d["dims"]["excl_k"]["direction"] == "higher"
    assert d["dims"]["excl_k"]["magnitude"] == "明显"
