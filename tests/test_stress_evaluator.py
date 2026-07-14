"""cluster 主角压力评估器的唯一合同回归。"""
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

import stress_evaluator as se  # noqa: E402
import build_manifest as bm  # noqa: E402


TRAIT = {
    "trait": "守信",
    "violation_keywords": ["背叛"],
    "align_keywords": ["守诺"],
    "stress_per_violation": 2,
}


def _card(card_id: str = "MB_doubt", *, weight: int = 1) -> dict:
    return {
        "card_id": card_id,
        "label": "信念动摇",
        "trigger_min_stress": 8,
        "weight": weight,
        "permanent_persona_changes": ["开始怀疑盟友"],
        "narrative_effect": "后续决策更谨慎",
    }


def _stress(*, level: int = 0, pool: list[dict] | None = None) -> dict:
    return {
        "_schema": "cluster_protagonist_stress",
        "schema_version": "1.0",
        "protagonist": "林默",
        "stress_level": level,
        "stress_max": 10,
        "stress_threshold_break": 8,
        "stress_log": [],
        "persona_violations_tracked": {"core_traits": [dict(TRAIT)]},
        "mental_break_pool": list(pool or []),
        "coping_mechanisms": {},
    }


def _write_project(
    root: Path,
    *,
    stress: dict | None = None,
    cluster_id: str = "cluster_001",
    draft: str = "他背叛了约定。",
    stress_self=None,
) -> None:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "主角压力档.json").write_text(
        json.dumps(stress or _stress(), ensure_ascii=False), encoding="utf-8"
    )
    (db / "事件表.json").write_text("{\"events\": []}", encoding="utf-8")
    draft_dir = root / "章节" / f"{cluster_id}_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    self_eval = {}
    if stress_self is not None:
        self_eval["stress_evaluation_self"] = stress_self
    (draft_dir / f"{cluster_id}_changes.json").write_text(
        json.dumps({"self_eval": self_eval}, ensure_ascii=False), encoding="utf-8"
    )
    (draft_dir / f"{cluster_id}_draft.txt").write_text(draft, encoding="utf-8")


def _run_cli(root: Path, cluster_id: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RUOYUAI_CLUSTER_STATE_INTERNAL"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "stress_evaluator.py"), str(root),
         "--cluster", cluster_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def test_validate_stress_accepts_cluster_schema():
    assert se.validate_stress(_stress())["stress_level"] == 0
    assert se.stress_view(_stress())["mode"] == "cluster"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"stress_dimensions": {"fear": 4}}),
        lambda value: value.update({"schema_version": "0.9"}),
        lambda value: value["mental_break_pool"].append(_card(weight=0)),
        lambda value: value["stress_log"].append({"ch": 1, "trigger_type": "neutral"}),
    ],
)
def test_validate_stress_rejects_noncanonical_shapes(mutate):
    value = _stress()
    mutate(value)
    with pytest.raises(se.StressContractError):
        se.validate_stress(value)


def test_self_eval_delta_is_authoritative():
    result = se.evaluate_stress_delta_from_self_eval(
        {
            "violations_made": ["背弃承诺"],
            "alignments_made": [],
            "estimated_stress_change": "+5",
        },
        [TRAIT],
    )
    assert result["delta"] == 5
    assert result["source"] == "writer_declared"


def test_self_eval_delta_rejects_malformed_estimate():
    with pytest.raises(se.StressContractError, match="带符号整数"):
        se.evaluate_stress_delta_from_self_eval(
            {"violations_made": [], "alignments_made": [],
             "estimated_stress_change": "about five"},
            [TRAIT],
        )


def test_keyword_delta_uses_whole_cluster_prose():
    result = se.evaluate_stress_delta("背叛。背叛。背叛。背叛。", [TRAIT])
    assert result["delta"] == 6
    assert result["source"] == "keyword_scan"


def test_mental_break_draw_is_cluster_deterministic():
    pool = [_card("A"), _card("B", weight=3)]
    draws = [se.draw_mental_break_card(pool, 8, "cluster_001") for _ in range(10)]
    assert all(item == draws[0] for item in draws)
    assert se.draw_mental_break_card(pool, 7, "cluster_001") is None


def test_apply_card_to_event_table_is_idempotent(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "事件表.json").write_text("{\"events\": []}", encoding="utf-8")
    card = _card()
    first = se.apply_card_to_event_table(tmp_path, "cluster_001", card, "林默")
    second = se.apply_card_to_event_table(tmp_path, "cluster_001", card, "林默")
    table = json.loads((db / "事件表.json").read_text(encoding="utf-8"))
    assert first == second
    assert len(table["events"]) == 1
    assert table["events"][0]["cluster_id"] == "cluster_001"


