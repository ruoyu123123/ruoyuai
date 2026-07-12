"""跨故事块顾问 required 调度器的确定性合同测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_cross_cluster_aggregates as runner  # noqa: E402


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", "cluster_001"), ("cluster_1", "cluster_001"),
     ("cluster_023", "cluster_023"), ("9999", "cluster_9999")],
)
def test_normalize_cluster_id(value, expected):
    assert runner.normalize_cluster_id(value) == expected


@pytest.mark.parametrize("value", ["0", "cluster_000", "cluster_x", "ch_3", "", "-1"])
def test_normalize_cluster_id_rejects_noncanonical_input(value):
    with pytest.raises(ValueError, match="无效 cluster"):
        runner.normalize_cluster_id(value)


def test_cluster_draft_path_is_cluster_native():
    root = Path("P:/novel")
    assert runner.cluster_draft_path(root, "cluster_007") == (
        root / "章节" / "cluster_007_draft" / "cluster_007_draft.txt"
    )


def test_scanner_registry_has_no_duplicates_and_all_scripts_exist():
    assert len(runner.SCANNERS) == len(set(runner.SCANNERS))
    assert len(runner.SCANNERS) >= 30
    missing = [name for name in runner.SCANNERS if not (SCRIPTS / f"{name}.py").is_file()]
    assert missing == []


def test_build_command_for_fate_is_explicit_cluster():
    command = runner.build_scanner_command(
        "cross_cluster_fate_drift_aggregate", Path("fate.py"), Path("project"),
        "cluster_004", 8,
    )
    assert command[-3:] == ["project", "--cluster", "cluster_004"]
    assert "--ch" not in command and "--auto" not in command


def test_build_command_for_location_includes_cluster_draft():
    project = Path("project")
    command = runner.build_scanner_command(
        "location_signature_consistency", Path("location.py"), project,
        "cluster_004", 8,
    )
    assert command[2:6] == ["--project", "project", "--scan-cluster", "cluster_004"]
    assert command[-2] == "--draft"
    assert command[-1].endswith("cluster_004_draft.txt")


def test_build_command_uses_cluster_window_only_when_supported():
    no_window = runner.build_scanner_command(
        "cross_cluster_arc_progression_aggregate", Path("arc.py"), Path("project"),
        "cluster_003", 7,
    )
    with_window = runner.build_scanner_command(
        "cross_cluster_continuity_aggregate", Path("continuity.py"), Path("project"),
        "cluster_003", 7,
    )
    assert no_window[2:] == ["project"]
    assert with_window[2:] == ["project", "--last-n", "7"]


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (0, "ok", "", False),
        (1, "advisory", "", False),
        (2, "warning", "", False),
        (2, "", "[FATAL] bad input", True),
        (3, "", "", True),
        (-9, "", "", True),
        (0, "Traceback (most recent call last)", "", True),
        (0, "", "usage: scanner", True),
    ],
)
def test_execution_failure_classification(returncode, stdout, stderr, expected):
    assert runner._is_execution_failure(returncode, stdout, stderr) is expected


def test_run_scanner_preserves_advisory_exit(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="finding", stderr=""
    ))
    result = runner._run_scanner("scanner", ["python", "scanner.py"], {}, 10)
    assert result["status"] == "advisory"
    assert result["exit_code"] == 1


def test_run_scanner_marks_fatal_output_failed(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=2, stdout="", stderr="[FATAL] contract broken"
    ))
    result = runner._run_scanner("scanner", ["python", "scanner.py"], {}, 10)
    assert result["status"] == "failed"


def test_write_run_report_uses_cross_cluster_directory():
    with tempfile.TemporaryDirectory() as temp:
        project = Path(temp)
        path = runner.write_run_report(project, {"required": True})
        assert path == project / "_数据库" / ".cross_cluster_scan" / "cross_cluster_wrapper_latest.json"
        assert json.loads(path.read_text(encoding="utf-8")) == {"required": True}


def _project(root: Path) -> Path:
    project = root / "novel"
    project.mkdir(parents=True)
    return project


def _scripts(root: Path, names: list[str]) -> Path:
    root.mkdir(parents=True)
    for name in names:
        (root / f"{name}.py").write_text("# scanner\n", encoding="utf-8")
    return root


def test_main_requires_every_registered_scanner(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        project = _project(root)
        scripts = _scripts(root / "scripts", ["one"])
        monkeypatch.setattr(runner, "SCANNERS", ["one", "missing"])
        monkeypatch.setattr(runner, "scripts_dir", lambda: scripts)
        monkeypatch.setattr(runner, "_run_scanner", lambda *args, **kwargs: {
            "scanner": "one", "status": "ok", "exit_code": 0,
            "stdout_tail": "", "stderr_tail": "",
        })
        assert runner.main([str(project), "--cluster", "1"]) == 2
        report = json.loads((
            project / "_数据库" / ".cross_cluster_scan" /
            "cross_cluster_wrapper_latest.json"
        ).read_text(encoding="utf-8"))
        assert report["expected_count"] == 2
        assert report["executed_count"] == 2
        assert report["failed_count"] == 1
        assert report["tasks"][1]["status"] == "failed"


def test_main_advisory_findings_do_not_fail_required_execution(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        project = _project(root)
        scripts = _scripts(root / "scripts", ["one"])
        monkeypatch.setattr(runner, "SCANNERS", ["one"])
        monkeypatch.setattr(runner, "scripts_dir", lambda: scripts)
        monkeypatch.setattr(runner, "_run_scanner", lambda *args, **kwargs: {
            "scanner": "one", "status": "advisory", "exit_code": 1,
            "stdout_tail": "finding", "stderr_tail": "",
        })
        assert runner.main([str(project), "--cluster", "cluster_002"]) == 0
        report = json.loads((
            project / "_数据库" / ".cross_cluster_scan" /
            "cross_cluster_wrapper_latest.json"
        ).read_text(encoding="utf-8"))
        assert report["required"] is True
        assert report["advisory_count"] == 1
        assert report["failed_count"] == 0


def test_main_timeout_is_required_failure(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        project = _project(root)
        scripts = _scripts(root / "scripts", ["one"])
        monkeypatch.setattr(runner, "SCANNERS", ["one"])
        monkeypatch.setattr(runner, "scripts_dir", lambda: scripts)
        monkeypatch.setattr(
            runner,
            "_run_scanner",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["scanner"], 1)
            ),
        )
        assert runner.main([str(project), "--cluster", "1", "--timeout", "1"]) == 2


def test_main_rejects_missing_project_and_invalid_cluster(tmp_path):
    assert runner.main([str(tmp_path / "missing"), "--cluster", "1"]) == 2
    project = _project(tmp_path)
    assert runner.main([str(project), "--cluster", "ch_1"]) == 2
