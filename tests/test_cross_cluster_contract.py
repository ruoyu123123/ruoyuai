"""跨 cluster required 调度器与流程文档的一致性测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
RUNNER_PATH = SCRIPTS / "run_cross_cluster_aggregates.py"
sys.path.insert(0, str(SCRIPTS))

import run_cross_cluster_aggregates as runner  # noqa: E402


def test_every_required_scanner_exists_once():
    assert len(runner.SCANNERS) == len(set(runner.SCANNERS))
    assert len(runner.SCANNERS) >= 30
    assert [
        name for name in runner.SCANNERS
        if not (SCRIPTS / f"{name}.py").is_file()
    ] == []


def test_required_scanners_are_registered_as_cross_cluster():
    registry = json.loads(
        (SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8")
    )
    entries = registry.get("scanners", registry)
    by_script = {
        str(value.get("script")): value
        for value in entries.values()
        if isinstance(value, dict)
    }
    missing = []
    wrong_layer = []
    for name in runner.SCANNERS:
        script = f"{name}.py"
        entry = by_script.get(script)
        if entry is None:
            missing.append(script)
        elif entry.get("layer") != "cross-cluster":
            wrong_layer.append(script)
    assert missing == []
    assert wrong_layer == []


def test_runner_has_no_reduced_or_chapter_mode():
    source = RUNNER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "SCAN_TIERS",
        "--tier",
        "minimal_5",
        "core_10",
        "full_18",
        "--ch",
        "chapters_in_last_n_clusters",
        "cross_cluster_scan_intensity",
    ):
        assert forbidden not in source


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_requires_cluster_and_rejects_removed_options():
    with tempfile.TemporaryDirectory() as temp:
        project = Path(temp)
        missing = _run(str(project))
        old_ch = _run(str(project), "--ch", "3")
        old_tier = _run(str(project), "--cluster", "3", "--tier", "full_18")
    assert missing.returncode == 2
    assert old_ch.returncode == 2
    assert old_tier.returncode == 2


def test_plan_and_command_use_single_cluster_cli():
    plan = json.loads((
        ROOT / "core" / "claude-home" / "plans" / "cluster-save-state.plan.json"
    ).read_text(encoding="utf-8"))
    calls = [
        command
        for step in plan["steps"]
        for command in (step.get("scripts") or [])
        if "run_cross_cluster_aggregates.py" in command
    ]
    assert calls == [
        "python core/scripts/run_cross_cluster_aggregates.py {project_root} --cluster {key}"
    ]

    command_doc = (
        ROOT / ".claude" / "commands" / "cluster-save-state.md"
    ).read_text(encoding="utf-8")
    lines = [line for line in command_doc.splitlines()
             if "run_cross_cluster_aggregates.py" in line]
    assert lines == [
        'python core/scripts/run_cross_cluster_aggregates.py "<项目路径>" --cluster <key>'
    ]


def test_wrapper_report_is_required_plan_output():
    plan = json.loads((
        ROOT / "core" / "claude-home" / "plans" / "cluster-save-state.plan.json"
    ).read_text(encoding="utf-8"))
    step = next(item for item in plan["steps"] if item["n"] == 11)
    assert (
        "_数据库/.cross_cluster_scan/cross_cluster_wrapper_latest.json"
        in step["expected_outputs"]
    )
