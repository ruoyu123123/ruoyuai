"""D5 人设漂移曲线的 cluster-native 纯函数回归。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cross_cluster_persona_drift_aggregate as aggregate  # noqa: E402


def _points(*values: float) -> dict[str, list[dict]]:
    return {
        f"cluster_{index:03d}": [{"character": "甲", "drift": value}]
        for index, value in enumerate(values, start=1)
    }


def test_linreg_slope() -> None:
    assert aggregate._linreg_slope([1, 2, 3], [0.2, 0.4, 0.6]) == pytest.approx(0.2)
    assert aggregate._linreg_slope([1], [0.5]) == 0.0


def test_curve_uses_cluster_points_and_multiple_metrics() -> None:
    curve = aggregate.build_d5_curves(_points(0.2, 0.4, 0.6))["甲"]
    assert curve["n"] == 3
    assert curve["mean"] == 0.4
    assert curve["last"] == 0.6
    assert curve["slope"] == 0.2
    assert curve["volatility"] == 0.2
    assert [point["cluster_id"] for point in curve["points"]] == [
        "cluster_001", "cluster_002", "cluster_003"
    ]
    assert all("ch" not in point for point in curve["points"])


def test_findings_require_three_points_and_remain_advisory() -> None:
    assert aggregate.build_d5_findings(
        aggregate.build_d5_curves(_points(0.8, 0.8)),
        dict(aggregate._D5_GENERIC_BAND),
    ) == []
    findings = aggregate.build_d5_findings(
        aggregate.build_d5_curves(_points(0.6, 0.7, 0.8)),
        dict(aggregate._D5_GENERIC_BAND),
    )
    assert findings[0]["code"] == "PERSONA_DRIFT_CURVE_TREND"
    assert findings[0]["severity"] == "advisory"
    assert findings[0]["gate_level"] == "advisory"


def test_author_band_tightens_for_stable_behavior_loops(tmp_path: Path) -> None:
    database = tmp_path / "_数据库"
    database.mkdir()
    (database / "作者风格.json").write_text(json.dumps({
        "narrative_fingerprint": {
            "character_behavior_loops": {"观察到推理": 9}
        }
    }, ensure_ascii=False), encoding="utf-8")
    band = aggregate.compute_d5_author_band(tmp_path)
    assert band["_source"] == "作者档"
    assert band["warn_mean"] < aggregate._D5_GENERIC_BAND["warn_mean"]


def test_author_band_widens_for_complex_characters(tmp_path: Path) -> None:
    database = tmp_path / "_数据库"
    database.mkdir()
    (database / "作者风格.json").write_text(json.dumps({
        "narrative_fingerprint": {
            "character_behavior_loops": {"观察到推理": 1},
            "character_depth_grade_distribution": {"极高": 5, "其他": 5},
        }
    }, ensure_ascii=False), encoding="utf-8")
    band = aggregate.compute_d5_author_band(tmp_path)
    assert band["_source"] == "作者档"
    assert band["warn_mean"] > aggregate._D5_GENERIC_BAND["warn_mean"]


def test_new_code_is_not_hard_gate() -> None:
    import audit_hub

    assert "PERSONA_DRIFT_CURVE_TREND" not in audit_hub.HARD_GATE_CODES