def test_manifest_reads_mental_break_card_from_cluster_log(tmp_path):
    stress = _stress()
    stress["stress_log"] = [{
        "cluster_id": "cluster_001",
        "stress_old": 6,
        "change": 2,
        "new_total": 0,
        "trigger_type": "persona_violation",
        "delta_source": "keyword_scan",
        "violations": [],
        "alignments": [],
        "mental_break_card": "MB_doubt",
    }]
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "主角压力档.json").write_text(
        json.dumps(stress, ensure_ascii=False), encoding="utf-8"
    )
    scanner = type("Scanner", (), {"root": tmp_path})()
    view = bm._collect_protagonist_stress(scanner, 1)
    assert view["last_mental_break"] == {
        "cluster_id": "cluster_001",
        "card_id": "MB_doubt",
    }


def test_evaluate_updates_cluster_log_idempotently(tmp_path):
    _write_project(tmp_path)
    first = se.evaluate(tmp_path, "cluster_001")
    second = se.evaluate(tmp_path, "cluster_001")
    saved = json.loads(
        (tmp_path / "_数据库" / "主角压力档.json").read_text(encoding="utf-8")
    )
    assert first["stress_delta"] == 2
    assert second["stress_old"] == 0
    assert second["stress_new"] == 2
    assert len(saved["stress_log"]) == 1
    assert saved["stress_log"][0]["cluster_id"] == "cluster_001"


def test_evaluate_uses_self_eval_before_keyword_scan(tmp_path):
    _write_project(
        tmp_path,
        draft="背叛。背叛。背叛。",
        stress_self={
            "violations_made": [],
            "alignments_made": ["守诺", "守诺"],
            "estimated_stress_change": "-1",
        },
        stress=_stress(level=3),
    )
    result = se.evaluate(tmp_path, "cluster_001")
    assert result["delta_source"] == "writer_declared"
    assert result["stress_new"] == 2


def test_evaluate_records_mental_break_and_resets_stress(tmp_path):
    _write_project(tmp_path, stress=_stress(level=6, pool=[_card()]))
    result = se.evaluate(tmp_path, "cluster_001")
    table = json.loads(
        (tmp_path / "_数据库" / "事件表.json").read_text(encoding="utf-8")
    )
    assert result["mental_break_triggered"] is True
    assert result["stress_new"] == 0
    assert table["events"][0]["type"] == "mental_break_triggered"


def test_evaluate_rejects_missing_cluster_artifacts(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "主角压力档.json").write_text(
        json.dumps(_stress(), ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(se.StressContractError, match="文件不存在"):
        se.evaluate(tmp_path, "cluster_001")


def test_cli_returns_advisory_and_contract_exit_codes(tmp_path):
    _write_project(tmp_path, stress=_stress(level=4))
    advisory = _run_cli(tmp_path, "cluster_001")
    assert advisory.returncode == 1
    assert json.loads(advisory.stdout)["high_stress_warning"] is True

    missing = _run_cli(tmp_path, "cluster_002")
    assert missing.returncode == 2
    assert "[FATAL]" in missing.stderr


def test_cli_requires_internal_state_pipeline(tmp_path):
    _write_project(tmp_path)
    env = os.environ.copy()
    env.pop("RUOYUAI_CLUSTER_STATE_INTERNAL", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "stress_evaluator.py"), str(tmp_path),
         "--cluster", "cluster_001"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert result.returncode == 2
    assert "内部" in result.stderr


def test_unignited_skeleton_skips_instead_of_fatal(tmp_path):
    """回归锁：scaffold 裸骨架（protagonist 空+log 空+pool 空）→ 跳过评估不 FATAL。
    真机《衔石与朝云》save-state step11 曾因此 exit 2（31 子系统裸骨架=合法 fluid）。"""
    import json as _json
    db = tmp_path / "_数据库"
    db.mkdir(parents=True)
    (db / "主角压力档.json").write_text(_json.dumps({
        "_schema": "cluster_protagonist_stress", "schema_version": "1.0",
        "_doc": "x", "consumption": {"layer": "direct_inject", "by": [], "status": "live"},
        "protagonist": "", "stress_level": 0, "stress_max": 100,
        "stress_threshold_break": 80, "stress_log": [],
        "persona_violations_tracked": [], "mental_break_pool": [],
        "coping_mechanisms": {},
    }, ensure_ascii=False), encoding="utf-8")
    import stress_evaluator as se
    result = se.evaluate(tmp_path, "cluster_001")
    assert result["skipped"] is True
    assert result["mental_break_triggered"] is False


def test_consumption_metadata_field_allowed_in_stress_card():
    """回归锁：scaffold 统一的 consumption 消费方声明块不算 unknown 字段。"""
    import stress_evaluator as se
    doc = {
        "_schema": "cluster_protagonist_stress", "schema_version": "1.0",
        "consumption": {"layer": "direct_inject", "by": [], "status": "live"},
        "protagonist": "女娃", "stress_level": 0, "stress_max": 100,
        "stress_threshold_break": 80, "stress_log": [],
        "persona_violations_tracked": {"core_traits": []}, "mental_break_pool": [],
        "coping_mechanisms": {},
    }
    assert se.validate_stress(doc)
