"""Cluster 世界状态增量应用回归测试。"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import world_evolution_apply_cluster as apply_world  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _project(root: Path) -> Path:
    db = root / "_数据库"
    _write(db / "世界状态.json", {
        "current_world_time": {"cluster": "cluster_000", "day": 1},
        "factions_state": {"巡夜司": {"stability": 50}},
        "active_npc_threads": [{
            "thread_id": "NT_001",
            "npc_id": "审查组",
            "current_action": "暗中核查",
            "since_cluster": "cluster_001",
            "expected_complete_cluster": None,
            "expected_responses": 2,
            "responded_count": 0,
            "responded_by_cluster": [],
            "outcome_if_complete": "审查升级",
        }],
        "emergent_opportunities": [{
            "id": "EO_001",
            "trigger_cluster": "cluster_001",
            "expires_at_cluster": "cluster_002",
            "type": "副线",
            "description": "一封匿名信",
            "consumed_by_writer": False,
        }],
        "consequence_tracker": {},
    })
    _write(db / "涟漪规则.json", {"ripple_rules": [
        {
            "id": "RR_AUTO",
            "trigger_type": "auto_tick",
            "trigger_match": "every_cluster",
            "ripples": [{"target": "current_world_time.day", "advance": 1}],
        },
        {
            "id": "RR_ME_1",
            "trigger_type": "fate_event",
            "trigger_match": "ME_001",
            "ripples": [{"target": "factions_state.巡夜司.stability", "delta": -5}],
        },
    ]})
    _write(db / "大势卡.json", {"major_events": [
        {"id": "ME_001", "volume": 1, "status": "pending"},
    ]})
    return root


def _delta(cluster_id: str, *, events=None, opportunities=None, threads=None) -> dict:
    return {
        "cluster_id": cluster_id,
        "time_advance": {},
        "location_changes": [],
        "hub_usage": [],
        "fate_events_triggered": events or [],
        "world_state_consumption": {
            "emergent_opportunities_consumed": opportunities or [],
            "thread_responded": threads or [],
        },
        "heart_events_revealed": [],
    }


def _read(root: Path, name: str) -> dict:
    return json.loads((root / "_数据库" / name).read_text(encoding="utf-8"))


def test_cluster_apply_updates_all_world_domains_and_me_projection():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        result = apply_world.apply_cluster(root, "cluster_001", _delta(
            "cluster_001",
            events=[{"event_id": "ME_001", "evidence": "审查现场被公开"}],
            opportunities=["EO_001"],
            threads=["NT_001"],
        ))
        assert result["tick"]["matched_rules"] == ["RR_AUTO"]
        assert result["fate_events"][0]["matched_rules"] == ["RR_ME_1"]
        assert result["world_state_consumption"]["opportunities"]["consumed"] == ["EO_001"]
        assert result["world_state_consumption"]["threads"]["responded"] == ["NT_001"]

        world = _read(root, "世界状态.json")
        assert world["current_world_time"] == {"cluster": "cluster_001", "day": 2}
        assert world["factions_state"]["巡夜司"]["stability"] == 45
        assert world["emergent_opportunities"][0]["consumed_at_cluster"] == "cluster_001"
        thread = world["active_npc_threads"][0]
        assert thread["responded_count"] == 1
        assert thread["responded_by_cluster"] == ["cluster_001"]

        event = _read(root, "大势卡.json")["major_events"][0]
        assert event["status"] == "completed"
        assert event["completed_at_cluster"] == "cluster_001"
        assert event["completion_evidence"] == "审查现场被公开"


def test_same_delta_replay_is_idempotent():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        delta = _delta(
            "cluster_001",
            events=[{"event_id": "ME_001", "evidence": "审查现场被公开"}],
            opportunities=["EO_001"],
            threads=["NT_001"],
        )
        apply_world.apply_cluster(root, "cluster_001", delta)
        second = apply_world.apply_cluster(root, "cluster_001", delta)
        assert second["skipped_idempotent"] is True
        world = _read(root, "世界状态.json")
        assert world["current_world_time"]["day"] == 2
        assert world["factions_state"]["巡夜司"]["stability"] == 45
        assert world["active_npc_threads"][0]["responded_count"] == 1


def test_same_cluster_rejects_changed_delta():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        apply_world.apply_cluster(root, "cluster_001", _delta("cluster_001"))
        with pytest.raises(ValueError, match="不同 state delta"):
            apply_world.apply_cluster(
                root, "cluster_001", _delta("cluster_001", threads=["NT_001"]))


def test_thread_completes_after_expected_cluster_responses():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        apply_world.apply_cluster(
            root, "cluster_001", _delta("cluster_001", threads=["NT_001"]))
        apply_world.apply_cluster(
            root, "cluster_002", _delta("cluster_002", threads=["NT_001"]))
        world = _read(root, "世界状态.json")
        assert world["active_npc_threads"] == []
        consequence = world["consequence_tracker"]["cluster_002_thread_responded_NT_001"]
        assert consequence["outcome"] == "审查升级"
        assert consequence["added_at_cluster"] == "cluster_002"


@pytest.mark.parametrize(
    ("delta", "message"),
    [
        (_delta("cluster_001", opportunities=["EO_UNKNOWN"]), "未知机缘"),
        (_delta("cluster_001", threads=["NT_UNKNOWN"]), "未知 NPC thread"),
        (_delta("cluster_001", events=[{"event_id": "ME_UNKNOWN"}]), "未知 ME"),
    ],
)
def test_missing_state_references_fail_before_write(delta, message):
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        world_path = root / "_数据库" / "世界状态.json"
        before = world_path.read_bytes()
        with pytest.raises(ValueError, match=message):
            apply_world.apply_cluster(root, "cluster_001", delta)
        assert world_path.read_bytes() == before


def test_me_without_fate_rule_fails_before_write():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        rules_path = root / "_数据库" / "涟漪规则.json"
        rules = _read(root, "涟漪规则.json")
        rules["ripple_rules"] = [rules["ripple_rules"][0]]
        _write(rules_path, rules)
        before = (root / "_数据库" / "世界状态.json").read_bytes()
        with pytest.raises(ValueError, match="没有 fate_event"):
            apply_world.apply_cluster(
                root, "cluster_001",
                _delta("cluster_001", events=[{"event_id": "ME_001"}]),
            )
        assert (root / "_数据库" / "世界状态.json").read_bytes() == before


def test_expired_opportunity_cannot_be_consumed():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        with pytest.raises(ValueError, match="有效窗口"):
            apply_world.apply_cluster(
                root, "cluster_003",
                _delta("cluster_003", opportunities=["EO_001"]),
            )
