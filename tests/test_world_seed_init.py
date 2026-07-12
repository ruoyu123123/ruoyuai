"""世界演化播种器与 canonical ME 池回归测试。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import world_seed_init as wsi  # noqa: E402
import world_evolution_engine as wee  # noqa: E402
import world_evolution_apply_cluster as wac  # noqa: E402
import gen_creative_volume_arc as gva  # noqa: E402  volume_arc 实现（2026-07-07 从 gen_creative 拆出）


# ═══════════════════════ 脚手架 ═══════════════════════

def _db(project: Path) -> Path:
    d = project / "_数据库"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _w(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _r(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _mk_project(tmp: Path, *, major=None, arc=None, ensemble=None,
                world=None, rules=None) -> Path:
    db = _db(tmp)
    _w(db / "大势卡.json", major if major is not None else {
        "major_events": [
            {"id": "ME-V1-01", "volume": 1, "title": "开场", "is_volume_finale": False},
            {"id": "ME-V1-02", "volume": 1, "title": "收尾", "is_volume_finale": True,
             "prerequisites": ["ME-V1-01"]},
        ]})
    _w(db / "character_arc_state.json", arc if arc is not None else {
        "characters": {
            "主角甲": {"status": "alive", "role": "主角", "current_stage_at_cluster": "cluster_001:开荒"},
            "烈士乙": {"status": "dead", "role": "战士", "current_stage_at_cluster": "cluster_002:牺牲(cluster_002·挡刀)",
                       "death_cluster": "cluster_002"},
            "盟友丙": {"status": "alive", "role": "掮客", "current_stage_at_cluster": "cluster_001:走私"},
        }})
    _w(db / "群像档.json", ensemble if ensemble is not None else {"characters": {}})
    _w(db / "涟漪规则.json", rules if rules is not None else {"ripple_rules": []})
    _w(db / "世界状态.json", world if world is not None else {
        "current_world_time": {"cluster": "cluster_001", "day": 1},
        "factions_state": {}, "protagonist_state": {},
        "active_npc_threads": [], "emergent_opportunities": [], "consequence_tracker": {}})
    # 事件簇供 ch→cluster 反查（cluster_002 含死角色 death_cluster）
    _w(db / "事件簇.json", {"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 4]},
        {"cluster_id": "cluster_002", "chapter_range": [5, 8]},
    ]})
    return tmp


# ═══════════════════════ 1. SYS-1 · advance op（无界 day 推进） ═══════════════════════

def test_advance_op_unbounded_increment():
    """advance 不被钳 0-100：day 从 99 推到 105（delta 会被钳到 100）。"""
    world = {"current_world_time": {"day": 99}}
    log = []
    ok = wee._apply_ripple(
        world, {"target": "current_world_time.day", "advance": 6},
        "cluster_001", log)
    assert ok is True
    assert world["current_world_time"]["day"] == 105  # 无界（delta 会卡 100）
    assert log[-1]["op"] == "advance" and log[-1]["new"] == 105


def test_advance_op_skips_missing_path():
    """路径不存在 → 跳过不崩（返回 False·记 skip_path_missing）。"""
    world = {"current_world_time": {"day": 1}}
    log = []
    ok = wee._apply_ripple(
        world, {"target": "nonexist.x", "advance": 1}, "cluster_001", log)
    assert ok is False
    assert log[-1]["result"] == "skip_path_missing"


def test_advance_op_dirty_old_value_starts_from_zero():
    """旧值脏（None）→ 从 0 起推进不崩。"""
    world = {"current_world_time": {"day": None}}
    log = []
    ok = wee._apply_ripple(
        world, {"target": "current_world_time.day", "advance": 3},
        "cluster_001", log)
    assert ok is True
    assert world["current_world_time"]["day"] == 3


# ═══════════════════════ 2. SYS-1 · world_seed_init 播种 + tick 复活 ═══════════════════════

def test_seed_adds_rules_and_baselines():
    """播种后：auto_tick 基线 + per-ME fate + per-faction minor 规则齐 + factions/protagonist/threads。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        r = wsi.seed(tmp, explicit_factions=["阵营X"], force=False, reset_ticks=False, dry_run=False)
        assert "RR_AUTO_TICK_BASE" in r["rules_added"]
        assert "RR_FATE_ME-V1-01" in r["rules_added"] and "RR_FATE_ME-V1-02" in r["rules_added"]
        assert "RR_MINOR_阵营X" in r["rules_added"]
        assert r["protagonist"] == "主角甲" and r["protagonist_seeded"] is True
        assert "烈士乙" in r["threads_seeded"] and "盟友丙" in r["threads_seeded"]
        assert "主角甲" not in r["threads_seeded"]  # 主角不进 NPC thread
        world = _r(tmp / "_数据库" / "世界状态.json")
        assert isinstance(world["consequence_tracker"], dict)
        assert world["factions_state"]["阵营X"]["power"] == 50
        # 死角色 thread 带 expected_complete_cluster=death_cluster
        dead = next(t for t in world["active_npc_threads"] if t["npc_id"] == "烈士乙")
        assert dead["expected_complete_cluster"] == "cluster_002"
        alive = next(t for t in world["active_npc_threads"] if t["npc_id"] == "盟友丙")
        assert alive["expected_complete_cluster"] is None  # 在世角色不预设结局


