"""world_evolution_engine 涟漪引擎回归测试 — 北极星②（涟漪规则为核心 · 混合式分界）。

钉死三条核心语义（此前零覆盖）：
  · 数值 delta —— 客观世界数值由引擎确定性算 delta（精确应用 + 0-100 钳制），
    minor_event 无幂等账本 → 重复触发按设计累加（每次走向卡选择都是新因果）。
  · 触发不满足 —— trigger_match 不命中 → matched_rules 空、世界数值一动不动。
  · narrative 分流 —— 叙事/主观因果 ripple（{narrative} 无 target）**不机械改数值**，
    只收集进 narrative_consequences 交模型解读（build_manifest 注入 writer/emergence），
    且 (ch, text) 去重防断点重跑单调膨胀。
  · 幂等边界 —— fate_event 按 event_id 账本去重（事件级一次性影响）；
    tick 按 applied_ticks 章号账本去重（WAL 断点重跑不漂移）。

规则/世界状态形态取自 core/claude-home/templates/subsystem_skeletons.json 的
涟漪规则(ripple_rules_v20_1) / 世界状态(world_state_v20_1) 骨架。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import world_evolution_engine as wee  # noqa: E402


# ───────────────────── 骨架形态 fixture（world_state_v20_1 / ripple_rules_v20_1） ─────────────────────

def _world() -> dict:
    # 注：骨架 consequence_tracker 写 []，引擎 add/evaluate_completion 按 dict 消费；
    # 本测试不触 consequence op，置 {} 避免骨架-引擎类型分歧噪音（另案契约债）。
    return {
        "_schema": "world_state_v20_1",
        "schema_version": "v27",
        "current_world_time": {"ch": 1, "cluster": "cluster_001", "day": 1},
        "protagonist_state": {},
        "factions_state": {"巡夜司": {"power": 60, "stability": 50, "wealth": 95}},
        "active_npc_threads": [],
        "emergent_opportunities": [],
        "consequence_tracker": {},
    }


def _rules() -> dict:
    return {
        "_schema": "ripple_rules_v20_1",
        "schema_version": "v27",
        "ripple_rules": [
            # ① 数值 delta 类（minor_event · 含钳制用例：wealth 95+20 → 100）
            {"id": "RR_001", "trigger_type": "minor_event",
             "trigger_match": "B_夜探据点|铁锈味重锤埋设",
             "ripples": [
                 {"target": "factions_state.巡夜司.power", "delta": -10, "reason": "据点暴露被反制"},
                 {"target": "factions_state.巡夜司.wealth", "delta": 20, "reason": "缴获赃银（钳到 100）"},
             ]},
            # ② 触发条件不满足类（trigger_match 永不命中本批测试的走向词）
            {"id": "RR_002_never", "trigger_type": "minor_event",
             "trigger_match": "Z_永不触发的走向",
             "ripples": [{"target": "factions_state.巡夜司.power", "delta": 50}]},
            # ③ narrative 分流类（无 target → 交模型解读，不机械改数值）
            {"id": "RR_003_narrative", "trigger_type": "minor_event",
             "trigger_match": "D_心理余波",
             "ripples": [{"narrative": "巡夜司内部开始互相猜忌，老都头夜巡不再独行",
                          "reason": "夜探余波属主观因果，交模型解读"}]},
            # fate_event（幂等账本用例）
            {"id": "RR_FATE", "trigger_type": "fate_event", "trigger_match": "ME_001",
             "ripples": [{"target": "factions_state.巡夜司.stability", "delta": -5, "reason": "大事件震荡"}]},
            # auto_tick（tick 幂等用例）
            {"id": "RR_AUTO_TICK", "trigger_type": "auto_tick", "trigger_match": "every_chapter",
             "ripples": [{"target": "factions_state.巡夜司.power", "delta": 1, "reason": "巡夜司日常扩张"}]},
        ],
    }


def _mk_project(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "世界状态.json").write_text(json.dumps(_world(), ensure_ascii=False), encoding="utf-8")
    (db / "涟漪规则.json").write_text(json.dumps(_rules(), ensure_ascii=False), encoding="utf-8")
    return tmp


def _disk_world(root: Path) -> dict:
    return json.loads((root / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))


# ---------- ① 数值 delta：精确应用 + 0-100 钳制 + 落盘 ----------

def test_minor_event_numeric_delta_exact_and_clamp():
    """命中 RR_001 → power 60-10=50 精确落地；wealth 95+20 钳到 100；applied_log 记 old/new/delta。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        r = wee.apply_minor_event(root, 5, "B_夜探据点")
        assert r["matched_rules"] == ["RR_001"]
        by_target = {e["target"]: e for e in r["applied_log"]}
        assert by_target["factions_state.巡夜司.power"]["old"] == 60
        assert by_target["factions_state.巡夜司.power"]["new"] == 50
        assert by_target["factions_state.巡夜司.power"]["delta"] == -10
        assert by_target["factions_state.巡夜司.wealth"]["new"] == 100  # 95+20 钳制上限
        # 原子写落盘（不是只改内存）
        w = _disk_world(root)
        assert w["factions_state"]["巡夜司"]["power"] == 50
        assert w["factions_state"]["巡夜司"]["wealth"] == 100
        assert w["factions_state"]["巡夜司"]["stability"] == 50  # 未涉及维度不动


