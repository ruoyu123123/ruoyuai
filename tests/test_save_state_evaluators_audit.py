"""cluster 状态 evaluator wrapper 合同。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import save_state_evaluators as module  # noqa: E402


def _fake_script(directory: Path, name: str, exit_code: int = 0) -> None:
    (directory / name).write_text(
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )


def _project(root: Path) -> Path:
    (root / "_数据库").mkdir(parents=True)
    return root


def test_runs_all_evaluators_once_and_records_advisory(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name, script, _ in module.EVALUATORS:
        _fake_script(scripts, script, exit_code=1 if name == "stress" else 0)
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    receipt = module.run_evaluators(project, "cluster_001")
    assert receipt["advisories"] == ["stress"]
    assert len(receipt["evaluators"]) == 4
    clock = next(item for item in receipt["evaluators"] if item["name"] == "clock")
    assert json.loads(clock["stdout_tail"])["argv"] == [
        str(project), "tick-cluster-end", "--cluster", "cluster_001"
    ]
    saved = json.loads(
        (project / "_数据库" / ".wal" / "cluster_001_state_evaluators_receipt.json")
        .read_text(encoding="utf-8")
    )
    assert saved == receipt


def test_exit_two_is_contract_failure(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name, script, _ in module.EVALUATORS:
        _fake_script(scripts, script, exit_code=2 if name == "relationship" else 0)
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    with pytest.raises(RuntimeError, match="relationship"):
        module.run_evaluators(project, "cluster_001")
    assert not (project / "_数据库" / ".wal" / "cluster_001_state_evaluators_receipt.json").exists()


def test_missing_evaluator_is_required_failure(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    with pytest.raises(RuntimeError, match="脚本不存在"):
        module.run_evaluators(project, "cluster_001")


def test_source_has_no_physical_chapter_dispatch_or_optional_skip():
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("chapter_range", "run_evaluators_for_chapter", "[SKIP]", "--all", "--only"):
        assert forbidden not in source
