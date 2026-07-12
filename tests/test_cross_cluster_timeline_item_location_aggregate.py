"""cluster 时间、道具、地点连续性顾问合同。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cross_cluster_timeline_item_location_aggregate as module  # noqa: E402


def _record(cluster_id: str, *, period: str = "", elapsed: str = "",
            items=None, draft: str = "") -> dict:
    return {
        "cluster_id": cluster_id,
        "summary": {"cluster_id": cluster_id},
        "delta": {
            "cluster_id": cluster_id,
            "time_advance": {key: value for key, value in {
                "period": period, "elapsed": elapsed,
            }.items() if value},
        },
        "archive": {"cluster_id": cluster_id, "items": list(items or [])},
        "draft": draft,
    }


def _write_db(root: Path, name: str, value: dict) -> None:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_timeline_frozen_uses_cluster_sequence():
    records = [_record(f"cluster_{index:03d}", period="周一晚") for index in range(1, 7)]
    findings = module.scan_timeline(records)
    frozen = [item for item in findings if item["code"] == "TIME_FROZEN"]
    assert frozen[0]["consecutive_clusters"] == [
        "cluster_001", "cluster_002", "cluster_003",
        "cluster_004", "cluster_005", "cluster_006",
    ]


def test_timeline_gap_requires_elapsed_explanation():
    records = [
        _record("cluster_001", period="周二下午"),
        _record("cluster_002", period="周五清晨"),
    ]
    assert module.scan_timeline(records)[0]["code"] == "TIME_GAP_UNEXPLAINED"
    records[1]["delta"]["time_advance"]["elapsed"] = "三天"
    assert module.scan_timeline(records) == []


def test_item_duplicate_holder_is_cluster_advisory(tmp_path):
    _write_db(tmp_path, "道具.json", {"items": [{"id": "I_KEY"}]})
    records = [_record("cluster_001", items=[
        {"id": "I_KEY", "holder": "C_A"},
        {"id": "I_KEY", "holder": "C_B"},
    ])]
    finding = module.scan_item_chain(tmp_path, records)[0]
    assert finding["code"] == "ITEM_DUPLICATE_HOLDER"
    assert finding["cluster_id"] == "cluster_001"
    assert finding["severity"] == "advisory"


def test_item_abandoned_uses_ten_cluster_archives(tmp_path):
    _write_db(tmp_path, "道具.json", {"items": [{"id": "I_KEY"}]})
    records = [_record(f"cluster_{index:03d}") for index in range(1, 11)]
    findings = module.scan_item_chain(tmp_path, records)
    assert findings == [{
        "severity": "advisory",
        "code": "ITEM_ABANDONED",
        "item_id": "I_KEY",
        "clusters_without_state_change": 10,
        "suggestion": "该道具在最近 cluster archive 中没有状态记录，请确认是否已退出叙事",
    }]


def test_location_distribution_reads_whole_cluster_draft(tmp_path):
    _write_db(tmp_path, "地图.json", {"locations": [
        {"id": "L_CITY", "name": "青云城"},
        {"id": "L_VALLEY", "name": "落霞谷"},
        {"id": "L_CAVE", "name": "幽冥窟"},
        {"id": "L_TOWER", "name": "天机阁"},
        {"id": "L_FIELD", "name": "枯骨原"},
    ]})
    _write_db(tmp_path, "枢纽场景.json", {"hubs": []})
    records = [
        _record(f"cluster_{index:03d}", draft="青云城的雨。" if index <= 4 else "荒野。")
        for index in range(1, 6)
    ]
    findings = module.scan_location_distribution(tmp_path, records)
    over = next(item for item in findings if item["code"] == "LOCATION_OVERFREQ")
    never = next(item for item in findings if item["code"] == "LOCATION_NEVER_VISITED")
    rhythm = next(item for item in findings if item["code"] == "LOCATION_RHYTHM_BROKEN")
    assert over["appearance_clusters"] == 4
    assert never["total_unused"] == 4
    assert rhythm["consecutive_clusters"] == [
        "cluster_001", "cluster_002", "cluster_003", "cluster_004",
    ]


def test_load_records_requires_cluster_artifacts(tmp_path):
    _write_db(tmp_path, "故事块摘要.json", {
        "clusters": [{"cluster_id": "cluster_001", "status": "completed"}],
    })
    with pytest.raises(ValueError, match="state delta"):
        module.load_records(tmp_path, 10)


def test_write_report_uses_cluster_fields(tmp_path):
    records = [_record("cluster_001")]
    output = module.write_report(tmp_path, records, [])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["clusters_scanned"] == ["cluster_001"]
    assert "chapters_scanned" not in report


def test_source_has_no_chapter_or_mode_fallback():
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("IS_CLUSTER_MODE", "CLUSTER_MODE", "read_changes", "get_chapters", "changes.factual"):
        assert forbidden not in source
