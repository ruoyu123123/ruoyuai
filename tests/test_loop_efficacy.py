"""Cluster-level efficacy tracking for learned writer constraints."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_manifest as manifest  # noqa: E402
import learning_loop as loop  # noqa: E402


def _audit(cluster_id: str, code: str) -> dict:
    return {
        "cluster_id": cluster_id,
        "issues": [{
            "code": code,
            "dimension": "结构",
            "severity": "error",
            "desc": f"{code} in {cluster_id}",
            "waived": False,
        }],
        "pending_agent": [],
        "waived_issues": [],
        "waiver_audit": {
            "waive_rate": 0.0,
            "advisory_total": 1,
            "advisory_waived": 0,
            "blanket_suspected": False,
            "orphan_codes": [],
            "repeated_reason_codes": {},
        },
    }


def _write_audits(project: Path, audits: list[dict]) -> None:
    audit_dir = project / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for audit in audits:
        (audit_dir / f"{audit['cluster_id']}_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False), encoding="utf-8"
        )


def _ingest(project: Path, audit: dict) -> dict:
    _write_audits(project, [audit])
    path = project / "_数据库" / ".audit" / f"{audit['cluster_id']}_audit.json"
    return loop.ingest_audit(project, path)


def _pattern(experience: dict, pattern_id: str):
    return next(
        (item for item in experience["failure_patterns"] if item.get("id") == pattern_id),
        None,
    )


def test_baseline_is_recorded_at_first_cluster_escalation(tmp_path):
    (tmp_path / "_数据库").mkdir()
    _ingest(tmp_path, _audit("cluster_001", "PLOT_X"))
    _ingest(tmp_path, _audit("cluster_002", "PLOT_X"))
    experience = loop.load_experience(tmp_path)
    baseline = experience["_efficacy_tracker"]["recur_结构_PLOT_X"]
    assert baseline["status"] == "monitoring"
    assert baseline["baseline_clusters"] == ["cluster_001", "cluster_002"]
    assert baseline["baseline_rate"] == 1.0


def test_re_escalation_does_not_reset_baseline(tmp_path):
    (tmp_path / "_数据库").mkdir()
    for index in (1, 2):
        _ingest(tmp_path, _audit(f"cluster_{index:03d}", "PLOT_X"))
    before = dict(loop.load_experience(tmp_path)["_efficacy_tracker"]["recur_结构_PLOT_X"])
    _ingest(tmp_path, _audit("cluster_003", "PLOT_X"))
    after = loop.load_experience(tmp_path)["_efficacy_tracker"]["recur_结构_PLOT_X"]
    assert after["baseline_clusters"] == before["baseline_clusters"]
    assert after["baseline_rate"] == before["baseline_rate"]


def test_ineffective_constraint_stops_next_cluster_injection(tmp_path):
    audits = [_audit(f"cluster_{index:03d}", "PLOT_X") for index in range(1, 7)]
    _write_audits(tmp_path, audits)
    result = loop.scan_recurring(tmp_path)
    pattern_id = "recur_结构_PLOT_X"
    assert pattern_id in {item["pattern_id"] for item in result["ineffective"]}
    experience = loop.load_experience(tmp_path)
    pattern = _pattern(experience, pattern_id)
    assert pattern["active"] is False
    assert pattern["efficacy"]["post_recur_clusters"] == [
        "cluster_003", "cluster_004", "cluster_005", "cluster_006"
    ]
    assert experience["_efficacy_tracker"][pattern_id]["status"] == "ineffective"


def test_effective_constraint_keeps_injecting(tmp_path):
    audits = [_audit("cluster_001", "PLOT_X"), _audit("cluster_002", "PLOT_X")]
    audits.extend(_audit(f"cluster_{index:03d}", "OTHER") for index in range(3, 7))
    _write_audits(tmp_path, audits)
    loop.scan_recurring(tmp_path)
    experience = loop.load_experience(tmp_path)
    efficacy = experience["_efficacy_tracker"]["recur_结构_PLOT_X"]
    assert efficacy["status"] == "effective"
    assert _pattern(experience, "recur_结构_PLOT_X").get("active") is not False


def test_insufficient_post_clusters_remain_monitoring(tmp_path):
    audits = [_audit("cluster_001", "PLOT_X"), _audit("cluster_002", "PLOT_X")]
    audits.append(_audit("cluster_003", "OTHER"))
    _write_audits(tmp_path, audits)
    loop.scan_recurring(tmp_path)
    efficacy = loop.load_experience(tmp_path)["_efficacy_tracker"]["recur_结构_PLOT_X"]
    assert efficacy["status"] == "monitoring"


def test_lower_post_cluster_rate_is_effective(tmp_path):
    audits = [
        _audit("cluster_001", "PLOT_X"),
        _audit("cluster_002", "PLOT_X"),
        _audit("cluster_003", "PLOT_X"),
    ]
    audits.extend(_audit(f"cluster_{index:03d}", "OTHER") for index in range(4, 7))
    _write_audits(tmp_path, audits)
    loop.scan_recurring(tmp_path)
    efficacy = loop.load_experience(tmp_path)["_efficacy_tracker"]["recur_结构_PLOT_X"]
    assert efficacy["status"] == "effective"
    assert efficacy["post_rate"] < efficacy["baseline_rate"]


def test_terminal_status_does_not_flip(tmp_path):
    audits = [_audit("cluster_001", "PLOT_X"), _audit("cluster_002", "PLOT_X")]
    audits.extend(_audit(f"cluster_{index:03d}", "OTHER") for index in range(3, 7))
    _write_audits(tmp_path, audits)
    loop.scan_recurring(tmp_path)
    loop.scan_recurring(tmp_path)
    status = loop.load_experience(tmp_path)["_efficacy_tracker"]["recur_结构_PLOT_X"]["status"]
    assert status == "effective"


def test_ineffective_constraint_is_not_reactivated(tmp_path):
    audits = [_audit(f"cluster_{index:03d}", "PLOT_X") for index in range(1, 7)]
    _write_audits(tmp_path, audits)
    loop.scan_recurring(tmp_path)
    _ingest(tmp_path, _audit("cluster_007", "PLOT_X"))
    pattern = _pattern(loop.load_experience(tmp_path), "recur_结构_PLOT_X")
    assert pattern["active"] is False


def test_manifest_skips_inactive_failure_pattern(tmp_path):
    database = tmp_path / "_数据库"
    database.mkdir()
    (database / "写作经验.json").write_text(json.dumps({
        "success_patterns": [],
        "failure_patterns": [
            {"id": "active", "category": "failure", "confidence": 0.95,
             "trigger": "继续注入", "scene_types": []},
            {"id": "stopped", "category": "failure", "confidence": 0.95,
             "trigger": "停止注入", "scene_types": [], "active": False},
        ],
        "preferences": [],
    }, ensure_ascii=False), encoding="utf-8")
    entries = manifest.DatabaseScanner(tmp_path, 1).experience_entries()
    ids = {entry.get("id") for entry in entries}
    assert "active" in ids
    assert "stopped" not in ids


def test_incremental_ingest_uses_observed_cluster_ledger(tmp_path):
    (tmp_path / "_数据库").mkdir()
    for index in range(1, 5):
        _ingest(tmp_path, _audit(f"cluster_{index:03d}", "PLOT_X"))
    experience = loop.load_experience(tmp_path)
    assert experience["_observed_clusters"] == [
        "cluster_001", "cluster_002", "cluster_003", "cluster_004"
    ]
    assert experience["_efficacy_tracker"]["recur_结构_PLOT_X"]["status"] == "ineffective"
