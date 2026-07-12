"""Cluster-native learning-loop contracts and deterministic behavior."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import learning_loop as loop  # noqa: E402
import learning_loop_store as store  # noqa: E402

TARGET = SCRIPTS / "learning_loop.py"


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "_数据库").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _waiver_audit(**overrides) -> dict:
    result = {
        "waive_rate": 0.0,
        "advisory_total": 0,
        "advisory_waived": 0,
        "blanket_suspected": False,
        "orphan_codes": [],
        "repeated_reason_codes": {},
    }
    result.update(overrides)
    return result


def _audit(
    cluster_id: str,
    code: str | None = "PLOT_X",
    *,
    dimension: str = "结构",
    severity: str = "error",
    waived: bool = False,
    meta_suspect: bool = False,
) -> dict:
    issues = []
    waived_issues = []
    if code is not None:
        issue = {
            "code": code,
            "dimension": dimension,
            "severity": severity,
            "desc": f"{code} in {cluster_id}",
            "waived": waived,
        }
        if meta_suspect:
            issue["meta_suspect"] = True
        issues.append(issue)
        if waived:
            waived_issues.append({
                "code": code,
                "dimension": dimension,
                "waive_reason": f"{cluster_id} 场景需要",
            })
    return {
        "cluster_id": cluster_id,
        "issues": issues,
        "pending_agent": [],
        "waived_issues": waived_issues,
        "waiver_audit": _waiver_audit(
            advisory_total=len(issues), advisory_waived=len(waived_issues)
        ),
    }


def _write_audit(project: Path, audit: dict) -> Path:
    audit_dir = project / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"{audit['cluster_id']}_audit.json"
    path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return path


def _ingest(project: Path, audit: dict) -> dict:
    return loop.ingest_audit(project, _write_audit(project, audit))


def _run_cli(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TARGET), str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )


def test_route_entry_and_deduplication():
    experience = loop._empty_experience()
    assert loop._route_entry(experience, {"category": "success", "id": "s1"}) == "success"
    assert loop._route_entry(experience, {"category": "failure", "id": "f1"}) == "failure"
    assert loop._route_entry(experience, {"category": "other", "id": "x"}) == "skip"
    loop._route_entry(experience, {"category": "failure", "id": "f1", "trigger": "new"})
    assert len(experience["failure_patterns"]) == 1
    assert experience["failure_patterns"][0]["trigger"] == "new"
    assert "updated_at" in experience["success_patterns"][0]


def test_merge_reflection_writes_source_clusters(tmp_path):
    project = _make_project(tmp_path)
    reflection = {
        "cluster_id": "cluster_007",
        "entries": [
            {
                "id": "s1",
                "category": "success",
                "source_cluster": "cluster_007",
                "trigger": "强钩子",
            },
            {
                "id": "f1",
                "category": "failure",
                "source_cluster": "cluster_007",
                "trigger": "节奏松散",
            },
        ],
        "note": "完成",
    }
    path = tmp_path / "cluster_007_reflection.json"
    path.write_text(json.dumps(reflection, ensure_ascii=False), encoding="utf-8")
    result = loop.merge_reflection(project, path)
    assert result["cluster_id"] == "cluster_007"
    experience = loop.load_experience(project)
    assert experience["success_patterns"][0]["source_clusters"] == ["cluster_007"]
    assert "source_cluster" not in experience["success_patterns"][0]


def test_merge_reflection_rejects_mismatched_source(tmp_path):
    project = _make_project(tmp_path)
    path = tmp_path / "reflection.json"
    path.write_text(json.dumps({
        "cluster_id": "cluster_002",
        "entries": [{
            "id": "x",
            "category": "success",
            "source_cluster": "cluster_001",
        }],
    }), encoding="utf-8")
    with pytest.raises(store.ExperienceContractError):
        loop.merge_reflection(project, path)


@pytest.mark.parametrize("payload", [
    {"entries": [], "success_patterns": [], "failure_patterns": [], "preferences": []},
    {
        "success_patterns": [{"id": "x", "source_" + "chapters": [1]}],
        "failure_patterns": [],
        "preferences": [],
    },
    {
        "success_patterns": [],
        "failure_patterns": [],
        "preferences": [],
        "_efficacy_tracker": {"x": {"baseline_" + "chapters": [1, 2]}},
    },
])
def test_load_experience_rejects_non_cluster_schema(tmp_path, payload):
    project = _make_project(tmp_path)
    (project / "_数据库" / "写作经验.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(store.ExperienceContractError):
        loop.load_experience(project)


def test_issue_key_uses_dimension_and_code():
    assert loop._issue_key({"dimension": "结构", "code": "PLOT_X"}) == "结构::PLOT_X"
    assert loop._issue_key({"check": "style"}) == "unknown::style"


def test_consecutive_cluster_detection_and_recur_rate():
    assert loop._has_consecutive_clusters(["cluster_001", "cluster_002"], 2)
    assert not loop._has_consecutive_clusters(["cluster_001", "cluster_003"], 2)
    assert loop._has_consecutive_clusters(["cluster_004", "cluster_005", "cluster_006"], 3)
    assert loop._recur_rate(2, ["cluster_001", "cluster_002"]) == 1.0
    assert loop._recur_rate(0, []) == 0.0


def test_ingest_escalates_after_two_consecutive_clusters(tmp_path):
    project = _make_project(tmp_path)
    assert _ingest(project, _audit("cluster_001"))["escalated"] == []
    result = _ingest(project, _audit("cluster_002"))
    assert len(result["escalated"]) == 1
    pattern = result["escalated"][0]
    assert pattern["id"] == "recur_结构_PLOT_X"
    assert pattern["confidence"] == 0.95
    assert pattern["source_clusters"] == ["cluster_001", "cluster_002"]
    assert "下一 cluster" in pattern["why_works"]


def test_ingest_nonconsecutive_clusters_use_high_frequency_warning(tmp_path):
    project = _make_project(tmp_path)
    _ingest(project, _audit("cluster_001"))
    result = _ingest(project, _audit("cluster_003"))
    assert result["escalated"][0]["confidence"] == 0.8
    assert result["escalated"][0]["severity"] == "高频警示"


def test_ingest_filters_info_and_counts_duplicate_code_once(tmp_path):
    project = _make_project(tmp_path)
    _ingest(project, _audit("cluster_001", severity="info"))
    duplicate = _audit("cluster_002")
    duplicate["issues"].append(dict(duplicate["issues"][0]))
    _ingest(project, duplicate)
    tracker = loop.load_experience(project)["_recurrence_tracker"]
    assert tracker["结构::PLOT_X"]["clusters"] == ["cluster_002"]
    assert tracker["结构::PLOT_X"]["count"] == 1


def test_waived_issue_enters_calibration_not_recurrence(tmp_path):
    project = _make_project(tmp_path)
    for index in range(1, 4):
        result = _ingest(project, _audit(f"cluster_{index:03d}", "STYLE_对话占比", waived=True))
    experience = loop.load_experience(project)
    assert experience["_recurrence_tracker"] == {}
    assert experience["failure_patterns"] == []
    assert experience["_waiver_tracker"]["STYLE_对话占比"]["clusters"] == [
        "cluster_001", "cluster_002", "cluster_003"
    ]
    assert result["calibration"][0]["clusters"] == [
        "cluster_001", "cluster_002", "cluster_003"
    ]


def test_cluster_scene_type_drives_adaptation_suggestion(tmp_path):
    project = _make_project(tmp_path)
    blueprint = {
        f"cluster_{index:03d}": {"scene_storyboard": [{"scene_type": ["悬疑"]}]}
        for index in range(1, 4)
    }
    (project / "_数据库" / "进度.json").write_text(
        json.dumps({"cluster_blueprint": blueprint}, ensure_ascii=False), encoding="utf-8"
    )
    for index in range(1, 4):
        result = _ingest(project, _audit(f"cluster_{index:03d}", "STYLE_X", waived=True))
    assert result["calibration"][0]["suggestion_type"] == "add_scene_adaptation"
    assert result["calibration"][0]["scene_type_hint"] == "悬疑"


def test_ingest_requires_complete_cluster_audit(tmp_path):
    project = _make_project(tmp_path)
    path = project / "_数据库" / ".audit" / "cluster_001_audit.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"cluster_id": "cluster_001", "issues": []}), encoding="utf-8")
    with pytest.raises(store.ExperienceContractError):
        loop.ingest_audit(project, path)


def test_scan_recurring_uses_only_cluster_audits(tmp_path):
    project = _make_project(tmp_path)
    audit_dir = project / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "ch_001_audit.json").write_text(json.dumps({"chapter": 1}), encoding="utf-8")
    result = loop.scan_recurring(project)
    assert result["escalated"] == []
    assert loop.load_experience(project)["_observed_clusters"] == []


def test_scan_recurring_rejects_noncanonical_audit_filename(tmp_path):
    project = _make_project(tmp_path)
    audit_dir = project / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "cluster_1_audit.json").write_text(
        json.dumps(_audit("cluster_001"), ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(store.ExperienceContractError):
        loop.scan_recurring(project)


def test_audit_hub_does_not_bypass_required_learning_step():
    source = (SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "_feed_learning_loop" not in source
    plan = json.loads((ROOT / "core" / "claude-home" / "plans" /
                       "cluster-save-state.plan.json").read_text(encoding="utf-8"))
    step = next(item for item in plan["steps"] if item["n"] == 10)
    assert any("--auto-post-reflect-cluster" in command for command in step["scripts"])
    assert step["required"] is True and step["skip_output_allowed"] is False


def test_scan_recurring_meta_candidate_and_data_gap_filter(tmp_path):
    project = _make_project(tmp_path)
    for index in range(1, 4):
        audit = _audit(f"cluster_{index:03d}", "PACING_BUG", severity="warning")
        audit["issues"].append({
            "code": "FORESHADOWING_MISSING",
            "dimension": "伏笔",
            "severity": "error",
            "desc": "伏笔 MISSING",
            "waived": False,
        })
        _write_audit(project, audit)
    result = loop.scan_recurring(project)
    candidates = [item["key"] for item in result["meta_problems"] if item["confidence"] == "candidate"]
    assert any("PACING_BUG" in key for key in candidates)
    assert all("MISSING" not in key for key in candidates)


def test_scan_recurring_meta_suspect_is_high_confidence(tmp_path):
    project = _make_project(tmp_path)
    _write_audit(project, _audit("cluster_001", "STYLE_BUG", meta_suspect=True))
    result = loop.scan_recurring(project)
    assert any(item["confidence"] == "high" for item in result["meta_problems"])


def test_scan_recurring_prunes_stale_efficacy_state(tmp_path):
    project = _make_project(tmp_path)
    experience = loop._empty_experience()
    experience["failure_patterns"] = [{
        "id": "recur_结构_OLD",
        "category": "failure",
        "source_clusters": ["cluster_001", "cluster_002"],
    }]
    experience["_efficacy_tracker"] = {
        "recur_结构_OLD": {
            "key": "结构::OLD",
            "baseline_clusters": ["cluster_001", "cluster_002"],
            "status": "monitoring",
        }
    }
    loop.save_experience(project, experience)
    loop.scan_recurring(project)
    current = loop.load_experience(project)
    assert current["failure_patterns"] == []
    assert current["_efficacy_tracker"] == {}


def test_prune_and_decay_current_patterns():
    experience = loop._empty_experience()
    expired = (datetime.now() - timedelta(days=loop.EXPIRY_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    decaying = (datetime.now() - timedelta(days=loop.DECAY_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    experience["failure_patterns"].append({"id": "old", "confidence": 0.9, "updated_at": expired})
    experience["success_patterns"].append({"id": "mid", "confidence": 1.0, "updated_at": decaying})
    result = loop._prune_and_decay(experience)
    assert experience["failure_patterns"] == []
    assert experience["success_patterns"][0]["confidence"] == 0.8
    assert result["pruned"][0]["id"] == "old"


def test_cli_exit_statuses(tmp_path):
    project = _make_project(tmp_path)
    _write_audit(project, _audit("cluster_001"))
    assert _run_cli(project, "--scan-recurring").returncode == 0
    _write_audit(project, _audit("cluster_002"))
    assert _run_cli(project, "--scan-recurring").returncode == 1
    assert _run_cli(project, "--ingest", "missing.json").returncode == 2
    assert _run_cli(project).returncode == 2


def test_cli_no_arguments_prints_usage():
    result = subprocess.run(
        [sys.executable, str(TARGET)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )
    assert result.returncode == 0
    assert "--scan-recurring" in result.stdout
