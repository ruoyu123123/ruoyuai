"""故事块元质量聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_meta_quality_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_meta_quality_aggregate as scanner


def _records(counts: list[int]) -> list[dict]:
    return [cluster_record(
        f"cluster_{index:03d}", word_count=count,
        summary="主角追查失踪案并在废楼发现新的血迹证据，嫌疑人身份随即发生变化。" * 2,
        text_keyword_set=["主角", "追查", "失踪案", "废楼", "血迹", "嫌疑人"],
    ) for index, count in enumerate(counts, start=1)]


def _codes(findings: list[dict]) -> set[str]:
    return {item["code"] for item in findings}


def test_length_outlier_uses_cluster_word_count() -> None:
    findings = scanner.scan_length_distribution(_records([12000, 12100, 11900, 4000]))
    outlier = next(item for item in findings if item["code"] == "LENGTH_OUTLIER")
    assert outlier["cluster_id"] == "cluster_004"
    assert outlier["word_count"] == 4000


def test_length_drop_uses_cluster_sequence() -> None:
    findings = scanner.scan_length_distribution(_records([14000, 13000, 12000, 11000]))
    drop = next(item for item in findings if item["code"] == "LENGTH_TREND_DROP")
    assert drop["consecutive_clusters"] == [
        "cluster_001", "cluster_002", "cluster_003", "cluster_004"
    ]
    assert drop["trail"] == [14000, 13000, 12000, 11000]


def test_length_variance_requires_five_clusters() -> None:
    findings = scanner.scan_length_distribution(_records([4000, 20000, 4000, 20000, 4000]))
    assert "LENGTH_VARIANCE_HIGH" in _codes(findings)
    assert scanner.scan_length_distribution(_records([4000, 20000])) == []


def test_short_summary_is_reported_by_cluster() -> None:
    records = [cluster_record(summary="太短", word_count=10000, text_keyword_set=[])]
    finding = scanner.scan_summary_consistency(records)[0]
    assert finding["code"] == "SUMMARY_TOO_SHORT"
    assert finding["cluster_id"] == "cluster_001"


def test_metrics_consume_only_cluster_summary_fields() -> None:
    records = _records([10000, 12000])
    assert scanner._metrics(records) == {
        "cluster_count": 2,
        "total_word_count": 22000,
        "mean_word_count": 11000.0,
        "min_word_count": 10000,
        "max_word_count": 12000,
        "summaries_with_keyword_fingerprint": 2,
    }


def test_cli_writes_cluster_report() -> None:
    td = tempfile.TemporaryDirectory(prefix="meta_quality_cluster_")
    try:
        project = Path(td.name) / "project"
        write_cluster_summary(project, _records([10000, 10100, 10200]))
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(project), "--last-n", "2"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_ENV, timeout=120,
        )
        assert result.returncode == 0
        reports = sorted((project / "_数据库" / ".cross_cluster_scan").glob(
            "meta_quality_*.json"
        ))
        assert reports
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002", "cluster_003"]
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "metrics", "findings", "summary"
        }
    finally:
        td.cleanup()
