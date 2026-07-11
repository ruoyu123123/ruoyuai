"""style_evaluator 多参考区间 profile + 作者档 baseline 契约回归。"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402


def _baseline() -> dict:
    return {
        "quantitative": {
            "sentence_length": {"mean": 28.1, "std": 16.2},
            "dialogue_ratio": {"mean": 0.21},
            "chapter_words": {"mean": 2219},
            "single_sentence_para_ratio": 0.61,
            "paragraph_length": {
                "mean_chars": 41.8,
                "single_sentence_para_ratio_mean": 0.61,
            },
            "punctuation_density_per_1000": {
                "comma": {"mean": 63.0},
                "period": {"mean": 25.6},
            },
            "function_word_fingerprint_per_1000": {
                "的": {"mean": 49.2},
                "了": {"mean": 14.5},
            },
        }
    }


def test_apply_baseline_accepts_interval_scalars_from_multi_ref():
    profile = {
        "ultra_short_para_ratio": {"min": 0.12, "max": 0.25, "mean": 0.18},
        "single_sentence_para_ratio": {"min": 0.4, "max": 0.7, "mean": 0.55},
        "sentence_stats": {
            "mean": {"min": 18.0, "max": 30.0, "mean": 24.0},
            "std": {"min": 10.0, "max": 20.0, "mean": 15.0},
            "count": {"min": 20, "max": 30, "mean": 25},
        },
        "paragraph_count": {"min": 20, "max": 30, "mean": 25},
    }

    out = se._apply_baseline(profile, _baseline())

    assert out["ultra_short_para_ratio"] == {"min": 0.12, "max": 0.25, "mean": 0.18}
    assert out["single_sentence_para_ratio"] == 0.61
    assert out["sentence_stats"]["mean"] == 28.1
    assert out["sentence_stats"]["std"] == 16.2


def test_evaluate_multi_ref_with_structured_baseline_does_not_crash():
    refs = [
        "风从城门灌进来。老赵按住桌上的账本。‘这钱不能这么算。’" * 40,
        "雨落了一夜。陈谋把合同翻到最后一页。‘你先看看违约金。’" * 35,
    ]
    generated = "灯灭了。姚旭攥着刀退了半步。‘我去，这东西还会算账？’" * 38

    report = se.evaluate(refs, generated, baseline=_baseline())

    assert isinstance(report["sfs_quick"], (int, float))
    assert report["has_author_profile"] is True


def test_malformed_quantitative_is_ignored_instead_of_crashing():
    profile = {"ultra_short_para_ratio": {"min": 0.1, "max": 0.2, "mean": 0.15}}

    out = se._apply_baseline(profile, {"quantitative": ["invalid"]})

    assert out is profile
