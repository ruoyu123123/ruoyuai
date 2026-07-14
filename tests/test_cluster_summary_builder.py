"""cluster 摘要构建器的当前输入与输出合同。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import cluster_summary_builder as builder  # noqa: E402
import cluster_summary_store as store  # noqa: E402
import cross_cluster_structure_compliance_aggregate as structure_aggregate  # noqa: E402
from cluster_summary_reader import CLUSTER_FIELDS, load_summary  # noqa: E402
from cluster_summary_fixtures import cluster_record  # noqa: E402


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _prepare_project(tmp_path: Path, cluster_id: str = "cluster_001") -> Path:
    database = tmp_path / "_数据库"
    draft_dir = tmp_path / "章节" / f"{cluster_id}_draft"
    (database / ".wal").mkdir(parents=True)
    (database / ".audit").mkdir()
    (database / ".judge_reports").mkdir()
    draft_dir.mkdir(parents=True)
    draft = "青灯摇曳，林舟走进旧港。\n铁门后突然传来异常响动。\n夜色压下来。"
    (draft_dir / f"{cluster_id}_draft.txt").write_text(draft, encoding="utf-8")
    (draft_dir / f"{cluster_id}_changes.json").write_text(
        json.dumps({
            "self_eval": {
                "applied_style": {"ending_type": "场景硬收"},
                "waivers": [{"code": "STYLE_HINT", "reason": "场景需要"}],
                "moves_used": ["门前对峙"],
                "storyteller_alignment": {"actual_outcome": "进门"},
            }
        }, ensure_ascii=False), encoding="utf-8"
    )

    _write_json(database / "事件簇.json", {
        "clusters": [{
            "cluster_id": cluster_id,
            "narrative_mode": "linear",
            "scene_storyboard": [{"scene": 0}, {"scene": 1}],
            "throughline_progress": {"main": "进入旧港"},
        }]
    })
    _write_json(database / "beat_map.json", {
        "schema_version": "v27",
        "cluster_beats": {cluster_id: [{"beat": "Catalyst"}]},
    })
    _write_json(database / "地图.json", {
        "locations": [{"id": "LOC_HARBOR", "name": "旧港"},
                       {"id": "LOC_OTHER", "name": "白塔"}]
    })
    _write_json(database / "主角压力档.json", {
        "stress_log": [{"cluster_id": cluster_id, "total": 42, "trigger": "铁门"}]
    })
    _write_json(database / ".wal" / f"{cluster_id}_summary.json", {
        "cluster_id": cluster_id,
        "title": "旧港铁门",
        "summary": "林舟在旧港找到入口并承担新的风险。",
        "scene_summaries": ["抵达旧港", "发现铁门"],
        "key_details": ["青灯", "铁门"],
        "emotion": {"value": 0.7, "trend": "up"},
        "anchor_delivery": {"delivered": ["旧港"]},
        "appraisal_beats": [{"beat": "门前停顿"}],
    })
    _write_json(database / ".audit" / f"{cluster_id}_audit.json", {
        "cluster_id": cluster_id,
        "verdict": "pass",
        "summary": {"fatal": 0, "error": 0},
        "issues": [],
        "scanner_status": [{"name": "cluster", "status": "ok"}],
        "persona_drift": {"score": 0.0},
    })
    _write_json(database / ".wal" / f"{cluster_id}_archive.json", {
        "cluster_id": cluster_id,
        "characters": [{"id": "C_001", "name": "林舟"}],
        "relationships": [{"from": "林舟", "to": "旧港", "type": "调查"}],
        "items": [{"id": "I_001", "name": "铁钥匙"}],
        "locked_facts": ["旧港有第二道门"],
        "throughline_progress": {"main": "入口已确认"},
    })
    _write_json(database / ".wal" / f"{cluster_id}_state_delta.json", {
        "cluster_id": cluster_id,
        "time_advance": {"elapsed": "一小时", "period": "深夜"},
        "location_changes": [{"location_id": "LOC_HARBOR", "new_status": "封锁"}],
    })
    _write_json(database / ".wal" / f"{cluster_id}_entity_stats.json", {
        "cluster_id": cluster_id,
        "known_entities": [{"name": "林舟", "appears": True, "mention_count": 2}],
    })
    _write_json(database / ".judge_reports" / f"{cluster_id}_writer-truth-check.json", {
        "cluster_id": cluster_id,
        "verdict": "pass",
        "lie_count": 0,
        "detected_ending_type": "场景硬收",
        "ending_type_match": True,
        "body_cjk_count": 31,
    })
    _write_json(database / ".wal" / f"{cluster_id}_judge_reports_rollup.json", {
        "cluster_id": cluster_id,
        "reports": [{"judge_id": "audit-hub", "overall_grade": "A"}],
        "judge_score": 4.0,
        "judge_grade": "A",
        "waivers": [{"code": "STYLE_HINT", "reason": "场景需要"}],
    })
    store.initialize_summary(tmp_path)
    return tmp_path


def test_helpers_keep_cluster_semantics():
    assert builder._canonical_cluster_id(1) == "cluster_001"
    assert builder._ending_line("第一句\n\n最后一句") == "最后一句"
    assert "青灯" in builder._extract_keywords("青灯摇曳，青灯再次摇曳。")
    audit = builder._audit_rollup({
        "verdict": "warn",
        "summary": {"error": 1},
        "issues": [{"code": "X", "severity": "warn", "gate_level": "advisory"}],
        "persona_drift": {"score": 0.2},
    })
    assert audit["issues"][0]["code"] == "X"
    assert audit["persona_drift"]["score"] == 0.2
    assert builder._dedupe_waivers(
        [{"code": "X", "reason": "same"}],
        [{"code": "X", "reason": "same"}, {"code": "Y", "reason": "other"}],
    ) == [{"code": "X", "reason": "same"}, {"code": "Y", "reason": "other"}]


def test_truth_rollup_carries_ending_type_advisory():
    """G4①回归锁：writer_truth_check 的 ending_type_advisory 随 rollup 落库（非孤儿字段）。"""
    advisory = {"field": "applied_style.ending_type",
                "declared": "死兆留白钩", "actual": "场景硬收",
                "note": "作者自由文学标签"}
    rollup = builder._truth_rollup({
        "verdict": "pass",
        "ending_type_match": False,
        "ending_type_advisory": advisory,
        "detected_ending_type": "场景硬收",
    })
    assert rollup["ending_type_advisory"] == advisory
    assert rollup["ending_type_match"] is False
    # 无 advisory 时字段为 None（不缺键·下游可安全读取）
    assert builder._truth_rollup({"verdict": "pass"})["ending_type_advisory"] is None


def test_build_cluster_record_requires_all_cluster_artifacts(tmp_path):
    project = _prepare_project(tmp_path)
    record = builder.build_cluster_record(project, "001")
    assert set(record) == set(CLUSTER_FIELDS)
    assert record["cluster_id"] == "cluster_001"
    assert record["title"] == "旧港铁门"
    assert record["characters"] == ["林舟"]
    assert record["locations_mentioned"] == ["LOC_HARBOR"]
    assert record["state_delta"]["time_advance"]["period"] == "深夜"
    assert record["truth_check"]["verdict"] == "pass"
    assert record["audit"]["persona_drift"]["score"] == 0.0
    assert record["structure"]["beats_declared"] == ["Catalyst"]
    assert record["structure"]["beats_addressed"] == ["Catalyst"]
    assert record["structure"]["beat_signal_hit"] is True
    assert isinstance(record["pattern_metrics"], dict)

    (project / "_数据库" / ".wal" / "cluster_001_archive.json").unlink()
    with pytest.raises(builder.ClusterSummaryBuildError, match="必需产物不存在"):
        builder.build_cluster_record(project, "cluster_001")


def test_build_cluster_summary_persists_strict_record(tmp_path):
    project = _prepare_project(tmp_path)
    result = builder.build_cluster_summary(project, "cluster_001")
    assert result["ok"] is True
    loaded = load_summary(project)
    assert loaded["clusters"][0]["cluster_id"] == "cluster_001"
    assert "chapters" not in loaded["clusters"][0]


def test_structure_evidence_flows_from_draft_to_cross_cluster_scan(tmp_path):
    project = _prepare_project(tmp_path)
    builder.build_cluster_summary(project, "cluster_001")
    report = structure_aggregate.build_report(project)
    assert not any(
        finding["code"] == "BEAT_MISSED" for finding in report["findings"]
    )


def test_build_cluster_record_rejects_mismatched_artifact(tmp_path):
    project = _prepare_project(tmp_path)
    path = project / "_数据库" / ".wal" / "cluster_001_state_delta.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cluster_id"] = "cluster_002"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(builder.ClusterSummaryBuildError, match="state_delta.cluster_id"):
        builder.build_cluster_record(project, "cluster_001")
