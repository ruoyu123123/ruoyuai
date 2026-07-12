"""persona drift 聚合器的 canonical cluster 回归。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cross_cluster_persona_drift_aggregate as aggregate  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def test_build_findings_uses_cluster_id_and_advisory_gate() -> None:
    findings = aggregate._build_findings({
        "cluster_002": [{"character": "甲", "drift": 0.6}],
        "cluster_003": [{"character": "乙", "drift": 0.8}],
    })
    assert [item["cluster_id"] for item in findings] == [
        "cluster_002", "cluster_003"
    ]
    assert findings[0]["severity"] == "advisory"
    assert findings[1]["severity"] == "warning"
    assert all(item["gate_level"] == "advisory" for item in findings)


def test_extract_persona_drift_reads_audit_rollup() -> None:
    clusters = [
        cluster_record(
            "cluster_001",
            audit={"persona_drift": {"甲": 0.2, "乙": "invalid"}},
        ),
        cluster_record("cluster_002", audit={"persona_drift": {"甲": 0.7}}),
    ]
    result = aggregate.extract_persona_drift(clusters)
    assert result["cluster_001"][0]["character"] == "甲"
    assert result["cluster_002"][0]["drift"] == 0.7
    assert "乙" not in json.dumps(result, ensure_ascii=False)


def test_cli_writes_cluster_report_and_warning_exit(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [
        cluster_record(
            "cluster_001", audit={"persona_drift": {"甲": 0.82}}
        ),
        cluster_record(
            "cluster_002", audit={"persona_drift": {"甲": 0.3}}
        ),
    ])
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "cross_cluster_persona_drift_aggregate.py"), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 2, result.stderr
    reports = sorted(
        (tmp_path / "_数据库" / ".cross_cluster_scan").glob("persona_drift_*.json")
    )
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["clusters_scanned"] == ["cluster_001", "cluster_002"]
    assert "per_cluster" in report
    assert "per_chapter" not in report
    assert report["summary"]["warning"] == 1


def test_cli_skips_when_audit_has_no_telemetry(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [cluster_record("cluster_001")])
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "cross_cluster_persona_drift_aggregate.py"), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0
    assert not (tmp_path / "_数据库" / ".cross_cluster_scan").exists()


def test_audit_hub_telemetry_normalizes_statistics_and_embedding() -> None:
    import audit_hub

    stdout = json.dumps({
        "drift_issues": [
            {"character": "甲", "deviation_pct": 60},
            {"character": "甲", "embedding_distance": 0.8},
            {"character": "乙", "deviation_pct": 25},
        ]
    }, ensure_ascii=False)
    assert audit_hub._persona_drift_telemetry(stdout) == {
        "甲": 0.8,
        "乙": 0.25,
    }
