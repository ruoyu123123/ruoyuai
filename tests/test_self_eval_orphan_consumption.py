"""writer 压力自评字段的确定性消费合同。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stress_evaluator as module  # noqa: E402


TRAITS = [{
    "trait": "护短",
    "violation_keywords": ["背叛"],
    "align_keywords": ["保护"],
    "stress_per_violation": 2,
}]


def test_violations_use_trait_weight_and_cap():
    result = module.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["a", "b", "c", "d"]}, TRAITS
    )
    assert result["source"] == "writer_declared"
    assert result["delta"] == 6
    assert result["violations"][0]["count"] == 4


def test_alignments_provide_relief():
    result = module.evaluate_stress_delta_from_self_eval(
        {"alignments_made": ["保护弱者", "守住承诺"]}, TRAITS
    )
    assert result["delta"] == -1
    assert result["alignments"][0]["count"] == 2


@pytest.mark.parametrize(("estimate", "expected"), [("+7", 7), ("-3", -3), ("0", 0)])
def test_explicit_estimate_is_authoritative(estimate, expected):
    result = module.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["背叛"], "estimated_stress_change": estimate}, TRAITS
    )
    assert result["delta"] == expected


def test_no_traits_use_default_weight():
    result = module.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["x"]}, []
    )
    assert result["delta"] == 2


def test_empty_declaration_delegates_to_prose_scan():
    assert module.evaluate_stress_delta_from_self_eval({}, TRAITS) is None
    assert module.evaluate_stress_delta_from_self_eval(None, TRAITS) is None


def test_malformed_declaration_fails_contract():
    with pytest.raises(module.StressContractError):
        module.evaluate_stress_delta_from_self_eval(
            {"estimated_stress_change": "about three"}, TRAITS
        )
