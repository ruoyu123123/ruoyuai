"""故事块 Judge 质量聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_judge_quality_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_judge_quality_aggregate as scanner


def _records(scores: list[float | None], **shared) -> list[dict]:
    return [cluster_record(
        f"cluster_{index:03d}", judge_score=score,
        judge_grade=("A" if score is not None and score >= 3.5 else "B") if score is not None else None,
        **shared,
    ) for index, score in enumerate(scores, start=1)]


def _codes(findings: list[dict]) -> set[str]:
    return {item["code"] for item in findings}


def test_score_decline_uses_cluster_trail() -> None:
    records = _records([4.0, 3.5, 3.0, 2.5])
    findings = scanner.scan_judge_scores(records)
    decline = next(item for item in findings if item["code"] == "JUDGE_SCORE_DECLINE")
    assert decline["trail"] == [
        ("cluster_001", 4.0), ("cluster_002", 3.5),
        ("cluster_003", 3.0), ("cluster_004", 2.5),
    ]


def test_score_plateau_and_volatility_use_grade_scale() -> None:
    plateau = scanner.scan_judge_scores(_records([3.0, 3.02, 3.01, 2.99, 3.0]))
    assert "JUDGE_SCORE_PLATEAU" in _codes(plateau)
    volatile = scanner.scan_judge_scores(_records([1.0, 4.0, 1.0, 4.0, 1.0]))
    assert "JUDGE_SCORE_VOLATILITY" in _codes(volatile)


def test_waiver_runaway_and_persistent_code_use_cluster_order() -> None:
    repeated = {"code": "STYLE_TEST", "reason": "作者档明确允许"}
    records = [
        cluster_record("cluster_001", waivers=[repeated]),
        cluster_record("cluster_002", waivers=[repeated]),
        cluster_record("cluster_003", waivers=[repeated]),
        cluster_record(
            "cluster_004",
            waivers=[{"code": f"CODE_{n}", "reason": "明确理由"} for n in range(5)],
        ),
    ]
    findings = scanner.scan_waiver_accumulation(records)
    persistent = next(item for item in findings if item["code"] == "WAIVER_PERSISTENT_CODE")
    runaway = next(item for item in findings if item["code"] == "WAIVER_RUNAWAY")
    assert persistent["cluster_range"] == ["cluster_001", "cluster_003"]
    assert persistent["consecutive_clusters"] == 3
    assert runaway["cluster_id"] == "cluster_004"


def test_judge_report_disagreement_reads_summary_reports() -> None:
    records = [cluster_record(
        judge_reports=[
            {"judge_id": "reader", "overall_grade": "A"},
            {"judge_id": "critic", "overall_grade": "C"},
        ]
    )]
    finding = scanner.scan_report_disagreement(records)[0]
    assert finding["code"] == "JUDGE_REPORT_DISAGREEMENT"
    assert finding["cluster_id"] == "cluster_001"
    assert finding["score_range"] == [2.0, 4.0]


def test_metrics_consume_all_canonical_judge_fields() -> None:
    records = [cluster_record(
        judge_score=3.5, judge_grade="A",
        judge_reports=[{"judge_id": "reader", "overall_grade": "A"}],
        waivers=[{"code": "STYLE_TEST", "reason": "作者档允许"}],
    )]
    assert scanner._metrics(records) == {
        "scored_clusters": 1,
        "graded_clusters": 1,
        "judge_reports": 1,
        "waivers": 1,
        "grade_distribution": {"A": 1},
    }


def test_cli_writes_cluster_report() -> None:
    td = tempfile.TemporaryDirectory(prefix="judge_quality_cluster_")
    try:
        project = Path(td.name) / "project"
        records = _records([3.0, 3.1, 3.2])
        write_cluster_summary(project, records)
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(project), "--last-n", "2"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_ENV, timeout=120,
        )
        assert result.returncode == 0
        reports = sorted((project / "_数据库" / ".cross_cluster_scan").glob(
            "judge_quality_*.json"
        ))
        assert reports
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002", "cluster_003"]
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "metrics", "findings", "summary"
        }
    finally:
        td.cleanup()
