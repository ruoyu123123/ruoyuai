"""cluster 状态更新 wrapper 合同。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import save_state_updates as module  # noqa: E402


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


def test_run_updates_executes_each_module_once_per_cluster(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for _, name in module.SUB_MODULES:
        _fake_script(scripts, name)
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    receipt = module.run_updates(project, "001")
    assert receipt["cluster_id"] == "cluster_001"
    assert [item["name"] for item in receipt["modules"]] == ["offscreen", "character_arc"]
    for item in receipt["modules"]:
        argv = json.loads(item["stdout_tail"])["argv"]
        assert argv == [str(project), "--cluster", "cluster_001"]
    saved = json.loads(
        (project / "_数据库" / ".wal" / "cluster_001_state_updates_receipt.json")
        .read_text(encoding="utf-8")
    )
    assert saved == receipt


def test_missing_module_is_required_failure(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    with pytest.raises(RuntimeError, match="脚本不存在"):
        module.run_updates(project, "cluster_001")
    assert not (project / "_数据库" / ".wal" / "cluster_001_state_updates_receipt.json").exists()


def test_nonzero_update_is_required_failure(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _fake_script(scripts, "offscreen_update.py", exit_code=1)
    _fake_script(scripts, "character_arc_update.py")
    monkeypatch.setattr(module, "SCRIPT_DIR", scripts)
    project = _project(tmp_path / "project")
    with pytest.raises(RuntimeError, match="offscreen"):
        module.run_updates(project, "cluster_001")


def test_source_has_no_physical_chapter_dispatch():
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("chapter_range", "representative_ch", "run_updates_for_chapter", "--all", "--only"):
        assert forbidden not in source
