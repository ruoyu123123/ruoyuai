"""cluster 叙事节拍校准器的唯一合同回归。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import narrator_calibrate as nc  # noqa: E402


def _pacer() -> dict:
    return {
        "_schema": "cluster_storyteller",
        "schema_version": "1.0",
        "rhythm_profile": "standard",
        "beat_targets": [
            {"cluster_id": "cluster_001", "target": "first pressure beat"},
        ],
        "default_beat_policy": {"mode": "fluid"},
        "appraisal_beats": [],
        "storyteller_profile": "cassandra",
        "current_pressure_phase": "rising",
        "since_phase_change_cluster": "cluster_001",
        "cluster_outcome_log": [],
        "adaptation_factor": {
            "recent_n_clusters": 5,
            "expected_setback_per_n_clusters": 1,
            "tolerance_window": 0,
            "current_setback_count_in_window": 0,
            "current_win_streak": 0,
            "current_loss_streak": 0,
        },
        "narrator_recommendation": {
            "next_cluster_target_outcome": "auto",
            "next_cluster_intensity_target": "auto",
        },
    }


def _write_project(
    root: Path,
    *,
    cluster_id: str = "cluster_001",
    pacer: dict | None = None,
    draft: str = "他在雨里失败，又一次暴露了退路。",
    alignment: dict | None = None,
) -> None:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "叙事节拍器.json").write_text(
        json.dumps(pacer or _pacer(), ensure_ascii=False), encoding="utf-8"
    )
    draft_dir = root / "章节" / f"{cluster_id}_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    self_eval = {}
    if alignment is not None:
        self_eval["storyteller_alignment"] = alignment
    (draft_dir / f"{cluster_id}_changes.json").write_text(
        json.dumps({"self_eval": self_eval}, ensure_ascii=False), encoding="utf-8"
    )
    (draft_dir / f"{cluster_id}_draft.txt").write_text(draft, encoding="utf-8")


def _run_cli(root: Path, cluster_id: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RUOYUAI_CLUSTER_STATE_INTERNAL"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "narrator_calibrate.py"), str(root),
         "--cluster", cluster_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_validate_pacer_accepts_cluster_schema():
    assert nc.validate_pacer(_pacer())["_schema"] == "cluster_storyteller"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"schema_version": "0.9"}),
        lambda value: value.update({"chapter_outcome_log": []}),
        lambda value: value["beat_targets"].append({"ch": 2}),
        lambda value: value.update({"since_phase_change_cluster": "chapter_001"}),
    ],
)
def test_validate_pacer_rejects_noncanonical_shapes(mutate):
    value = _pacer()
    mutate(value)
    with pytest.raises(nc.NarratorContractError):
        nc.validate_pacer(value)


def test_narrator_view_selects_current_cluster_beats():
    view = nc.narrator_view(_pacer(), "cluster_001")
    assert view["current_phase"] == "rising"
    assert view["current_cluster_beats"] == [
        {"cluster_id": "cluster_001", "target": "first pressure beat"}
    ]


def test_infer_outcome_prefers_writer_declaration():
    changes = {
        "self_eval": {"storyteller_alignment": {"actual_outcome": "win"}},
    }
    outcome, intensity, source = nc.infer_outcome(changes, "失败 失败 暴露")
    assert (outcome, source) == ("win", "writer_declared")
    assert 0 <= intensity <= 8


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        ("失败后再次暴露，众人败退。", "setback"),
        ("他成功突破封锁，救下同伴。", "win"),
        ("雨落在空街上。", "neutral"),
    ],
)
def test_infer_outcome_uses_whole_cluster_prose(draft, expected):
    outcome, _, source = nc.infer_outcome({"self_eval": {}}, draft)
    assert outcome == expected
    assert source == "prose_inference"


def test_evaluate_phase_uses_cluster_sequence():
    log = [
        {"cluster_id": f"cluster_{number:03d}", "outcome": "win", "intensity": 4,
         "outcome_source": "prose_inference"}
        for number in range(1, 4)
    ]
    assert nc.evaluate_phase(
        log, "rising", "cluster_001", "cluster_003"
    ) == ("climax", "cluster_003")


def test_calibrate_is_idempotent_and_recommends_setback(tmp_path):
    _write_project(tmp_path, draft="雨落在空街上。")
    first = nc.calibrate(tmp_path, "cluster_001")
    second = nc.calibrate(tmp_path, "cluster_001")
    saved = json.loads(
        (tmp_path / "_数据库" / "叙事节拍器.json").read_text(encoding="utf-8")
    )
    assert first["next_recommendation"]["next_cluster_target_outcome"] == "setback"
    assert second["outcome"] == "neutral"
    assert len(saved["cluster_outcome_log"]) == 1
    assert saved["cluster_outcome_log"][0]["cluster_id"] == "cluster_001"


def test_calibrate_recommends_win_when_setbacks_exceed_window(tmp_path):
    pacer = _pacer()
    pacer["cluster_outcome_log"] = [
        {"cluster_id": f"cluster_{number:03d}", "outcome": "setback", "intensity": 4,
         "outcome_source": "prose_inference"}
        for number in (1, 2)
    ]
    _write_project(tmp_path, cluster_id="cluster_003", pacer=pacer, draft="雨落在空街上。")
    result = nc.calibrate(tmp_path, "cluster_003")
    assert result["next_recommendation"]["next_cluster_target_outcome"] == "win"
    assert result["_urgent"] is True


def test_calibrate_rejects_missing_artifact(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "叙事节拍器.json").write_text(
        json.dumps(_pacer(), ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(nc.NarratorContractError, match="文件不存在"):
        nc.calibrate(tmp_path, "cluster_001")


def test_cli_returns_advisory_and_contract_exit_codes(tmp_path):
    _write_project(tmp_path, draft="雨落在空街上。")
    advisory = _run_cli(tmp_path, "cluster_001")
    assert advisory.returncode == 1
    assert json.loads(advisory.stdout)["cluster_id"] == "cluster_001"

    missing = _run_cli(tmp_path, "cluster_002")
    assert missing.returncode == 2
    assert "[FATAL]" in missing.stderr


def test_cli_requires_internal_state_pipeline(tmp_path):
    _write_project(tmp_path)
    env = os.environ.copy()
    env.pop("RUOYUAI_CLUSTER_STATE_INTERNAL", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "narrator_calibrate.py"), str(tmp_path),
         "--cluster", "cluster_001"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert result.returncode == 2
    assert "内部" in result.stderr
