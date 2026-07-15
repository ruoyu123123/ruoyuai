"""Cluster 世界涟漪规则引擎回归测试。"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import world_evolution_engine as engine  # noqa: E402


def _world() -> dict:
    return {
        "current_world_time": {"cluster": "cluster_001", "day": 1},
        "protagonist_state": {},
        "factions_state": {"巡夜司": {"power": 60, "stability": 50, "wealth": 95}},
        "active_npc_threads": [],
        "emergent_opportunities": [],
        "consequence_tracker": {},
    }


def _rules() -> dict:
    return {
        "ripple_rules": [
            {
                "id": "RR_MINOR",
                "trigger_type": "minor_event",
                "trigger_match": "夜探据点|铁锈味",
                "ripples": [
                    {"target": "factions_state.巡夜司.power", "delta": -10},
                    {"target": "factions_state.巡夜司.wealth", "delta": 20},
                ],
            },
            {
                "id": "RR_NARRATIVE",
                "trigger_type": "minor_event",
                "trigger_match": "心理余波",
                "ripples": [{"narrative": "巡夜司内部开始互相猜忌"}],
            },
            {
                "id": "RR_FATE",
                "trigger_type": "fate_event",
                "trigger_match": "ME_001",
                "ripples": [{"target": "factions_state.巡夜司.stability", "delta": -5}],
            },
            {
                "id": "RR_AUTO",
                "trigger_type": "auto_tick",
                "trigger_match": "every_cluster",
                "ripples": [{"target": "current_world_time.day", "advance": 1}],
            },
        ]
    }


def _project(root: Path) -> Path:
    db = root / "_数据库"
    db.mkdir(parents=True)
    (db / "世界状态.json").write_text(
        json.dumps(_world(), ensure_ascii=False), encoding="utf-8")
    (db / "涟漪规则.json").write_text(
        json.dumps(_rules(), ensure_ascii=False), encoding="utf-8")
    return root


def _disk_world(root: Path) -> dict:
    return json.loads((root / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))


def test_minor_event_applies_numeric_delta_and_clamp():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        result = engine.apply_minor_event(root, "cluster_005", "夜探据点")
        assert result["matched_rules"] == ["RR_MINOR"]
        world = _disk_world(root)
        assert world["factions_state"]["巡夜司"]["power"] == 50
        assert world["factions_state"]["巡夜司"]["wealth"] == 100


def test_minor_event_same_cluster_is_idempotent():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        engine.apply_minor_event(root, "cluster_005", "夜探据点")
        second = engine.apply_minor_event(root, "cluster_005", "夜探据点")
        assert second["skipped_idempotent"] is True
        assert _disk_world(root)["factions_state"]["巡夜司"]["power"] == 50


def test_minor_event_rejects_different_choice_for_applied_cluster():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        engine.apply_minor_event(root, "cluster_005", "夜探据点")
        with pytest.raises(ValueError, match="禁止覆盖"):
            engine.apply_minor_event(root, "cluster_005", "心理余波")


def test_minor_event_different_clusters_accumulates():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        engine.apply_minor_event(root, "cluster_005", "夜探据点")
        engine.apply_minor_event(root, "cluster_006", "夜探据点")
        assert _disk_world(root)["factions_state"]["巡夜司"]["power"] == 40


def test_unmatched_minor_event_does_not_write_world():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        before = (root / "_数据库" / "世界状态.json").read_bytes()
        result = engine.apply_minor_event(root, "cluster_005", "无关走向")
        assert result["matched_rules"] == []
        assert (root / "_数据库" / "世界状态.json").read_bytes() == before


def test_narrative_consequence_is_cluster_scoped_and_deduplicated():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        engine.apply_minor_event(root, "cluster_005", "心理余波")
        engine.apply_minor_event(root, "cluster_005", "心理余波")
        engine.apply_minor_event(root, "cluster_006", "心理余波")
        rows = _disk_world(root)["narrative_consequences"]
        assert [row["cluster_id"] for row in rows] == ["cluster_005", "cluster_006"]
        assert all(row["_kind"] == "narrative" for row in rows)


def test_advance_is_unbounded_and_null_starts_at_zero():
    world = {"current_world_time": {"day": 99}}
    log = []
    assert engine._apply_ripple(
        world, {"target": "current_world_time.day", "advance": 6},
        "cluster_001", log)
    assert world["current_world_time"]["day"] == 105
    world["current_world_time"]["day"] = None
    engine._apply_ripple(
        world, {"target": "current_world_time.day", "advance": 3},
        "cluster_002", log)
    assert world["current_world_time"]["day"] == 3


def test_missing_ripple_path_is_reported_without_mutation():
    world = _world()
    log = []
    assert not engine._apply_ripple(
        world, {"target": "missing.value", "advance": 1},
        "cluster_001", log)
    assert log == [{"target": "missing.value", "op": "advance", "result": "skip_path_missing"}]


def test_noncanonical_rule_shapes_fail_hard():
    for rules in (
        {"rules": []},
        {"ripple_rules": [{"id": "RR", "trigger_type": "minor_event",
                            "trigger_match": "x", "ripples": "叙事字符串"}]},
        {"ripple_rules": [{"rule_id": "RR", "trigger": "x", "effect": "旧格式"}]},
    ):
        with pytest.raises(ValueError):
            engine._canonical_rules(rules)


def test_spawned_opportunity_uses_cluster_window():
    world = _world()
    log = []
    engine._apply_ripple(
        world,
        {"target": "emergent_opportunities", "spawn": {
            "type": "副线", "description": "一封匿名信", "expires_clusters": 3,
        }},
        "cluster_004",
        log,
    )
    opportunity = world["emergent_opportunities"][0]
    assert opportunity["trigger_cluster"] == "cluster_005"
    assert opportunity["expires_at_cluster"] == "cluster_007"
    assert engine.opportunity_window(opportunity, "cluster_006") == (True, 1)
    assert engine.opportunity_window(opportunity, "cluster_008") == (False, -1)


# ───────── producer 契约静态校验（单一真理源 anti-drift 回归锁·2026-07-15 衔石与朝云实证）─────────
def test_apply_ripple_rejects_op_note_shape_with_canonical_hint():
    """真机原样 {op/target/note} 形态 → 入口 ValueError 且报错含 canonical 修法提示。"""
    with pytest.raises(ValueError, match="op/note"):
        engine._apply_ripple(
            _world(),
            {"op": "narrative", "target": "factions_state.巡夜司", "note": "痴执至死"},
            "cluster_001", [])


def test_apply_ripple_rejects_narrative_with_target():
    """narrative + target 混合形态 → 入口拒绝（canonical narrative 必须无 target）。"""
    with pytest.raises(ValueError, match="narrative"):
        engine._apply_ripple(
            _world(), {"narrative": "文本", "target": "factions_state.巡夜司.power"},
            "cluster_001", [])


def test_validate_ripple_shape_aligned_with_apply_dispatch():
    """anti-drift 锁：validate_ripple_shape 放行的每个 canonical 形态，_apply_ripple 必须可应用
    （不允许出现『校验说合法、apply 说未知操作』的劈叉）。"""
    canonical = [
        {"narrative": "叙事后果"},
        {"target": "factions_state.巡夜司.power", "delta": -5},
        {"target": "current_world_time.day", "advance": 2},
        {"target": "factions_state.巡夜司.wealth", "set": 10},
        {"target": "current_world_time.cluster", "set_to_current_cluster": True},
        {"target": "active_npc_threads", "add_thread": {"npc_id": "顾沉", "action": "彻查内鬼"}},
        {"target": "active_npc_threads", "evaluate_completion": True},
        {"target": "emergent_opportunities",
         "spawn": {"type": "副线", "description": "匿名信", "expires_clusters": 2}},
        {"target": "consequence_tracker", "add": {"event": "信任崩塌", "world_changes": []}},
    ]
    for ripple in canonical:
        assert engine.validate_ripple_shape(ripple) is None, ripple
        engine._apply_ripple(_world(), ripple, "cluster_003", [])  # 不得 raise


def test_validate_trigger_contract_dead_rule_detection():
    """fate_event 文本触发词 / auto_tick 非 every_cluster = 死规则；合法组合放行。"""
    assert engine.validate_trigger_contract("minor_event", "神农以身试毒/尝百草中毒") is None
    assert engine.validate_trigger_contract("fate_event", "ME-V1-01|ME-V2-03") is None
    assert engine.validate_trigger_contract("auto_tick", "every_cluster") is None
    assert "永不点火" in engine.validate_trigger_contract("fate_event", "尝百草中毒")
    assert "永不点火" in engine.validate_trigger_contract("fate_event", "ME-V1-01|尝百草中毒")
    assert "永不点火" in engine.validate_trigger_contract("auto_tick", "岁月流转")


def test_validate_rules_contract_reports_rule_and_ripple_positions():
    """整库校验定位到 rule 与 ripple 下标（供 db_schema_validate / 骨架验收直接转述）。"""
    errs = engine.validate_rules_contract({"ripple_rules": [
        {"id": "RR_OK", "trigger_type": "minor_event", "trigger_match": "触发词",
         "ripples": [{"narrative": "合法"}]},
        {"id": "RR_BAD", "trigger_type": "fate_event", "trigger_match": "文本触发词",
         "ripples": [{"op": "narrative", "note": "旧形态", "target": "x"}]},
    ]})
    assert any("ripple_rules[1](RR_BAD)" in e and "永不点火" in e for e in errs), errs
    assert any("ripple_rules[1](RR_BAD).ripples[0]" in e for e in errs), errs
    assert not any("RR_OK" in e for e in errs), errs


def test_dashboard_reports_current_cluster():
    with tempfile.TemporaryDirectory() as temp:
        root = _project(Path(temp))
        report = engine.dashboard(root)
        assert report["current_cluster"] == "cluster_001"
        assert report["current_day"] == 1