def test_seed_requires_precreated_core_databases():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "_数据库").mkdir()
        try:
            wsi.seed(root, explicit_factions=[], force=False,
                     reset_ticks=False, dry_run=False)
        except ValueError as exc:
            assert "大势卡.json" in str(exc)
        else:
            raise AssertionError("缺核心状态库必须失败")


def test_seed_rejects_malformed_world_schema():
    with tempfile.TemporaryDirectory() as directory:
        root = _mk_project(Path(directory), world={
            "current_world_time": {"cluster": "cluster_001", "day": 1},
            "factions_state": {},
            "protagonist_state": {},
            "active_npc_threads": [],
            "emergent_opportunities": [],
        })
        try:
            wsi.seed(root, explicit_factions=[], force=False,
                     reset_ticks=False, dry_run=False)
        except ValueError as exc:
            assert "consequence_tracker" in str(exc)
        else:
            raise AssertionError("缺 consequence_tracker 必须失败")


def _delta(cluster_id: str) -> dict:
    return {
        "cluster_id": cluster_id,
        "time_advance": {},
        "location_changes": [],
        "hub_usage": [],
        "fate_events_triggered": [],
        "world_state_consumption": {
            "emergent_opportunities_consumed": [],
            "thread_responded": [],
        },
        "heart_events_revealed": [],
    }


def test_tick_fires_rules_after_seed():
    """播种后每个 cluster 触发 auto_tick 并推进 world day。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=True, dry_run=False)
        day0 = _r(tmp / "_数据库" / "世界状态.json")["current_world_time"]["day"]
        after = wac.apply_cluster(tmp, "cluster_001", _delta("cluster_001"))
        assert "RR_AUTO_TICK_BASE" in after["tick"]["matched_rules"]
        day1 = _r(tmp / "_数据库" / "世界状态.json")["current_world_time"]["day"]
        assert day1 == day0 + 1


def test_sacrifice_lands_in_consequence_tracker():
    """死角色 thread 到 expected_complete_cluster 时落入 consequence_tracker。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=True, dry_run=False)
        wac.apply_cluster(tmp, "cluster_001", _delta("cluster_001"))
        wac.apply_cluster(tmp, "cluster_002", _delta("cluster_002"))
        world = _r(tmp / "_数据库" / "世界状态.json")
        ct = world["consequence_tracker"]
        assert isinstance(ct, dict)
        sac = [v for v in ct.values() if "牺牲" in (v.get("outcome") or "")]
        assert sac, f"应有牺牲条目，实得 {ct}"
        assert sac[0]["npc"] == "烈士乙"
        # 完成后 thread 从 active 移除
        assert all(t["npc_id"] != "烈士乙" for t in world["active_npc_threads"])


