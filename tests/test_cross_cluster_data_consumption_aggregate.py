"""Cluster-native data-consumption scanner regression tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cross_cluster_data_consumption_aggregate as scanner  # noqa: E402


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _project(tmp_path: Path, count: int = 4) -> Path:
    database = tmp_path / "_数据库"
    database.mkdir()
    clusters = [
        {"cluster_id": f"cluster_{index:03d}", "status": "done", "title": f"块{index}"}
        for index in range(1, count + 1)
    ]
    _write(database / "故事块摘要.json", {"clusters": clusters})
    _write(database / "事件簇.json", {"clusters": clusters})
    for cluster in clusters:
        _write(database / ".wal" / f"{cluster['cluster_id']}_state_delta.json", {
            "cluster_id": cluster["cluster_id"],
            "fate_events_triggered": [],
            "heart_events_revealed": [],
        })
    return tmp_path


def _observations(project: Path):
    return scanner._observations(project, 10)


def test_extract_ids_accepts_string_and_object_entries():
    assert scanner._extract_ids([{"aspect_id": "A"}, "B", {"id": "C"}], "aspect_id", "id") == {"A", "B", "C"}


def test_aspect_streak_uses_cluster_records(tmp_path):
    project = _project(tmp_path, 3)
    _write(project / "_数据库" / "角色烙印.json", {"characters": {"陆参": {"active_aspects": [
        {"aspect_id": "scar", "acquired_at_cluster": "cluster_001", "label": "断指"}
    ]}}})
    findings = scanner.scan_aspect_continuity(project, _observations(project))
    assert findings[0]["code"] == "ASPECT_NOT_ADDRESSED"
    assert findings[0]["consecutive_clusters"] == ["cluster_001", "cluster_002", "cluster_003"]


def test_aspect_addressed_resets_cluster_streak(tmp_path):
    project = _project(tmp_path, 3)
    _write(project / "_数据库" / "角色烙印.json", {"characters": {"陆参": {"active_aspects": [
        {"aspect_id": "scar", "acquired_at_cluster": "cluster_001"}
    ]}}})
    ledger = json.loads((project / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
    ledger["clusters"][1]["aspects_addressed"] = ["scar"]
    _write(project / "_数据库" / "事件簇.json", ledger)
    assert scanner.scan_aspect_continuity(project, _observations(project)) == []


def test_urgent_clock_checks_last_two_clusters(tmp_path):
    project = _project(tmp_path, 3)
    _write(project / "_数据库" / "时钟表.json", {"clocks": [
        {"clock_id": "doom", "status": "active", "ticks": 5, "max": 6}
    ]})
    finding = scanner.scan_clock_addressing(project, _observations(project))[0]
    assert finding["code"] == "URGENT_CLOCK_IGNORED"
    assert finding["checked_clusters"] == ["cluster_002", "cluster_003"]


def test_heart_event_forgotten_uses_cluster_presence(tmp_path):
    project = _project(tmp_path, 4)
    _write(project / "_数据库" / "群像档.json", {"characters": {"周明": {"heart_events": [
        {"event_id": "HE_01", "consumed": True, "consumed_at_cluster": "cluster_001"}
    ]}}})
    summary = json.loads((project / "_数据库" / "故事块摘要.json").read_text(encoding="utf-8"))
    for cluster in summary["clusters"][1:]:
        cluster["characters"] = ["周明"]
    _write(project / "_数据库" / "故事块摘要.json", summary)
    finding = scanner.scan_heart_event_consistency(project, _observations(project))[0]
    assert finding["code"] == "HEART_EVENT_FORGOTTEN"
    assert finding["post_appearance_clusters"] == ["cluster_002", "cluster_003", "cluster_004"]


def test_fate_event_missing_from_state_delta_is_warning(tmp_path):
    project = _project(tmp_path, 1)
    _write(project / "_数据库" / "事件池.json", {
        "drawn_events_log": [{"cluster_id": "cluster_001", "event_id": "EV_01"}],
        "events": [{"event_id": "EV_01", "physical_evidence": ["断裂桥梁"]}],
    })
    finding = scanner.scan_fate_dice_consumption(project, _observations(project))[0]
    assert finding["code"] == "FATE_DICE_NOT_DECLARED"
    assert finding["cluster_id"] == "cluster_001"


def test_fate_event_low_evidence_is_advisory(tmp_path):
    project = _project(tmp_path, 1)
    _write(project / "_数据库" / "事件池.json", {
        "drawn_events_log": [{"cluster_id": "cluster_001", "event_id": "EV_01"}],
        "events": [{"event_id": "EV_01", "physical_evidence": ["断裂桥梁", "焦黑尸体"]}],
    })
    _write(project / "_数据库" / ".wal" / "cluster_001_state_delta.json", {
        "cluster_id": "cluster_001",
        "fate_events_triggered": [{"event_id": "EV_01", "evidence": "远处有异响"}],
        "heart_events_revealed": [],
    })
    finding = scanner.scan_fate_dice_consumption(project, _observations(project))[0]
    assert finding["code"] == "FATE_DICE_EVIDENCE_LOW"
    assert finding["severity"] == "advisory"


def test_fate_event_matching_evidence_is_clean(tmp_path):
    project = _project(tmp_path, 1)
    _write(project / "_数据库" / "事件池.json", {
        "drawn_events_log": [{"cluster_id": "cluster_001", "event_id": "EV_01"}],
        "events": [{"event_id": "EV_01", "physical_evidence": ["断裂桥梁", "焦黑尸体"]}],
    })
    _write(project / "_数据库" / ".wal" / "cluster_001_state_delta.json", {
        "cluster_id": "cluster_001",
        "fate_events_triggered": [{"event_id": "EV_01", "evidence": "断裂桥梁旁躺着焦黑尸体"}],
        "heart_events_revealed": [],
    })
    assert scanner.scan_fate_dice_consumption(project, _observations(project)) == []


def test_source_has_no_chapter_fallback_contract():
    source = (SCRIPTS / "cross_cluster_data_consumption_aggregate.py").read_text(encoding="utf-8")
    assert "IS_CLUSTER_MODE" not in source
    assert "CLUSTER_MODE" not in source
    assert "_changes" not in source
    assert 'project_root / "章节"' not in source
