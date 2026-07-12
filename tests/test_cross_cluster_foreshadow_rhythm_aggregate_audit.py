"""伏笔故事块节奏聚合器的合同与行为测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary


_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_foreshadow_rhythm_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _project(last_cluster: int = 7) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory(prefix="foreshadow_cluster_")
    project = Path(td.name) / "project"
    db = project / "_数据库"
    db.mkdir(parents=True)
    records = [cluster_record(f"cluster_{n:03d}", word_count=3500)
               for n in range(1, last_cluster + 1)]
    write_cluster_summary(project, records)
    return project, db, td


def _write_promises(db: Path, promises: list[dict]) -> None:
    canonical = []
    for promise in promises:
        item = {"owner": "writer", "payoff_scope": ""}
        item.update(promise)
        canonical.append(item)
    (db / "伏笔表.json").write_text(
        json.dumps({"schema_version": "v27", "promises": canonical,
                    "deadlines": [], "pledges": [], "secrets": []},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def _run(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_ENV,
        timeout=120,
    )


def _report(db: Path) -> dict:
    files = sorted((db / ".cross_cluster_scan").glob("foreshadow_rhythm_*.json"))
    assert files, "未生成故事块伏笔报告"
    return json.loads(files[-1].read_text(encoding="utf-8"))


def test_report_uses_current_cluster_and_due_window() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [{
            "id": "fs_due", "setup_cluster": "cluster_001",
            "due_by_cluster": "cluster_003", "status": "open",
            "payoff_progress": [],
        }])
        result = _run(project)
        assert result.returncode == 1
        report = _report(db)
        assert report["current_cluster_id"] == "cluster_007"
        assert report["clusters_scanned"][-1] == "cluster_007"
        assert set(report) == {
            "scan_type", "scan_ts", "current_cluster_id", "clusters_scanned",
            "total_promises", "findings", "summary",
        }
        finding = report["findings"][0]
        assert finding["code"] == "FORESHADOW_OVERDUE"
        assert finding["due_by_cluster"] == "cluster_003"
        assert finding["overdue_by_clusters"] == 4
    finally:
        td.cleanup()


def test_progress_suppresses_missing_reinforcement() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [
            {"id": "fs_progress", "setup_cluster": "cluster_001",
             "due_by_cluster": None, "status": "open",
             "payoff_progress": ["cluster_003"]},
            {"id": "fs_silent", "setup_cluster": "cluster_001",
             "due_by_cluster": None, "status": "open", "payoff_progress": []},
        ])
        _run(project)
        findings = _report(db)["findings"]
        assert not any(item["id"] == "fs_progress" and
                       item["code"] == "FORESHADOW_NO_REINFORCEMENT"
                       for item in findings)
        assert any(item["id"] == "fs_silent" and
                   item["code"] == "FORESHADOW_NO_REINFORCEMENT"
                   for item in findings)
    finally:
        td.cleanup()


def test_eight_progress_entries_emit_fatigue_advisory() -> None:
    project, db, td = _project(9)
    try:
        _write_promises(db, [{
            "id": "fs_many", "setup_cluster": "cluster_001",
            "due_by_cluster": None, "status": "open",
            "payoff_progress": [f"cluster_{n:03d}" for n in range(1, 9)],
        }])
        _run(project)
        finding = _report(db)["findings"][0]
        assert finding["code"] == "FORESHADOW_OVER_REINFORCEMENT"
        assert finding["payoff_progress_count"] == 8
    finally:
        td.cleanup()


def test_consumed_promise_is_not_scanned() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [{
            "id": "fs_done", "setup_cluster": "cluster_001",
            "due_by_cluster": "cluster_002", "status": "consumed",
            "consumed_at_cluster": "cluster_004", "payoff_progress": [],
        }])
        result = _run(project)
        assert result.returncode == 0
        assert _report(db)["findings"] == []
    finally:
        td.cleanup()


def test_suspended_promise_does_not_create_rhythm_findings() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [{
            "id": "fs_hold", "setup_cluster": "cluster_001",
            "due_by_cluster": "cluster_002", "status": "suspended",
            "owner": "writer", "payoff_scope": "",
            "payoff_progress": [],
        }])
        result = _run(project)
        assert result.returncode == 0
        assert _report(db)["findings"] == []
    finally:
        td.cleanup()


def test_future_setup_is_not_scanned() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [{
            "id": "fs_future", "setup_cluster": "cluster_009",
            "due_by_cluster": "cluster_010", "status": "open",
            "payoff_progress": [],
        }])
        result = _run(project)
        assert result.returncode == 0
        assert _report(db)["findings"] == []
    finally:
        td.cleanup()


def test_consumed_requires_consumed_cluster() -> None:
    project, db, td = _project()
    try:
        _write_promises(db, [{
            "id": "fs_invalid", "setup_cluster": "cluster_001",
            "due_by_cluster": None, "status": "consumed",
            "payoff_progress": [],
        }])
        result = _run(project)
        assert result.returncode == 2
        assert "consumed_at_cluster" in result.stderr
        assert not (db / ".cross_cluster_scan").exists()
    finally:
        td.cleanup()


def test_promises_is_required_top_level_contract() -> None:
    project, db, td = _project()
    try:
        (db / "伏笔表.json").write_text(
            json.dumps({"schema_version": "v27"}), encoding="utf-8"
        )
        result = _run(project)
        assert result.returncode == 2
        assert "promises" in result.stderr
    finally:
        td.cleanup()


def test_module_imports() -> None:
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import cross_cluster_foreshadow_rhythm_aggregate as module

    assert callable(module.build_report)
    assert callable(module.main)
