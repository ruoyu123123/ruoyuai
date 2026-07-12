"""跨故事块大势漂移报告的 CLI 与严重度测试。"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "core" / "scripts" / "cross_cluster_fate_drift_aggregate.py"


def _project(root: Path, maximum: int = 2) -> Path:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    payload = {
        "major_events": [
            {
                "id": "ME-V1-01", "title": "导火索", "status": "completed",
                "completed_at_cluster": "cluster_001", "prerequisites": [],
                "expected_window_after": None,
            },
            {
                "id": "ME-V1-02", "title": "反击", "status": "pending",
                "prerequisites": ["ME-V1-01"],
                "expected_window_after": {
                    "event": "ME-V1-01", "max_clusters": maximum,
                },
            },
        ]
    }
    (db / "大势卡.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _run(project: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, str(TARGET), str(project), *args],
        cwd=str(ROOT), capture_output=True,
    )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def _report(project: Path) -> dict:
    paths = list((project / "_数据库" / ".cross_cluster_scan").glob("fate_drift_*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_cli_warning_uses_overdue_cluster_count():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp), maximum=2)
        result = _run(project, "--cluster", "cluster_005")
        report = _report(project)
    assert result.returncode == 1, result.stderr
    assert report["cluster_id"] == "cluster_005"
    assert report["summary"] == {"warning": 1, "advisory": 0, "total": 1}
    finding = report["findings"][0]
    assert finding["gate_level"] == "advisory"
    assert finding["metric"]["overdue_by_clusters"] == 2


def test_cli_single_cluster_overdue_is_advisory():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp), maximum=2)
        result = _run(project, "--cluster", "cluster_004")
        report = _report(project)
    assert result.returncode == 0, result.stderr
    assert report["summary"] == {"warning": 0, "advisory": 1, "total": 1}


def test_cli_healthy_report_is_empty():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp), maximum=3)
        result = _run(project, "--cluster", "cluster_003")
        report = _report(project)
    assert result.returncode == 0, result.stderr
    assert report["findings"] == []
    assert report["summary"]["total"] == 0


def test_cli_requires_cluster_and_rejects_chapter_option():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp))
        missing = _run(project)
        old = _run(project, "--ch", "5")
    assert missing.returncode == 2
    assert old.returncode == 2
    assert not (project / "_数据库" / ".cross_cluster_scan").exists()
