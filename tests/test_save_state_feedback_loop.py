"""Cluster Hub 状态与 storyteller 自评消费合同。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import cluster_state_delta as csd
import narrator_calibrate as nc


def make_project(root: Path) -> Path:
    db = root / "_数据库"
    db.mkdir()
    (db / "枢纽场景.json").write_text(json.dumps({
        "hubs": [{"hub_id": "HUB_home"}, {"hub_id": "HUB_office"}],
        "cluster_hub_log": [],
    }), encoding="utf-8")
    (db / "时间线.json").write_text(json.dumps({
        "current_time": {}, "time_log": [],
    }), encoding="utf-8")
    (db / "地图.json").write_text(json.dumps({"locations": []}), encoding="utf-8")
    (db / "大势卡.json").write_text(json.dumps({"major_events": []}), encoding="utf-8")
    (db / "世界状态.json").write_text(json.dumps({
        "emergent_opportunities": [], "active_npc_threads": [],
    }), encoding="utf-8")
    (db / "群像档.json").write_text(json.dumps({"characters": {}}), encoding="utf-8")
    (db / "涟漪规则.json").write_text(json.dumps({"ripple_rules": []}), encoding="utf-8")
    return root


def delta(hub_id="HUB_home", role="return") -> dict:
    return {"cluster_id": "cluster_007", "time_advance": {}, "location_changes": [],
            "hub_usage": [{"hub_id": hub_id, "role": role}], "fate_events_triggered": [],
            "world_state_consumption": {"emergent_opportunities_consumed": [], "thread_responded": []},
            "heart_events_revealed": []}


def hub_log(project: Path) -> list:
    return json.loads((project / "_数据库" / "枢纽场景.json").read_text(encoding="utf-8"))["cluster_hub_log"]


def test_state_delta_writes_and_replaces_cluster_hub():
    with tempfile.TemporaryDirectory() as temp:
        project = make_project(Path(temp))
        csd.apply_delta(project, delta("HUB_office", "depart"))
        csd.apply_delta(project, delta("HUB_home", "return"))
        assert hub_log(project) == [{"cluster_id": "cluster_007", "hub_id": "HUB_home", "role": "return"}]


def test_unknown_hub_fails_hard():
    with tempfile.TemporaryDirectory() as temp:
        project = make_project(Path(temp))
        try:
            csd.apply_delta(project, delta("HUB_UNKNOWN", "quest"))
        except ValueError as exc:
            assert "未知 hub_id" in str(exc)
        else:
            raise AssertionError("未知 Hub 应硬停")


def test_writer_changes_schema_cannot_write_hub():
    with tempfile.TemporaryDirectory() as temp:
        project = make_project(Path(temp))
        schema = json.loads((ROOT / "core/claude-home/schemas/changes_schema.json").read_text(encoding="utf-8"))
        assert "factual" not in schema["properties"]


def test_storyteller_declared_outcome_still_consumed():
    changes = {"self_eval": {"storyteller_alignment": {"actual_outcome": "setback"}}}
    outcome, intensity, source = nc.infer_outcome(changes, "他成功突破了封锁。")
    assert outcome == "setback"
    assert intensity >= 1
    assert source == "writer_declared"
