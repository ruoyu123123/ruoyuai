"""大势引擎的 cluster-native 单一合同回归测试。"""

import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fate_engine  # noqa: E402


FATE = {
    "story_destiny": {"final_image": "天台上的最后一次对峙"},
    "major_events": [
        {
            "id": "ME-V1-01",
            "title": "导火索",
            "stage": "opening",
            "status": "completed",
            "completed_at_cluster": "cluster_001",
            "prerequisites": [],
            "expected_window_after": None,
        },
        {
            "id": "ME-V1-02",
            "title": "第一次反击",
            "stage": "escalation",
            "status": "pending",
            "prerequisites": ["ME-V1-01"],
            "expected_window_after": {"event": "ME-V1-01", "max_clusters": 2},
            "trigger_when": "主角取得可验证证据",
            "physical_evidence": ["账本"],
            "downstream_unlocks": ["ME-V1-03"],
        },
        {
            "id": "ME-V1-03",
            "title": "阶段决战",
            "stage": "finale",
            "status": "pending",
            "prerequisites": ["ME-V1-02"],
            "expected_window_after": {"event": "ME-V1-02", "max_clusters": 1},
        },
    ],
}


def _project(root: Path, fate: dict = FATE) -> Path:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(
        json.dumps(fate, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_events_accepts_only_major_events_with_id():
    assert [event["id"] for event in fate_engine._events(FATE)] == [
        "ME-V1-01", "ME-V1-02", "ME-V1-03"
    ]
    with pytest.raises(ValueError, match="major_events"):
        fate_engine._events({"events": FATE["major_events"]})
    missing_id = copy.deepcopy(FATE)
    missing_id["major_events"][0].pop("id")
    with pytest.raises(ValueError, match="缺少 id"):
        fate_engine._events(missing_id)


@pytest.mark.parametrize("bad", [None, "placeholder", 7])
def test_events_rejects_non_object_entries(bad):
    fate = copy.deepcopy(FATE)
    fate["major_events"].append(bad)
    with pytest.raises(ValueError, match="只能包含 object"):
        fate_engine._events(fate)


def test_events_rejects_old_status_and_duplicate_id():
    old_status = copy.deepcopy(FATE)
    old_status["major_events"][1]["status"] = "scheduled"
    with pytest.raises(ValueError, match="pending 或 completed"):
        fate_engine._events(old_status)
    duplicate = copy.deepcopy(FATE)
    duplicate["major_events"][1]["id"] = "ME-V1-01"
    with pytest.raises(ValueError, match="重复"):
        fate_engine._events(duplicate)


def test_evaluate_uses_cluster_distance_for_priority_and_overdue():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp))
        normal = fate_engine.evaluate(project, "2")
        assert normal["cluster_id"] == "cluster_002"
        assert normal["active_fate_events"][0]["id"] == "ME-V1-02"
        assert normal["active_fate_events"][0]["priority"] == 5
        assert normal["overdue_events"] == []

        near_end = fate_engine.evaluate(project, "cluster_003")
        assert near_end["active_fate_events"][0]["priority"] == 8

        overdue = fate_engine.evaluate(project, "cluster_004")
        assert overdue["active_fate_events"][0]["priority"] == 10
        assert overdue["overdue_events"][0]["overdue_by_clusters"] == 1


def test_drift_returns_only_canonical_cluster_metrics():
    with tempfile.TemporaryDirectory() as temp:
        result = fate_engine.drift(_project(Path(temp)), "cluster_005")
    assert result["cluster_id"] == "cluster_005"
    assert result["overdue_count"] == 1
    metric = result["overdue_events"][0]
    assert metric == {
        "event_id": "ME-V1-02",
        "title": "第一次反击",
        "prereq": "ME-V1-01",
        "prereq_completed_at_cluster": "cluster_001",
        "current_cluster": "cluster_005",
        "gap_clusters": 4,
        "max_clusters": 2,
        "overdue_by_clusters": 2,
    }


def test_old_window_fields_are_rejected():
    fate = copy.deepcopy(FATE)
    fate["major_events"][1]["expected_window_after"] = {
        "event": "ME-V1-01", "window": 5
    }
    with tempfile.TemporaryDirectory() as temp:
        with pytest.raises(ValueError, match="max_clusters"):
            fate_engine.evaluate(_project(Path(temp), fate), "cluster_002")


def test_completed_window_anchor_requires_completed_at_cluster():
    fate = copy.deepcopy(FATE)
    fate["major_events"][0].pop("completed_at_cluster")
    with tempfile.TemporaryDirectory() as temp:
        with pytest.raises(ValueError, match="非法 cluster_id"):
            fate_engine.evaluate(_project(Path(temp), fate), "cluster_002")


def test_evaluate_and_drift_are_read_only():
    with tempfile.TemporaryDirectory() as temp:
        project = _project(Path(temp))
        path = project / "_数据库" / "大势卡.json"
        before = path.read_bytes()
        fate_engine.evaluate(project, "cluster_004")
        fate_engine.drift(project, "cluster_004")
        assert path.read_bytes() == before


def test_dashboard_uses_canonical_pool():
    with tempfile.TemporaryDirectory() as temp:
        result = fate_engine.dashboard(_project(Path(temp)))
    assert result["total_events"] == 3
    assert result["by_status"] == {"completed": 1, "pending": 2}
    assert result["by_stage"] == {"opening": 1, "escalation": 1, "finale": 1}
