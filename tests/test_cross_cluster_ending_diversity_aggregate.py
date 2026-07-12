"""Cluster ending-diversity scanner tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import cross_cluster_ending_diversity_aggregate as scanner  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _write_project(project: Path, endings: list[str | None]) -> None:
    clusters = [
        cluster_record(f"cluster_{index:03d}",
                       **({"ending_type": ending} if ending is not None else {}))
        for index, ending in enumerate(endings, start=1)
    ]
    write_cluster_summary(project, clusters)


def _run(project: Path) -> tuple[int, dict]:
    saved = sys.argv
    sys.argv = ["ending", str(project)]
    try:
        try:
            scanner.main()
        except SystemExit as error:
            code = int(error.code)
    finally:
        sys.argv = saved
    path = sorted((project / "_数据库" / ".cross_cluster_scan").glob("ending_diversity_*.json"))[-1]
    return code, json.loads(path.read_text(encoding="utf-8"))


def test_rec_ending_type_priority():
    record = {"ending_type": "cliffhanger", "self_eval": {"applied_style": {"ending_type": "resolution"}}}
    assert scanner._rec_ending_type(record) == "cliffhanger"
    assert scanner._rec_ending_type({"self_eval": {"applied_style": {"ending_type": "question"}}}) == "question"


def test_monotone_clusters_exit_warning(tmp_path):
    _write_project(tmp_path, ["cliffhanger"] * 4 + ["resolution"])
    code, report = _run(tmp_path)
    assert code == 2
    assert "ENDING_TYPE_MONOTONE" in {item["code"] for item in report["findings"]}


def test_missing_cluster_metadata_is_warning(tmp_path):
    _write_project(tmp_path, [None, None, None])
    code, report = _run(tmp_path)
    assert code == 2
    assert report["findings"][0]["missing_clusters"] == ["cluster_001", "cluster_002", "cluster_003"]


def test_low_diversity_is_advisory(tmp_path):
    _write_project(tmp_path, ["revelation", "revelation", "resolution", "resolution", "revelation", "resolution"])
    code, report = _run(tmp_path)
    assert code == 1
    assert "ENDING_TYPE_LOW_DIVERSITY" in {item["code"] for item in report["findings"]}


def test_run_uses_cluster_ids(tmp_path):
    _write_project(tmp_path, ["question"] * 4 + ["resolution", "revelation", "promise", "threat"])
    _code, report = _run(tmp_path)
    finding = next(item for item in report["findings"] if item["code"] == "ENDING_TYPE_RUN")
    assert finding["consecutive_clusters"] == ["cluster_001", "cluster_002", "cluster_003", "cluster_004"]


def test_source_has_no_mode_or_chapter_fallback():
    source = (ROOT / "core" / "scripts" / "cross_cluster_ending_diversity_aggregate.py").read_text(encoding="utf-8")
    assert "CLUSTER_MODE" not in source
    assert 'project_root / "章节"' not in source
