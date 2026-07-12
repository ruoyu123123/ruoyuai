"""cluster 级 throughline 覆盖率回归测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "core" / "scripts" / "cross_cluster_throughline_balance_aggregate.py"
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cross_cluster_throughline_balance_aggregate as scanner  # noqa: E402


def _project(tmp_path: Path, cluster_count: int, active_count: int) -> Path:
    project = tmp_path / "project"
    database = project / "_数据库"
    database.mkdir(parents=True)
    active = scanner.THROUGHLINES[:active_count]
    event_clusters = []
    summaries = []
    for number in range(1, cluster_count + 1):
        cluster_id = f"cluster_{number:03d}"
        progress = {line: f"{line} 本块有实质推进" for line in active}
        event_clusters.append({"cluster_id": cluster_id, "throughline_progress": progress})
        summaries.append({"cluster_id": cluster_id})
    (database / "事件簇.json").write_text(
        json.dumps({"clusters": event_clusters}, ensure_ascii=False), encoding="utf-8")
    (database / "故事块摘要.json").write_text(
        json.dumps({"clusters": summaries}, ensure_ascii=False), encoding="utf-8")
    return project


def _run(project: Path) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(SCANNER), str(project), "--last-n", "10"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert "Traceback" not in result.stderr
    reports = sorted((project / "_数据库" / ".cross_cluster_scan").glob("throughline_balance_*.json"))
    assert reports, result.stdout + result.stderr
    return result.returncode, json.loads(reports[-1].read_text(encoding="utf-8"))


@pytest.mark.parametrize("cluster_count", [1, 3, 4])
def test_small_window_does_not_report_coverage(cluster_count: int, tmp_path: Path):
    _, report = _run(_project(tmp_path, cluster_count, 1))
    assert "PER_CLUSTER_COVERAGE_LOW" not in {item["code"] for item in report["findings"]}


@pytest.mark.parametrize("cluster_count", [5, 6])
def test_large_window_reports_low_coverage(cluster_count: int, tmp_path: Path):
    returncode, report = _run(_project(tmp_path, cluster_count, 1))
    assert "PER_CLUSTER_COVERAGE_LOW" in {item["code"] for item in report["findings"]}
    assert returncode != 0
    assert report["clusters_scanned"][-1] == f"cluster_{cluster_count:03d}"


def test_large_window_with_two_active_lines_is_healthy(tmp_path: Path):
    _, report = _run(_project(tmp_path, 6, 2))
    assert "PER_CLUSTER_COVERAGE_LOW" not in {item["code"] for item in report["findings"]}


def test_source_keeps_small_sample_guard():
    source = SCANNER.read_text(encoding="utf-8")
    assert "total >= 5 and len(clusters_with_lt2) >= total * 0.4" in source


def test_has_progress_semantics():
    assert scanner._has_progress("本块推进") is True
    assert scanner._has_progress("no_progress") is False
    assert scanner._has_progress(False) is False
