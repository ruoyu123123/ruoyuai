"""故事块 Judge consensus 决策与 required 失败语义测试。"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import maybe_judge_consensus as module  # noqa: E402


def _project(root: Path) -> Path:
    (root / "_数据库" / ".judge_reports").mkdir(parents=True)
    return root


def _report(project: Path, name: str, payload=None) -> Path:
    path = project / "_数据库" / ".judge_reports" / name
    path.write_text(json.dumps(payload or {"verdict": "pass"}), encoding="utf-8")
    return path


def _decision(project: Path, cluster_id="cluster_001") -> dict:
    path = project / "_数据库" / ".judge_reports" / f"{cluster_id}_consensus_decision.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_reports_writes_not_required_receipt(tmp_path):
    project = _project(tmp_path)
    assert module.run_cluster(project, "1") == 0
    decision = _decision(project)
    assert decision["status"] == "not_required"
    assert decision["required_substep_executed"] is True
    assert decision["input_count"] == 0


def test_one_report_is_not_required(tmp_path):
    project = _project(tmp_path)
    _report(project, "cluster_002_voice-checker.json")
    assert module.run_cluster(project, "cluster_002") == 0
    assert _decision(project, "cluster_002")["input_count"] == 1


def test_report_collection_excludes_prior_consensus_artifacts(tmp_path):
    project = _project(tmp_path)
    _report(project, "cluster_001_voice-checker.json")
    _report(project, "cluster_001_consensus.json")
    _report(project, "cluster_001_consensus_decision.json")
    assert [path.name for path in module._judge_reports(project, "cluster_001")] == [
        "cluster_001_voice-checker.json"
    ]


def test_two_reports_merge_once_per_cluster(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _report(project, "cluster_003_voice-checker.json")
    _report(project, "cluster_003_foreshadower.json")
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "judge_consensus.py").write_text("# test", encoding="utf-8")
    monkeypatch.setattr(module, "scripts_dir", lambda: script_dir)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({"consensus": "pass"}), stderr=""
    ))
    assert module.run_cluster(project, "003") == 0
    decision = _decision(project, "cluster_003")
    assert decision["status"] == "merged" and decision["input_count"] == 2
    consensus = project / "_数据库" / ".judge_reports" / "cluster_003_consensus.json"
    assert json.loads(consensus.read_text(encoding="utf-8")) == {"consensus": "pass"}


def test_missing_consensus_script_is_required_failure(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _report(project, "cluster_001_a.json")
    _report(project, "cluster_001_b.json")
    monkeypatch.setattr(module, "scripts_dir", lambda: tmp_path / "missing")
    assert module.run_cluster(project, "1") == 2


def test_bad_subprocess_output_is_required_failure(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _report(project, "cluster_001_a.json")
    _report(project, "cluster_001_b.json")
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "judge_consensus.py").write_text("# test", encoding="utf-8")
    monkeypatch.setattr(module, "scripts_dir", lambda: script_dir)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="not json", stderr=""
    ))
    assert module.run_cluster(project, "1") == 2


def test_timeout_is_required_failure(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _report(project, "cluster_001_a.json")
    _report(project, "cluster_001_b.json")
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "judge_consensus.py").write_text("# test", encoding="utf-8")
    monkeypatch.setattr(module, "scripts_dir", lambda: script_dir)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)),
    )
    assert module.run_cluster(project, "1") == 2
