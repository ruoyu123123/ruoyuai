"""world_seed_init + advance op + _normalize_me_pool 回归测试（SYS-1 / C02 / C19 · 2026-06-27）。

锁三件事的确定性骨架：
  SYS-1: world_evolution_engine._apply_ripple 新增无界 advance op（day 按章推进不被钳 0-100）
         + world_seed_init 播种让 tick 从「0 规则触发」复活成「N>0 规则触发」
         + 死角色 NPC thread 经 evaluate_completion 把牺牲落进 consequence_tracker
  C02:  world_seed_init 幂等（非空不覆盖·--force 重播）+ consequence_tracker list→dict 归一
  C19:  gen_creative_volume_arc._normalize_me_pool（volume 回填 / 悬空 prereq 过滤 / finale 兜底 / 完整性）

零依赖：仅标准库；test_* 无参数；失败 raise AssertionError；tempfile + utf-8。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import world_seed_init as wsi  # noqa: E402
import world_evolution_engine as wee  # noqa: E402
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
            "主角甲": {"status": "alive", "role": "主角", "current_stage": "开荒"},
            "烈士乙": {"status": "dead", "role": "战士", "current_stage": "牺牲(cluster_002·挡刀)",
                       "death_cluster": "cluster_002", "death_ch": 7},
            "盟友丙": {"status": "alive", "role": "掮客", "current_stage": "走私"},
        }})
    _w(db / "群像档.json", ensemble if ensemble is not None else {"characters": {}})
    _w(db / "涟漪规则.json", rules if rules is not None else {"ripple_rules": []})
    _w(db / "世界状态.json", world if world is not None else {
        "current_world_time": {"ch": 0, "cluster": "cluster_001", "day": 1},
        "factions_state": {}, "protagonist_state": {},
        "active_npc_threads": [], "consequence_tracker": []})  # 注意：list（待归一）
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
        world, {"target": "current_world_time.day", "advance": 6}, ch=1, applied_log=log)
    assert ok is True
    assert world["current_world_time"]["day"] == 105  # 无界（delta 会卡 100）
    assert log[-1]["op"] == "advance" and log[-1]["new"] == 105


def test_advance_op_skips_missing_path():
    """路径不存在 → 跳过不崩（返回 False·记 skip_path_missing）。"""
    world = {"current_world_time": {"day": 1}}
    log = []
    ok = wee._apply_ripple(world, {"target": "nonexist.x", "advance": 1}, ch=1, applied_log=log)
    assert ok is False
    assert log[-1]["result"] == "skip_path_missing"


def test_advance_op_dirty_old_value_starts_from_zero():
    """旧值脏（None）→ 从 0 起推进不崩。"""
    world = {"current_world_time": {"day": None}}
    log = []
    ok = wee._apply_ripple(world, {"target": "current_world_time.day", "advance": 3}, ch=1, applied_log=log)
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
        assert r["consequence_tracker_normalized"] is True  # list → dict
        world = _r(tmp / "_数据库" / "世界状态.json")
        assert isinstance(world["consequence_tracker"], dict)
        assert world["factions_state"]["阵营X"]["power"] == 50
        # 死角色 thread 带 expected_complete_cluster=death_cluster
        dead = next(t for t in world["active_npc_threads"] if t["npc_id"] == "烈士乙")
        assert dead["expected_complete_cluster"] == "cluster_002"
        alive = next(t for t in world["active_npc_threads"] if t["npc_id"] == "盟友丙")
        assert alive["expected_complete_cluster"] is None  # 在世角色不预设结局


def test_tick_fires_rules_after_seed():
    """SYS-1 核心：播种前 tick 0 规则触发；播种后 tick N>0 规则触发 + day 推进。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        # 播种前
        before = wee.tick(tmp, 1)
        assert before["matched_rules"] == []  # 空规则池 → 0 触发
        # 播种
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=True, dry_run=False)
        day0 = _r(tmp / "_数据库" / "世界状态.json")["current_world_time"]["day"]
        after = wee.tick(tmp, 1)
        assert "RR_AUTO_TICK_BASE" in after["matched_rules"]  # N>0 触发（复活）
        day1 = _r(tmp / "_数据库" / "世界状态.json")["current_world_time"]["day"]
        assert day1 == day0 + 1  # advance op 让 day 推进一格


