"""故事块互动指标聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_engagement_metrics_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_engagement_metrics_aggregate as scanner


def _project(records: list[dict]) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory(prefix="engagement_cluster_")
    project = Path(td.name) / "project"
    write_cluster_summary(project, records)
    return project, project / "_数据库", td


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(project), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=120,
    )


def test_collects_numeric_metrics_from_audit_summary() -> None:
    record = cluster_record(
        audit={
            "summary": {
                "hook_strength": {"score": 0.7},
                "golden_three": {
                    "kindling": 0.5, "hook": {"score": 0.6}, "turn": 0.8,
                },
            },
            "issues": [], "scanner_status": [],
        },
        truth_check={"verdict": "pass", "ending_type_match": True},
    )
    metrics = scanner.collect_cluster_metrics([record])
    assert metrics["hook"] == [("cluster_001", 0.7)]
    assert metrics["golden_kindling"] == [("cluster_001", 0.5)]
    assert metrics["golden_hook"] == [("cluster_001", 0.6)]
    assert metrics["golden_turn"] == [("cluster_001", 0.8)]
    assert metrics["truth_checks"] == 1
    assert metrics["truth_failures"] == 0


def test_hook_issue_becomes_cluster_weak_signal() -> None:
    record = cluster_record(audit={
        "summary": {},
        "issues": [{"code": "READER_EXP_HOOK_STRENGTH"}],
        "scanner_status": [{"scanner": "hook_strength_scanner", "ok": True}],
    })
    metrics = scanner.collect_cluster_metrics([record])
    assert metrics["hook"] == [("cluster_001", 0.0)]


def test_hook_trend_uses_cluster_ids() -> None:
    scores = [
        ("cluster_001", 0.9), ("cluster_002", 0.8),
        ("cluster_003", 0.7), ("cluster_004", 0.3),
    ]
    findings = scanner.scan_hook_trend(scores)
    decline = next(item for item in findings if item["code"] == "HOOK_STRENGTH_DECLINE")
    assert decline["trail"] == scores
    low = scanner.scan_hook_trend([
        ("cluster_001", 0.3), ("cluster_002", 0.2), ("cluster_003", 0.1)
    ])
    assert low[0]["low_clusters"] == ["cluster_001", "cluster_002", "cluster_003"]


def test_golden_trend_consumes_nested_audit_metrics() -> None:
    metrics = {
        "golden_kindling": [
            ("cluster_001", 0.9), ("cluster_002", 0.8),
            ("cluster_003", 0.7), ("cluster_004", 0.6),
        ],
        "golden_hook": [
            (f"cluster_{n:03d}", 0.5) for n in range(1, 6)
        ],
        "golden_turn": [],
    }
    findings = scanner.scan_golden_trend(metrics)
    assert any(item["code"] == "GOLDEN_DEGRADATION" for item in findings)
    assert any(item["code"] == "GOLDEN_FLAT" for item in findings)


def test_cliffhanger_quota_reports_ratio_and_streak_by_cluster() -> None:
    endings = [
        ("cluster_001", "悬念断章"),
        ("cluster_002", "悬念型"),
        ("cluster_003", "cliffhanger"),
        ("cluster_004", "场景硬收"),
    ]
    findings = scanner.scan_cliffhanger_quota(endings)
    ratio = next(item for item in findings if item["metric"] == "cliffhanger_ratio")
    streak = next(item for item in findings if item["metric"] == "consecutive_cliffhanger_streak")
    assert ratio["cliffhanger_clusters"] == [
        "cluster_001", "cluster_002", "cluster_003"
    ]
    assert streak["cluster_range"] == ["cluster_001", "cluster_003"]


def test_truth_check_mismatch_is_counted_without_new_gate() -> None:
    metrics = scanner.collect_cluster_metrics([
        cluster_record(truth_check={"verdict": "fail", "ending_type_match": False})
    ])
    assert metrics["truth_failures"] == 1
    assert metrics["ending_type_mismatches"] == 1


def test_ending_type_advisory_excluded_from_mismatch_count() -> None:
    """G4①回归锁：ending_type_advisory 非空（作者自由文学标签）→ 不计 mismatch，单列 advisory。"""
    metrics = scanner.collect_cluster_metrics([
        cluster_record(
            "cluster_001",
            truth_check={
                "verdict": "pass",
                "ending_type_match": False,
                "ending_type_advisory": {
                    "field": "applied_style.ending_type",
                    "declared": "死兆留白钩",
                    "actual": "场景硬收",
                },
            },
        ),
        cluster_record(
            "cluster_002",
            truth_check={"verdict": "fail", "ending_type_match": False},
        ),
    ])
    # cluster_001 是自由文学标签 → advisory 不计 mismatch；cluster_002 无 advisory → 真 mismatch
    assert metrics["ending_type_mismatches"] == 1
    assert len(metrics["ending_type_advisories"]) == 1
    assert metrics["ending_type_advisories"][0]["cluster_id"] == "cluster_001"
    assert metrics["ending_type_advisories"][0]["declared"] == "死兆留白钩"


def test_ending_type_advisory_is_consumed_not_orphan() -> None:
    """G4①回归锁：ending_type_advisory 有真实消费者（≥3 触发 taxonomy drift advisory）。"""
    advisories = [
        {"cluster_id": f"cluster_{n:03d}", "declared": "死兆留白钩", "detected": "场景硬收"}
        for n in range(1, 4)
    ]
    findings = scanner.scan_ending_type_advisories(advisories)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "ENDING_TYPE_TAXONOMY_DRIFT"
    assert finding["gate_level"] == "advisory"
    assert finding["count"] == 3
    # <3 不触发（跨 cluster 视野才有意义）
    assert scanner.scan_ending_type_advisories(advisories[:2]) == []


def test_ending_type_advisory_surfaces_in_cli_report() -> None:
    """G4①端到端：advisory 计数进 metrics_collected + drift finding 进 findings。"""
    records = [cluster_record(
        f"cluster_{n:03d}", ending_type="场景硬收",
        audit={"summary": {}, "issues": [], "scanner_status": []},
        truth_check={
            "verdict": "pass", "ending_type_match": False,
            "ending_type_advisory": {"declared": "死兆留白钩", "actual": "场景硬收"},
        },
    ) for n in range(1, 4)]
    project, db, td = _project(records)
    try:
        result = _run(project)
        report = json.loads(sorted(
            (db / ".cross_cluster_scan").glob("engagement_metrics_*.json"))[-1]
            .read_text(encoding="utf-8"))
        assert report["metrics_collected"]["ending_type_mismatches"] == 0
        assert report["metrics_collected"]["ending_type_advisories"] == 3
        assert any(f["code"] == "ENDING_TYPE_TAXONOMY_DRIFT" for f in report["findings"])
    finally:
        td.cleanup()


def test_cli_writes_cluster_report() -> None:
    records = [cluster_record(
        f"cluster_{n:03d}", ending_type="场景硬收",
        audit={"summary": {}, "issues": [], "scanner_status": []},
        truth_check={"verdict": "pass", "ending_type_match": True},
    ) for n in range(1, 4)]
    project, db, td = _project(records)
    try:
        result = _run(project, "--last-n", "2")
        assert result.returncode == 0
        reports = sorted((db / ".cross_cluster_scan").glob("engagement_metrics_*.json"))
        assert reports
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002", "cluster_003"]
        assert report["metrics_collected"]["truth_checks"] == 2
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "metrics_collected",
            "findings", "summary",
        }
    finally:
        td.cleanup()
