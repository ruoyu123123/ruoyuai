"""State Agent 与 cluster-save-state required 后置回执合同测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cluster_post_state_receipt as post_receipt  # noqa: E402
import plan_step_gates as gates  # noqa: E402
import plan_tracker  # noqa: E402
import run_cross_cluster_aggregates as cross_runner  # noqa: E402
import state_tracker_receipt  # noqa: E402


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _delta(cluster_id: str = "cluster_001") -> dict:
    return {
        "cluster_id": cluster_id,
        "time_advance": {},
        "location_changes": [],
        "hub_usage": [],
        "fate_events_triggered": [],
        "world_state_consumption": {
            "emergent_opportunities_consumed": [],
            "thread_responded": [],
        },
        "heart_events_revealed": [],
    }


def _state_receipt(project: Path, monkeypatch) -> tuple[Path, str]:
    plan_id = _post_plan(project, monkeypatch)
    delta_path = project / "_数据库" / ".wal" / "cluster_001_state_delta.json"
    _write_json(delta_path, _delta())
    receipt = state_tracker_receipt.build_receipt(
        project, "cluster_001", plan_id, "5",
    )
    return state_tracker_receipt.write_receipt(project, receipt), plan_id


def test_state_tracker_hook_contract_requires_receipt_paths():
    prompt = (
        "PLAN_ID: plan-1\nSTEP: 5\nPROJECT: novel\nCLUSTER_ID: cluster_001\n"
        "MODE: cluster\nCLUSTER_DRAFT_PATH: 章节/cluster_001_draft/cluster_001_draft.txt\n"
        "STATE_DELTA_PATH: _数据库/.wal/cluster_001_state_delta.json\n"
        "RECEIPT_PATH: _数据库/.wal/cluster_001_state_tracker_receipt.json\n"
        "TIMELINE_PATH: _数据库/时间线.json\nMAP_PATH: _数据库/地图.json\n"
        "HUBS_PATH: _数据库/枢纽场景.json\nWORLD_STATE_PATH: _数据库/世界状态.json\n"
        "GRAND_TREND_PATH: _数据库/大势卡.json\nENSEMBLE_PATH: _数据库/群像档.json\n"
        "RIPPLE_RULES_PATH: _数据库/涟漪规则.json\n读取整块正文并梳理当前状态增量。"
    )
    assert gates.check_agent_injection(
        prompt, "cluster-save-state", "novel-state-tracker", plan_state="ok",
    )["ok"]
    broken = prompt.replace(
        "RECEIPT_PATH: _数据库/.wal/cluster_001_state_tracker_receipt.json\n", "",
    )
    result = gates.check_agent_injection(
        broken, "cluster-save-state", "novel-state-tracker", plan_state="ok",
    )
    assert not result["ok"] and "RECEIPT_PATH" in result["msg"]
    no_step = prompt.replace("STEP: 5\n", "")
    result = gates.check_agent_injection(
        no_step, "cluster-save-state", "novel-state-tracker", plan_state="ok",
    )
    assert not result["ok"] and "STEP" in result["msg"]


def test_state_tracker_receipt_binds_plan_step_delta_and_digest(tmp_path: Path, monkeypatch):
    receipt_path, plan_id = _state_receipt(tmp_path, monkeypatch)
    assert plan_tracker._verify_state_tracker_receipt(
        receipt_path, project_root=tmp_path, plan_id=plan_id, step_n=5,
    )
    delta_path = tmp_path / "_数据库" / ".wal" / "cluster_001_state_delta.json"
    altered = _delta()
    altered["time_advance"] = {"elapsed": "一小时"}
    _write_json(delta_path, altered)
    assert not plan_tracker._verify_state_tracker_receipt(
        receipt_path, project_root=tmp_path, plan_id=plan_id, step_n=5,
    )


def test_state_tracker_receipt_rejects_delta_older_than_plan(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    delta_path = tmp_path / "_数据库" / ".wal" / "cluster_001_state_delta.json"
    _write_json(delta_path, _delta())
    os.utime(delta_path, (1, 1))
    with pytest.raises(ValueError, match="遗留产物"):
        state_tracker_receipt.build_receipt(
            tmp_path, "cluster_001", plan_id, "5",
        )


def test_plain_state_delta_cannot_impersonate_agent_receipt(tmp_path: Path):
    delta_path = tmp_path / "_数据库" / ".wal" / "cluster_001_state_delta.json"
    _write_json(delta_path, _delta())
    assert not plan_tracker._verify_state_tracker_receipt(
        delta_path, project_root=tmp_path, plan_id="plan-1", step_n=5,
    )


def test_writer_self_eval_receipt_rejects_objective_state_or_other_cluster(tmp_path: Path):
    path = tmp_path / "cluster_001_apply_cluster.json"
    receipt = {
        "cluster_id": "cluster_001",
        "source": "章节/cluster_001_draft/cluster_001_changes.json",
        "contract": "cluster_writer_self_eval",
        "objective_state_applied": False,
        "waiver_count": 0,
        "self_eval_fields": ["waivers"],
    }
    _write_json(path, receipt)
    assert plan_tracker._verify_writer_self_eval_receipt(
        path, cluster_id="cluster_001",
    )
    receipt["objective_state_applied"] = True
    _write_json(path, receipt)
    assert not plan_tracker._verify_writer_self_eval_receipt(
        path, cluster_id="cluster_001",
    )
    receipt["objective_state_applied"] = False
    _write_json(path, receipt)
    assert not plan_tracker._verify_writer_self_eval_receipt(
        path, cluster_id="cluster_002",
    )


def test_writer_truth_report_requires_current_cluster_pass(tmp_path: Path):
    path = tmp_path / "cluster_001_writer-truth-check.json"
    report = {
        "schema_version": "1.0.cluster",
        "judge_id": "writer-truth-check",
        "cluster_id": "cluster_001",
        "verdict": "pass",
        "lie_count": 0,
        "lies_detected": [],
        "specific_findings": {},
    }
    _write_json(path, report)
    assert plan_tracker._verify_writer_truth_report(
        path, cluster_id="cluster_001",
    )
    report["verdict"] = "fail"
    report["lie_count"] = 1
    report["lies_detected"] = [{"field": "anchors_hit"}]
    _write_json(path, report)
    assert not plan_tracker._verify_writer_truth_report(
        path, cluster_id="cluster_001",
    )


def test_foreshadow_state_receipt_requires_completed_current_cluster(tmp_path: Path):
    path = tmp_path / "cluster_001_foreshadow_state_receipt.json"
    receipt = {
        "schema_version": 1,
        "cluster_id": "cluster_001",
        "contract": "cluster_foreshadow_state",
        "completed": True,
        "brief_planned": 1,
        "brief_registered": 1,
        "payoff_checked": 0,
        "terminal_applied": 0,
        "progressive_recorded": 0,
        "rejection_count": 0,
        "sources": ["事件簇", "foreshadower"],
    }
    _write_json(path, receipt)
    assert plan_tracker._verify_foreshadow_state_receipt(
        path, cluster_id="cluster_001",
    )
    receipt["completed"] = False
    _write_json(path, receipt)
    assert not plan_tracker._verify_foreshadow_state_receipt(
        path, cluster_id="cluster_001",
    )


def test_step3_dynamic_receipts_must_be_newer_than_plan(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    plan = plan_tracker.get_plan(plan_id)
    step3 = next(step for step in plan["steps"] if step["n"] == 3)
    apply_path = tmp_path / "_数据库" / ".wal" / "cluster_001_apply_cluster.json"
    truth_path = (
        tmp_path / "_数据库" / ".judge_reports"
        / "cluster_001_writer-truth-check.json"
    )
    _write_json(apply_path, {
        "cluster_id": "cluster_001",
        "source": "章节/cluster_001_draft/cluster_001_changes.json",
        "contract": "cluster_writer_self_eval",
        "objective_state_applied": False,
        "waiver_count": 0,
        "self_eval_fields": ["waivers"],
    })
    _write_json(truth_path, {
        "schema_version": "1.0.cluster",
        "judge_id": "writer-truth-check",
        "cluster_id": "cluster_001",
        "verdict": "pass",
        "lie_count": 0,
        "lies_detected": [],
        "specific_findings": {},
    })
    verified, missing = plan_tracker._verify_outputs(plan, step3, str(tmp_path))
    assert len(verified) == 2 and missing == []

    os.utime(truth_path, (1, 1))
    verified, missing = plan_tracker._verify_outputs(plan, step3, str(tmp_path))
    assert str(truth_path).replace("\\", "/") in missing


def _post_outputs(project: Path, *, cluster_id: str = "cluster_001") -> None:
    db = project / "_数据库"
    _write_json(db / ".wal" / f"{cluster_id}_state_updates_receipt.json", {
        "schema_version": "cluster-state-updates.receipt.v1",
        "cluster_id": cluster_id,
        "completed": True,
        "modules": [
            {"name": "offscreen", "ok": True, "exit_code": 0},
            {"name": "character_arc", "ok": True, "exit_code": 0},
        ],
    })
    _write_json(db / ".wal" / f"{cluster_id}_state_evaluators_receipt.json", {
        "schema_version": "cluster-state-evaluators.receipt.v1",
        "cluster_id": cluster_id,
        "completed": True,
        "advisories": ["narrator"],
        "evaluators": [
            {"name": "clock", "ok": True, "exit_code": 0},
            {"name": "narrator", "ok": True, "exit_code": 1},
            {"name": "stress", "ok": True, "exit_code": 0},
            {"name": "relationship", "ok": True, "exit_code": 0},
        ],
    })
    tasks = [
        {"scanner": name, "status": "ok", "exit_code": 0}
        for name in cross_runner.SCANNERS
    ]
    _write_json(db / ".cross_cluster_scan" / "cross_cluster_wrapper_latest.json", {
        "schema_version": "cross_cluster_run.v1",
        "cluster_id": cluster_id,
        "required": True,
        "expected_count": len(tasks),
        "executed_count": len(tasks),
        "failed_count": 0,
        "advisory_count": 0,
        "tasks": tasks,
    })
    _write_json(db / ".world_evolution" / f"{cluster_id}_apply.json", {
        "cluster_id": cluster_id,
        "world_state_consumption": {},
        "fate_events": [],
        "tick": {},
        "applied_at": "2026-07-12T12:00:00",
    })
    _write_json(db / ".judge_reports" / f"{cluster_id}_consensus_decision.json", {
        "schema_version": "judge_consensus_decision.v1",
        "cluster_id": cluster_id,
        "required_substep_executed": True,
        "input_reports": [],
        "input_count": 0,
        "status": "not_required",
        "reason": "至少需要两份独立 Judge 报告",
    })
    _write_json(db / "knowledge_graph.json", {
        "schema_version": "v1", "nodes": [], "edges": [],
    })
    _write_json(db / "subplot_threads.json", {"threads": []})


def _post_plan(project: Path, monkeypatch) -> str:
    monkeypatch.setattr(plan_tracker, "PROJECTS_DIR", project.parent / "novels")
    monkeypatch.setattr(plan_tracker, "GLOBAL_PLANS_DIR", project.parent / "global-plans")
    monkeypatch.setattr(
        plan_tracker, "ATTEST_KEY_PATH",
        project.parent / "global-plans" / ".attest_key",
    )
    project.mkdir(exist_ok=True)
    return plan_tracker.create_plan("cluster-save-state", str(project), "1")


def test_post_state_receipt_validates_and_hashes_cluster_outputs(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    _post_outputs(tmp_path)
    receipt = post_receipt.build_receipt(tmp_path, "001", plan_id, "11")
    assert receipt["schema_version"] == "cluster-post-state.receipt.v1"
    assert receipt["cluster_id"] == "cluster_001"
    assert receipt["plan_id"] == plan_id and receipt["step"] == 11
    assert [row["role"] for row in receipt["artifacts"]] == [
        "state_updates", "state_evaluators", "cross_cluster",
        "world_evolution", "consensus_decision", "knowledge_graph",
        "subplot_threads",
    ]
    assert all(len(row["sha256"]) == 64 for row in receipt["artifacts"])


def test_plan_tracker_revalidates_post_state_receipt_contents(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    _post_outputs(tmp_path)
    receipt = post_receipt.build_receipt(tmp_path, "001", plan_id, "11")
    receipt_path = post_receipt.write_receipt(tmp_path, receipt)
    assert plan_tracker._verify_post_state_receipt(
        receipt_path, project_root=tmp_path, plan_id=plan_id,
        step_n=11, cluster_id="cluster_001",
    )
    receipt["plan_id"] = "stale-plan"
    _write_json(receipt_path, receipt)
    assert not plan_tracker._verify_post_state_receipt(
        receipt_path, project_root=tmp_path, plan_id=plan_id,
        step_n=11, cluster_id="cluster_001",
    )


def test_post_state_receipt_rejects_stale_other_cluster_wrapper(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    _post_outputs(tmp_path)
    wrapper = (
        tmp_path / "_数据库" / ".cross_cluster_scan"
        / "cross_cluster_wrapper_latest.json"
    )
    value = json.loads(wrapper.read_text(encoding="utf-8"))
    value["cluster_id"] = "cluster_002"
    _write_json(wrapper, value)
    with pytest.raises(ValueError, match="当前 cluster"):
        post_receipt.build_receipt(tmp_path, "001", plan_id, "11")


def test_post_state_receipt_rejects_failed_required_substep(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    _post_outputs(tmp_path)
    updates = tmp_path / "_数据库" / ".wal" / "cluster_001_state_updates_receipt.json"
    value = json.loads(updates.read_text(encoding="utf-8"))
    value["modules"][0]["ok"] = False
    value["modules"][0]["exit_code"] = 2
    _write_json(updates, value)
    with pytest.raises(ValueError, match="失败模块"):
        post_receipt.build_receipt(tmp_path, "001", plan_id, "11")


def test_post_state_receipt_rejects_artifact_older_than_plan(tmp_path: Path, monkeypatch):
    plan_id = _post_plan(tmp_path, monkeypatch)
    _post_outputs(tmp_path)
    wrapper = (
        tmp_path / "_数据库" / ".cross_cluster_scan"
        / "cross_cluster_wrapper_latest.json"
    )
    os.utime(wrapper, (1, 1))
    with pytest.raises(ValueError, match="遗留产物"):
        post_receipt.build_receipt(tmp_path, "001", plan_id, "11")


def test_cluster_save_state_plan_wires_required_receipts():
    plan = json.loads((
        ROOT / "core" / "claude-home" / "plans" / "cluster-save-state.plan.json"
    ).read_text(encoding="utf-8"))
    step5 = next(step for step in plan["steps"] if step["n"] == 5)
    assert step5["judge_report_path"]["novel-state-tracker"].endswith(
        "_state_tracker_receipt.json"
    )
    assert step5["judge_report_path"]["novel-state-tracker"] in step5["expected_outputs"]
    assert "_数据库/.wal/cluster_{key}_state_delta.json" in step5["expected_outputs"]

    step3 = next(step for step in plan["steps"] if step["n"] == 3)
    assert step3["scripts"][-1].endswith(
        "writer_truth_check.py {project_root} --cluster {key}"
    )
    assert (
        "_数据库/.judge_reports/cluster_{key}_writer-truth-check.json"
        in step3["expected_outputs"]
    )

    step10 = next(step for step in plan["steps"] if step["n"] == 10)
    assert (
        "python core/scripts/save_state.py {project_root} "
        "--apply-foreshadow-state {key}"
    ) in step10["scripts"]
    assert (
        "_数据库/.wal/cluster_{key}_foreshadow_state_receipt.json"
        in step10["expected_outputs"]
    )

    step11 = next(step for step in plan["steps"] if step["n"] == 11)
    scripts = "\n".join(step11["scripts"])
    assert "--tier" not in scripts and "--all" not in scripts
    assert scripts.rstrip().endswith(
        "cluster_post_state_receipt.py {project_root} --cluster {key} "
        "--plan-id {plan_id} --step 11"
    )
    expected = set(step11["expected_outputs"])
    assert {
        "_数据库/knowledge_graph.json",
        "_数据库/subplot_threads.json",
        "_数据库/.wal/cluster_{key}_state_updates_receipt.json",
        "_数据库/.wal/cluster_{key}_state_evaluators_receipt.json",
        "_数据库/.cross_cluster_scan/cross_cluster_wrapper_latest.json",
        "_数据库/.world_evolution/cluster_{key}_apply.json",
        "_数据库/.judge_reports/cluster_{key}_consensus_decision.json",
        "_数据库/.wal/cluster_{key}_post_state_receipt.json",
    } <= expected


def test_create_plan_substitutes_plan_id_into_agent_and_post_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_tracker, "PROJECTS_DIR", tmp_path / "novels")
    monkeypatch.setattr(plan_tracker, "GLOBAL_PLANS_DIR", tmp_path / "global-plans")
    monkeypatch.setattr(plan_tracker, "ATTEST_KEY_PATH", tmp_path / "global-plans" / ".attest_key")
    project = tmp_path / "novel"
    project.mkdir()
    plan_id = plan_tracker.create_plan("cluster-save-state", str(project), "1")
    plan = plan_tracker.get_plan(plan_id)
    step5 = next(step for step in plan["steps"] if step["n"] == 5)
    step11 = next(step for step in plan["steps"] if step["n"] == 11)
    assert step5["agent_input"]["PLAN_ID"] == plan_id
    assert f"--plan-id {plan_id} --step 11" in step11["scripts"][-1]
