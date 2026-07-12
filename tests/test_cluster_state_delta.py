import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import cluster_state_delta as module


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def payload(**updates) -> dict:
    value = {
        "cluster_id": "cluster_001",
        "time_advance": {"elapsed": "三小时", "period": "深夜", "key_events": ["抵达钟楼"]},
        "location_changes": [{"location_id": "L_TOWER", "new_status": "封锁"}],
        "hub_usage": [{"hub_id": "HUB_HOME", "role": "return", "scene_indices": [2]}],
        "fate_events_triggered": [{"event_id": "ME_001", "evidence": "冲突完成"}],
        "world_state_consumption": {
            "emergent_opportunities_consumed": ["EO_001"],
            "thread_responded": ["NT_001"],
        },
        "heart_events_revealed": [{"event_id": "HE_001", "evidence": "旧信件"}],
    }
    value.update(updates)
    return value


def project(root: Path, delta=None) -> Path:
    db = root / "_数据库"
    write(db / ".wal/cluster_001_state_delta.json", delta or payload())
    write(db / "时间线.json", {
        "current_time": {"cluster": "cluster_000", "period": "清晨"}, "time_log": []})
    write(db / "地图.json", {"locations": [{"id": "L_TOWER", "status": "开放"}]})
    write(db / "枢纽场景.json", {
        "hubs": [{"hub_id": "HUB_HOME"}], "cluster_hub_log": []})
    write(db / "大势卡.json", {"major_events": [{"id": "ME_001", "status": "pending"}]})
    write(db / "世界状态.json", {
        "active_npc_threads": [{"thread_id": "NT_001"}],
        "emergent_opportunities": [{"id": "EO_001"}],
    })
    write(db / "群像档.json", {"characters": {
        "阿青": {"heart_events": [{"event_id": "HE_001", "consumed": False}]}}})
    return root


def read(root: Path, name: str) -> dict:
    return json.loads((root / "_数据库" / name).read_text(encoding="utf-8"))


def test_schema_is_loaded_and_top_level_is_exact(tmp_path):
    root = project(tmp_path)
    _, loaded = module.load_delta(root, "1")
    assert loaded == payload()
    bad = payload(factual={})
    write(root / "_数据库/.wal/cluster_001_state_delta.json", bad)
    with pytest.raises(ValueError, match="未知字段"):
        module.load_delta(root, "1")


def test_apply_validates_then_updates_direct_domains(tmp_path):
    root = project(tmp_path)
    receipt = module.apply_delta(root, payload())
    assert receipt["applied_domains"] == ["时间线", "地图", "枢纽场景"]
    timeline = read(root, "时间线.json")
    assert timeline["current_time"] == {"cluster": "cluster_001", "period": "深夜"}
    assert timeline["time_log"] == [{
        "cluster_id": "cluster_001", "elapsed": "三小时", "period": "深夜",
        "key_events": ["抵达钟楼"]}]
    assert read(root, "地图.json")["locations"][0]["status"] == "封锁"
    assert read(root, "枢纽场景.json")["cluster_hub_log"][0]["cluster_id"] == "cluster_001"


def test_unknown_reference_fails_before_any_write(tmp_path):
    root = project(tmp_path)
    before = {name: (root / "_数据库" / name).read_bytes() for name in (
        "时间线.json", "地图.json", "枢纽场景.json")}
    bad = payload(hub_usage=[{"hub_id": "HUB_UNKNOWN", "role": "quest"}])
    with pytest.raises(ValueError, match="未知 hub_id"):
        module.apply_delta(root, bad)
    assert all((root / "_数据库" / name).read_bytes() == content
               for name, content in before.items())


def test_replay_replaces_cluster_rows_instead_of_duplicating(tmp_path):
    root = project(tmp_path)
    module.apply_delta(root, payload())
    second = payload(
        time_advance={"elapsed": "四小时", "period": "午夜", "key_events": []},
        hub_usage=[{"hub_id": "HUB_HOME", "role": "depart"}],
    )
    module.apply_delta(root, second)
    assert len(read(root, "时间线.json")["time_log"]) == 1
    assert read(root, "枢纽场景.json")["cluster_hub_log"] == [
        {"cluster_id": "cluster_001", "hub_id": "HUB_HOME", "role": "depart"}]
