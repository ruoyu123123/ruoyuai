"""故事块结构合规聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_structure_compliance_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_structure_compliance_aggregate as scanner


def _project(records: list[dict]) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory(prefix="structure_cluster_")
    project = Path(td.name) / "project"
    database = project / "_数据库"
    (database / ".wal").mkdir(parents=True)
    write_cluster_summary(project, records)
    return project, database, td


def _write_choice(database: Path, cluster_id: str, brief: dict) -> Path:
    path = database / ".wal" / f"{cluster_id}_user_choice.json"
    path.write_text(json.dumps({"answer": brief}, ensure_ascii=False), encoding="utf-8")
    return path


def _write_events(database: Path, clusters: list[dict]) -> None:
    (database / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8"
    )


def test_beat_keywords_current_mapping() -> None:
    assert scanner.beat_keywords_for("Catalyst") == [
        "催化", "意外", "事件", "异常", "异变", "震惊"
    ]
    assert scanner.beat_keywords_for("Midpoint_turn") == ["反转", "转折", "中点", "突变"]


def test_declared_beat_without_summary_signal_is_missed() -> None:
    records = [cluster_record(structure={
        "narrative_mode": "linear", "scene_count": 3,
        "summary_scene_count": 3, "appraisal_beats": [],
        "beats_declared": ["Midpoint"],
        "beats_addressed": [], "beat_signal_hit": False,
    })]
    finding = scanner.scan_beat_progression(records)[0]
    assert finding["code"] == "BEAT_MISSED"
    assert finding["cluster_id"] == "cluster_001"


def test_explicit_beats_addressed_satisfies_declared_beat() -> None:
    records = [cluster_record(structure={
        "narrative_mode": "linear", "scene_count": 3,
        "summary_scene_count": 3, "appraisal_beats": [],
        "beats_declared": ["Midpoint"],
        "beats_addressed": ["Midpoint reached"], "beat_signal_hit": False,
    })]
    assert scanner.scan_beat_progression(records) == []


def test_global_signal_does_not_mask_unaddressed_declared_beat() -> None:
    records = [cluster_record(structure={
        "narrative_mode": "linear", "scene_count": 3,
        "summary_scene_count": 3, "appraisal_beats": [],
        "beats_declared": ["Catalyst", "Midpoint"],
        "beats_addressed": ["Catalyst"], "beat_signal_hit": True,
    })]
    findings = scanner.scan_beat_progression(records)
    assert [item["expected_beat"] for item in findings] == ["Midpoint"]


def test_beat_gap_uses_cluster_distance() -> None:
    records = [
        cluster_record(
            f"cluster_{n:03d}",
            structure={
                "beats_declared": ["Opening Image"] if n == 1 else
                ["Catalyst"] if n == 7 else [],
                "beats_addressed": ["Opening Image"] if n == 1 else
                ["Catalyst"] if n == 7 else [],
                "beat_signal_hit": n in {1, 7},
            },
        )
        for n in range(1, 8)
    ]
    findings = scanner.scan_beat_progression(records)
    gap = next(item for item in findings if item["code"] == "BEAT_GAP_TOO_LONG")
    assert gap["from_cluster"] == "cluster_001"
    assert gap["to_cluster"] == "cluster_007"
    assert gap["gap_clusters"] == 6


def test_user_choice_not_landed_is_warning() -> None:
    project, database, td = _project([cluster_record("cluster_001")])
    try:
        _write_events(database, [{"cluster_id": "cluster_001"}])
        _write_choice(database, "cluster_002", {
            "cluster_id": "cluster_002", "scope_summary": "选中走向",
        })
        finding = scanner.scan_user_choice_landed(project)[0]
        assert finding["code"] == "USER_CHOICE_NOT_LANDED"
        assert finding["cluster_id"] == "cluster_002"
    finally:
        td.cleanup()


def test_user_choice_cluster_id_mismatch_is_warning() -> None:
    project, database, td = _project([cluster_record("cluster_001")])
    try:
        _write_events(database, [])
        _write_choice(database, "cluster_002", {
            "cluster_id": "cluster_003", "scope_summary": "错误编号",
        })
        assert scanner.scan_user_choice_landed(project)[0]["code"] == \
            "USER_CHOICE_CLUSTER_ID_MISMATCH"
    finally:
        td.cleanup()


def test_user_choice_landed_field_mismatch_is_advisory() -> None:
    project, database, td = _project([cluster_record("cluster_001")])
    try:
        _write_choice(database, "cluster_002", {
            "cluster_id": "cluster_002", "scope_summary": "选中走向",
            "ripple_match": "微弱涟漪", "scene_storyboard": [{"scene_idx": 0}],
        })
        _write_events(database, [{
            "cluster_id": "cluster_002", "scope_summary": "另一走向",
            "ripple_match": "微弱涟漪", "scene_storyboard": [],
        }])
        finding = scanner.scan_user_choice_landed(project)[0]
        assert finding["code"] == "USER_CHOICE_LANDED_MISMATCH"
        assert set(finding["fields"]) == {"scope_summary", "scene_storyboard"}
    finally:
        td.cleanup()


def test_user_choice_landed_clean() -> None:
    project, database, td = _project([cluster_record("cluster_001")])
    try:
        brief = {
            "cluster_id": "cluster_002", "scope_summary": "选中走向",
            "ripple_match": "微弱涟漪", "scene_storyboard": [{"scene_idx": 0}],
        }
        _write_choice(database, "cluster_002", brief)
        _write_events(database, [brief])
        assert scanner.scan_user_choice_landed(project) == []
    finally:
        td.cleanup()


def test_cli_writes_cluster_report() -> None:
    records = [cluster_record(f"cluster_{n:03d}") for n in range(1, 4)]
    project, database, td = _project(records)
    try:
        _write_events(database, [])
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(project), "--last-n", "2"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_ENV, timeout=120,
        )
        assert result.returncode == 0
        reports = sorted((database / ".cross_cluster_scan").glob(
            "structure_compliance_*.json"
        ))
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002", "cluster_003"]
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "findings", "summary"
        }
    finally:
        td.cleanup()
