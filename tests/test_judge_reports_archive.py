"""cluster JudgeReport 归档器的当前合同。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import judge_reports_archive as archive  # noqa: E402


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _prepare_project(tmp_path: Path, cluster_id: str = "cluster_001") -> Path:
    db = tmp_path / "_数据库"
    draft = tmp_path / "章节" / f"{cluster_id}_draft"
    (db / ".audit").mkdir(parents=True)
    (db / ".wal").mkdir()
    (db / ".judge_reports").mkdir()
    draft.mkdir(parents=True)
    (draft / f"{cluster_id}_changes.json").write_text(
        json.dumps({
            "self_eval": {
                "applied_style": {"applied_rules": ["短句"]},
                "waivers": [{"code": "W1", "reason": "场景需要"}],
            }
        }, ensure_ascii=False), encoding="utf-8"
    )
    _write_json(db / ".audit" / f"{cluster_id}_audit.json", {
        "cluster_id": cluster_id,
        "verdict": "pass",
        "summary": {"fatal": 0, "error": 0},
        "issues": [],
    })
    _write_json(db / ".wal" / f"{cluster_id}_summary.json", {
        "cluster_id": cluster_id,
        "scene_summaries": ["抵达"],
        "key_details": ["铁门"],
        "emotion": {"value": 0.4},
    })
    _write_json(db / ".wal" / f"{cluster_id}_reflection.json", {
        "cluster_id": cluster_id,
        "entries": [
            {"category": "success", "id": "S1"},
            {"category": "failure", "id": "F1"},
        ],
        "note": "保留门前停顿。",
    })
    _write_json(db / ".judge_reports" / f"{cluster_id}_writer-truth-check.json", {
        "schema_version": "1.0.cluster",
        "judge_id": "writer-truth-check",
        "cluster_id": cluster_id,
        "overall_grade": "A",
        "confidence": 1.0,
        "verdict": "pass",
        "waivers": [],
    })
    _write_json(db / ".judge_reports" / f"{cluster_id}_foreshadower.json", {
        "schema_version": "1.0.cluster",
        "judge_id": "foreshadower",
        "cluster_id": cluster_id,
        "overall_grade": "B",
        "confidence": 0.8,
        "verdict": "advisory",
        "waivers": [{"code": "W1", "reason": "场景需要"}],
    })
    _write_json(db / ".judge_reports" / f"{cluster_id}_style.json", {
        "schema_version": "1.0.cluster",
        "judge_id": "style",
        "cluster_id": cluster_id,
        "overall_grade": "B",
        "confidence": 0.7,
        "verdict": "warn",
        "waivers": [],
    })
    return tmp_path


def test_grade_and_score_are_cluster_level():
    assert archive._audit_grade({"summary": {"fatal": 0, "error": 0}}) == "A"
    assert archive._audit_grade({"summary": {"fatal": 0, "error": 1}}) == "B"
    assert archive._audit_grade({"summary": {"fatal": 0, "error": 3}}) == "C"
    assert archive._audit_grade({"summary": {"fatal": 1, "error": 0}}) == "D"
    assert archive._judge_score([{"overall_grade": "A"}, {"overall_grade": "C"}]) == (3.0, "B")
    assert archive._judge_score([{"overall_grade": "N/A"}]) == (None, None)


def test_build_audit_report_keeps_only_cluster_evidence():
    report = archive.build_audit_report({
        "verdict": "warn",
        "summary": {"fatal": 0, "error": 1},
        "issues": [{"code": "X", "evidence": "门未锁"}, {"code": "Y"}],
    }, "cluster_003")
    assert report["judge_id"] == "audit-hub"
    assert report["cluster_id"] == "cluster_003"
    assert report["overall_grade"] == "B"
    assert report["specific_findings"]["issue_codes"] == ["X", "Y"]
    assert report["evidence_quotes"] == [{"code": "X", "quote": "门未锁"}]


def test_build_rollup_requires_all_required_cluster_sources(tmp_path):
    project = _prepare_project(tmp_path)
    rollup, audit_report, output = archive.build_rollup(project, "001")
    assert rollup["cluster_id"] == "cluster_001"
    assert rollup["schema_version"] == "cluster-judge-rollup.v1"
    assert rollup["judge_grade"] == "A"
    assert {row["signal_id"] for row in rollup["signals"]} == {
        "writer-self-eval", "summarizer", "reflector"
    }
    assert {waiver["code"] for waiver in rollup["waivers"]} == {"W1"}
    assert audit_report["overall_grade"] == "A"
    assert output.name == "cluster_001_judge_reports_rollup.json"

    (project / "_数据库" / ".wal" / "cluster_001_reflection.json").unlink()
    with pytest.raises(archive.JudgeArchiveError, match="必需来源不存在"):
        archive.build_rollup(project, "cluster_001")


def test_build_rollup_rejects_source_without_cluster_id(tmp_path):
    project = _prepare_project(tmp_path)
    path = project / "_数据库" / ".wal" / "cluster_001_summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("cluster_id")
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(archive.JudgeArchiveError, match="summary.cluster_id 缺失"):
        archive.build_rollup(project, "cluster_001")


def test_archive_cluster_writes_audit_and_rollup_atomically(tmp_path):
    project = _prepare_project(tmp_path)
    result = archive.archive_cluster(project, "cluster_001")
    db = project / "_数据库"
    assert result["cluster_id"] == "cluster_001"
    assert (db / ".judge_reports" / "cluster_001_audit-hub.json").is_file()
    assert (db / ".wal" / "cluster_001_judge_reports_rollup.json").is_file()


def test_dry_run_and_cli_use_only_cluster_argument(tmp_path):
    project = _prepare_project(tmp_path)
    archive.archive_cluster(project, "cluster_001", dry_run=True)
    db = project / "_数据库"
    assert not (db / ".judge_reports" / "cluster_001_audit-hub.json").exists()
    assert archive.main([str(project), "--cluster", "001", "--dry-run"]) == 0
