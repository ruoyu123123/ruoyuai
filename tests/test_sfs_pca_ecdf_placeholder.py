# -*- coding: utf-8 -*-
"""style_evaluator.compute_programmatic_score 的 pca_ecdf 字段位单测（R7 Batch-D · 2026-06-20）。

确定性·零依赖·零 LLM/零联网。
本批只接入字段位 + 占位线性 percentile 近似（PCA 拟合留下批）：
  ① 字段存在 + _placeholder=true 标记
  ② sfs_ecdf_percentile 用 linear (total-60)/30 → [0,1] 占位
  ③ 不影响现有 total / grade 行为
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import style_evaluator as se  # noqa: E402


def _mk_profile(**fields):
    """造一个最小 profile dict 让 compute_programmatic_score 不崩。"""
    base = {
        "sentence_length_distribution": {"5-10": 0.3, "10-20": 0.5, "20+": 0.2},
        "paragraph_length_distribution": {"1-30": 0.4, "30-60": 0.4, "60+": 0.2},
        "dialogue_ratio": 0.3,
        "punctuation_density_per_1000": {
            "comma": 50, "period": 30, "ellipsis": 5,
            "exclamation": 3, "question": 4,
        },
        "function_word_fingerprint_per_1000": {"的": 80, "了": 30, "是": 20},
        "sentence_stats": {"std": 8.0},
        "banned_word_hits": {},
        "ultra_short_para_ratio": 0.1,
        "single_sentence_para_ratio": 0.4,
        "onomatopoeia_para_count": 2,
        "speaker_count": 3,
        "inner_monologue_ratio": 0.05,
        "vocabulary_richness": {"type_token_ratio": 0.4, "hapax_ratio": 0.25},
    }
    base.update(fields)
    return base


def test_pca_ecdf_field_present():
    ref = _mk_profile()
    gen = _mk_profile()
    result = se.compute_programmatic_score(ref, gen, gen_text="一段示意正文" * 100)
    assert "pca_ecdf" in result
    pe = result["pca_ecdf"]
    assert pe["_placeholder"] is True
    assert pe["sfs_pca_weights"] is None
    assert "sfs_ecdf_percentile" in pe
    assert 0.0 <= pe["sfs_ecdf_percentile"] <= 1.0


def test_pca_ecdf_does_not_alter_total_grade():
    """字段位接入不应改变 total / grade。"""
    ref = _mk_profile()
    gen = _mk_profile()
    result = se.compute_programmatic_score(ref, gen, gen_text="一段示意正文" * 100)
    # total 应仍是各 dim 加权平均（不掺 pca_ecdf）
    total_check = sum(d["score"] * d["weight"] for d in result["dimensions"]) / \
        sum(d["weight"] for d in result["dimensions"])
    assert abs(result["total"] - round(total_check, 2)) < 0.01


def test_pca_ecdf_linear_fallback_high_total_approaches_one():
    """完美匹配（同 profile）→ total 接近满分 → percentile 应 = 1.0 上限。"""
    ref = _mk_profile()
    gen = _mk_profile()
    result = se.compute_programmatic_score(ref, gen, gen_text="一段示意正文" * 100)
    # 自比应 total 接近 100 → (100-60)/30 = 1.33 → 钳到 1.0
    assert result["pca_ecdf"]["sfs_ecdf_percentile"] == 1.0


def test_pca_ecdf_baseline_source_marked():
    """占位实现必须明示 source 是 linear_fallback（防消费者误以为已 PCA-ECDF）。"""
    ref = _mk_profile()
    gen = _mk_profile()
    result = se.compute_programmatic_score(ref, gen, gen_text="一段示意正文" * 100)
    assert result["pca_ecdf"]["sfs_ecdf_baseline_source"] == "linear_fallback_60_to_90"