# ---------- ② 触发条件不满足：世界数值一动不动 ----------

def test_unmatched_rule_world_state_untouched():
    """走向词谁都不命中 → matched/applied 全空，factions/narrative 与初始深等（只许 ticks_log 记账）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        r = wee.apply_minor_event(root, 5, "完全无关的走向卡")
        assert r["matched_rules"] == []
        assert r["applied_log"] == []
        w = _disk_world(root)
        assert w["factions_state"] == _world()["factions_state"]
        assert "narrative_consequences" not in w
        # 引擎语义：apply_minor_event 即使 0 命中也记一条 world_ticks_log（审计轨迹）
        assert len(w["world_ticks_log"]) == 1
        assert w["world_ticks_log"][0]["trigger_type"] == "minor_event"


# ---------- ③ narrative 分流：只产「交模型解读」条目，不直接改数值（混合式分界） ----------

def test_narrative_ripple_diverts_to_model_not_numeric():
    """命中 RR_003 → 条目进 narrative_consequences（_kind=narrative），factions 数值零变化。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        r = wee.apply_minor_event(root, 5, "D_心理余波")
        assert r["matched_rules"] == ["RR_003_narrative"]
        assert [e["op"] for e in r["applied_log"]] == ["narrative"]
        w = _disk_world(root)
        nc = w["narrative_consequences"]
        assert len(nc) == 1
        assert nc[0]["ch"] == 5
        assert nc[0]["text"] == "巡夜司内部开始互相猜忌，老都头夜巡不再独行"
        assert nc[0]["_kind"] == "narrative"
        # 北极星②分界：叙事因果不由引擎机械算数值
        assert w["factions_state"] == _world()["factions_state"]


def test_narrative_dedup_same_ch_appends_new_ch():
    """同 (ch, text) 重跑去重只留 1 条；换 ch 触发是新因果 → 追加第 2 条。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        wee.apply_minor_event(root, 5, "D_心理余波")
        wee.apply_minor_event(root, 5, "D_心理余波")  # 断点重跑场景
        assert len(_disk_world(root)["narrative_consequences"]) == 1
        wee.apply_minor_event(root, 6, "D_心理余波")
        nc = _disk_world(root)["narrative_consequences"]
        assert [e["ch"] for e in nc] == [5, 6]


# ---------- 重复应用语义：minor_event 累加（无账本）vs fate_event/tick 幂等（有账本） ----------

def test_minor_event_reapply_accumulates_by_design():
    """minor_event 无幂等账本 → 两次走向触发数值累加（60→50→40），钳制项保持 100 不再涨。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        wee.apply_minor_event(root, 5, "B_夜探据点")
        wee.apply_minor_event(root, 6, "B_夜探据点")
        w = _disk_world(root)
        assert w["factions_state"]["巡夜司"]["power"] == 40
        assert w["factions_state"]["巡夜司"]["wealth"] == 100


def test_fate_event_idempotent_by_event_id_ledger():
    """同 event_id 重放（split 平铺逐章重放场景）→ 第二次 skipped_idempotent，delta 不乘倍。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        r1 = wee.apply_fate_event(root, 5, "ME_001")
        assert r1["matched_rules"] == ["RR_FATE"]
        assert _disk_world(root)["factions_state"]["巡夜司"]["stability"] == 45
        r2 = wee.apply_fate_event(root, 7, "ME_001")
        assert r2.get("skipped_idempotent") is True
        assert r2["first_applied_at_ch"] == 5
        assert r2["applied_log"] == []
        assert _disk_world(root)["factions_state"]["巡夜司"]["stability"] == 45  # 不二次扣减


def test_tick_idempotent_by_applied_ticks_ledger():
    """同章 tick 重跑（WAL 断点）→ 第二次 skipped=already_ticked，auto_tick delta 只落一次。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d))
        r1 = wee.tick(root, 3)
        assert r1["matched_rules"] == ["RR_AUTO_TICK"]
        w = _disk_world(root)
        assert w["factions_state"]["巡夜司"]["power"] == 61  # 60+1
        assert w["current_world_time"]["ch"] == 3
        assert w["applied_ticks"] == [3]
        r2 = wee.tick(root, 3)
        assert r2.get("skipped") == "already_ticked"
        w2 = _disk_world(root)
        assert w2["factions_state"]["巡夜司"]["power"] == 61  # 不重复 +1
        # auto_tick 日志同 ch 也去重，只留 1 条
        assert sum(1 for e in w2["world_ticks_log"] if e.get("trigger_type") == "auto_tick" and e.get("ch") == 3) == 1


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
