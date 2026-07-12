"""Cluster offscreen-action scanner tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import cross_cluster_offscreen_aggregate as scanner  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _project(path: Path, offscreen: dict, *, second: dict | None = None) -> None:
    clusters = [cluster_record("cluster_001", offscreen=offscreen)]
    if second is not None:
        clusters.append(cluster_record("cluster_002", offscreen=second))
    write_cluster_summary(path, clusters)


def test_missing_execution_is_warning(tmp_path):
    _project(tmp_path, {"expected": [{"character": "周明", "action": "潜入"}], "executed": []})
    findings, metrics, scanned = scanner._scan_from_ledger(tmp_path, 5)
    assert scanned == ["cluster_001"]
    assert findings[0]["code"] == "OFFSCREEN_ACTIONS_NOT_EXECUTED"
    assert metrics["cluster_001"]["expected_count"] == 1


def test_thin_evidence_is_advisory(tmp_path):
    _project(tmp_path, {"expected": [], "executed": [{"character": "周明", "evidence": "短"}]})
    findings, _metrics, _scanned = scanner._scan_from_ledger(tmp_path, 5)
    assert findings[0]["code"] == "OFFSCREEN_EVIDENCE_THIN"
    assert findings[0]["severity"] == "advisory"


def test_backlog_is_cluster_scoped(tmp_path):
    _project(tmp_path, {"expected": [], "executed": [], "backlog_count": 2})
    findings, _metrics, _scanned = scanner._scan_from_ledger(tmp_path, 5)
    assert findings[0]["cluster_id"] == "cluster_001"


def test_backlog_only_reports_latest_cluster(tmp_path):
    _project(
        tmp_path,
        {"expected": [], "executed": [], "backlog_count": 3},
        second={"expected": [], "executed": [], "backlog_count": 1},
    )
    findings, _metrics, scanned = scanner._scan_from_ledger(tmp_path, 5)
    backlog = [item for item in findings if item["code"] == "OFFSCREEN_BACKLOG"]
    assert scanned == ["cluster_001", "cluster_002"]
    assert [item["cluster_id"] for item in backlog] == ["cluster_002"]


def test_source_has_no_mode_or_disk_fallback():
    source = (ROOT / "core" / "scripts" / "cross_cluster_offscreen_aggregate.py").read_text(encoding="utf-8")
    assert "CLUSTER_MODE" not in source
    assert 'project_root / "章节"' not in source
    assert 'cluster.get("chapters")' not in source