# ═══════════════════════ 3. C02 · 幂等 + --force ═══════════════════════

def test_seed_idempotent_no_dup():
    """二次播种不重复加规则/thread（按 id / npc_id 去重）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        wsi.seed(tmp, explicit_factions=["阵营X"], force=False, reset_ticks=False, dry_run=False)
        r2 = wsi.seed(tmp, explicit_factions=["阵营X"], force=False, reset_ticks=False, dry_run=False)
        assert r2["rules_added"] == []  # 已存在 → 不再加
        assert r2["threads_seeded"] == []
        rules = _r(tmp / "_数据库" / "涟漪规则.json")["ripple_rules"]
        ids = [x["id"] for x in rules]
        assert len(ids) == len(set(ids))  # 无重复
        world = _r(tmp / "_数据库" / "世界状态.json")
        npcs = [t["npc_id"] for t in world["active_npc_threads"]]
        assert len(npcs) == len(set(npcs))


def test_seed_non_empty_not_overwritten():
    """非空不覆盖：已有 ripple_rules / protagonist_state 不被清掉。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), rules={"ripple_rules": [
            {"id": "RR_USER_1", "trigger_type": "minor_event", "trigger_match": "x",
             "ripples": [{"narrative": "用户规则"}]}]})
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=False, dry_run=False)
        rules = _r(tmp / "_数据库" / "涟漪规则.json")["ripple_rules"]
        assert any(x["id"] == "RR_USER_1" for x in rules)  # 用户规则保留
        assert any(x["id"] == "RR_AUTO_TICK_BASE" for x in rules)  # seed 增量加入


def test_force_resseeds_seeded_only():
    """--force 清掉本器播过的（_seeded_by）再重播·不碰用户手写规则。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), rules={"ripple_rules": [
            {"id": "RR_USER_1", "trigger_type": "minor_event", "trigger_match": "x",
             "ripples": [{"narrative": "用户规则"}]}]})
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=False, dry_run=False)
        r2 = wsi.seed(tmp, explicit_factions=[], force=True, reset_ticks=False, dry_run=False)
        assert "RR_AUTO_TICK_BASE" in r2["rules_added"]  # 重播
        rules = _r(tmp / "_数据库" / "涟漪规则.json")["ripple_rules"]
        assert any(x["id"] == "RR_USER_1" for x in rules)  # 用户规则不动
        ids = [x["id"] for x in rules]
        assert len(ids) == len(set(ids))  # 重播无重复


# ═══════════════════════ 4. ME 池结构与引用验证 ═══════════════════════

def _assert_invalid_me_pool(events, expected: str):
    try:
        gva._normalize_me_pool(events)
    except ValueError as exc:
        assert expected in str(exc)
    else:
        raise AssertionError("无效 ME 池必须失败")


def test_me_pool_requires_explicit_volume():
    _assert_invalid_me_pool(
        [{"id": "ME-V3-02", "title": "x", "is_volume_finale": True}],
        ".volume",
    )


def test_me_pool_rejects_dangling_prerequisites():
    mes = [
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True,
         "prerequisites": ["ME-V1-01", "ME-V9-99"]},
    ]
    _assert_invalid_me_pool(mes, "悬空 prerequisites")
    assert mes[1]["prerequisites"] == ["ME-V1-01", "ME-V9-99"]


def test_me_pool_requires_exactly_one_finale_per_volume():
    mes = [
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-03", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": False},
    ]
    _assert_invalid_me_pool(mes, "恰有一个")
    assert all(event["is_volume_finale"] is False for event in mes)


def test_me_pool_rejects_duplicate_ids():
    _assert_invalid_me_pool([
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": True},
    ], "id 重复")


def test_me_pool_valid_contract():
    mes = [
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False, "prerequisites": []},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True, "prerequisites": ["ME-V1-01"]},
    ]
    rep = gva._normalize_me_pool(mes)
    assert rep == {"validated_events": 2, "volumes": [1]}


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
