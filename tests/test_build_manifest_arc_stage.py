"""character_arc_state 的 cluster-native manifest 合同。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import build_manifest as bm


ARC = {
    "schema_version": "2.0",
    "characters": {
        "陆建国": {
            "framework": "mckee_arc",
            "stages_by_cluster": {"cluster_001": "protect", "cluster_002": "reveal"},
            "current_stage_at_cluster": "cluster_001:protect",
            "stage_definitions": {
                "protect": {"name": "防御阶段", "description": "把 SOP 当成生存协议"},
                "reveal": {"name": "揭示阶段", "description": "开始直面真相"},
            },
        }
    },
}


def project(root: Path, arc=ARC) -> Path:
    db = root / "_数据库"
    db.mkdir()
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 4]},
        {"cluster_id": "cluster_002", "chapter_range": [5, 8]},
    ]}, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps({"characters": [
        {"id": "C_PROT", "name": "陆建国", "role": "主角"}
    ]}, ensure_ascii=False), encoding="utf-8")
    (db / "character_arc_state.json").write_text(json.dumps(arc, ensure_ascii=False), encoding="utf-8")
    return root


def test_cluster_stage_is_injected_without_full_schedule():
    with tempfile.TemporaryDirectory() as temp:
        scanner = bm.DatabaseScanner(project(Path(temp)), 6)
        result = bm._collect_main_character_arc_stage(scanner, 6)
        assert result["mode"] == "on"
        assert result["gate_level"] == "advisory"
        stage = result["main_characters"][0]
        assert stage["current_stage_id"] == "reveal"
        assert stage["current_stage_name"] == "揭示阶段"
        assert stage["_resolved_by"] == "stages_by_cluster"
        assert "stages_by_cluster" not in stage


def test_old_arc_shapes_are_rejected_not_compatibly_read():
    old_shapes = [
        {"arcs": {"陆建国": {"current_stage": "protect"}}},
        {"characters": [{"name": "陆建国", "obsolete_schedule": {"1": "protect"}}]},
    ]
    for old in old_shapes:
        with tempfile.TemporaryDirectory() as temp:
            scanner = bm.DatabaseScanner(project(Path(temp), old), 6)
            assert bm._collect_main_character_arc_stage(scanner, 6)["mode"] == "off"


def test_missing_or_unmapped_stage_is_off():
    with tempfile.TemporaryDirectory() as temp:
        scanner = bm.DatabaseScanner(project(Path(temp), {"characters": {}}), 6)
        assert bm._collect_main_character_arc_stage(scanner, 6)["mode"] == "off"