def test_sacrifice_lands_in_consequence_tracker():
    """死角色 thread 经 auto_tick 的 evaluate_completion → 到 death_cluster 时把牺牲落 consequence_tracker。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=True, dry_run=False)
        # 逐章 tick 到 cluster_002（ch5 起 cur_cluster_num=2 >= 烈士乙 ec=2 → 完成）
        for ch in range(1, 8):
            wee.tick(tmp, ch)
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
            {"id": "RR_USER_1", "trigger_type": "minor_event", "trigger_match": "x", "ripples": []}]})
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=False, dry_run=False)
        rules = _r(tmp / "_数据库" / "涟漪规则.json")["ripple_rules"]
        assert any(x["id"] == "RR_USER_1" for x in rules)  # 用户规则保留
        assert any(x["id"] == "RR_AUTO_TICK_BASE" for x in rules)  # seed 增量加入


def test_force_resseeds_seeded_only():
    """--force 清掉本器播过的（_seeded_by）再重播·不碰用户手写规则。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), rules={"ripple_rules": [
            {"id": "RR_USER_1", "trigger_type": "minor_event", "trigger_match": "x", "ripples": []}]})
        wsi.seed(tmp, explicit_factions=[], force=False, reset_ticks=False, dry_run=False)
        r2 = wsi.seed(tmp, explicit_factions=[], force=True, reset_ticks=False, dry_run=False)
        assert "RR_AUTO_TICK_BASE" in r2["rules_added"]  # 重播
        rules = _r(tmp / "_数据库" / "涟漪规则.json")["ripple_rules"]
        assert any(x["id"] == "RR_USER_1" for x in rules)  # 用户规则不动
        ids = [x["id"] for x in rules]
        assert len(ids) == len(set(ids))  # 重播无重复


# ═══════════════════════ 4. C19 · _normalize_me_pool ═══════════════════════

def test_normalize_backfills_volume_from_id():
    """ME 无 volume → 从 id 反推 ME-V3- → volume=3。"""
    mes = [{"id": "ME-V3-02", "title": "x"}]
    rep = gva._normalize_me_pool(mes)
    assert mes[0]["volume"] == 3
    assert "ME-V3-02" in rep["volume_backfilled"]


def test_normalize_drops_dangling_prereqs():
    """悬空 prerequisites（指向不存在的 id）被过滤·命中真实 id 的保留。"""
    mes = [
        {"id": "ME-V1-01", "volume": 1},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True,
         "prerequisites": ["ME-V1-01", "ME-V9-99"]},  # 后者悬空
    ]
    rep = gva._normalize_me_pool(mes)
    assert mes[1]["prerequisites"] == ["ME-V1-01"]
    assert rep["dangling_prereqs_dropped"][0]["dropped"] == ["ME-V9-99"]


def test_normalize_finale_fallback():
    """卷无 is_volume_finale → 最大序号 ME 兜底标 finale。"""
    mes = [
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-03", "volume": 1, "is_volume_finale": False},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": False},
    ]
    rep = gva._normalize_me_pool(mes)
    anchor = next(m for m in mes if m["id"] == "ME-V1-03")  # 序号最大
    assert anchor["is_volume_finale"] is True
    assert anchor["_finale_inferred"] is True
    assert rep["finale_fallback"][0]["me"] == "ME-V1-03"


def test_normalize_integrity_violation_no_volume():
    """ME 无 volume 且 id 无法反推 → 记 integrity_violations。"""
    mes = [{"id": "BADID", "title": "x"}]
    rep = gva._normalize_me_pool(mes)
    assert rep["integrity_violations"]
    assert rep["integrity_violations"][0]["me"] == "BADID"


def test_normalize_clean_pool_no_changes():
    """已规范 ME 池（有 volume / finale / 合法 prereq）→ 无回填无丢弃无兜底。"""
    mes = [
        {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False, "prerequisites": []},
        {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True, "prerequisites": ["ME-V1-01"]},
    ]
    rep = gva._normalize_me_pool(mes)
    assert rep == {"volume_backfilled": [], "dangling_prereqs_dropped": [],
                   "finale_fallback": [], "integrity_violations": []}


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
