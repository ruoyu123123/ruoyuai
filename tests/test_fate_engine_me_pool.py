"""fate_engine ME 池契约 + thread_responded 消费方回归测试 — [#6 北极星③ · #7 孤儿契约]。

[#6] 钉死 fate_engine 能读真实项目大势卡 ME 池（三重契约背离修复）：
  · 键名 —— 真实项目顶层是 major_events_pool（不是 major_events）→ _events 两套兼容
  · 字段名 —— 真实项目 ME 用 me_id（如 V1_ME_001）不是 id → _event_id 两套兼容
  · ID 格式 —— changes_schema event_id pattern 放宽认 V1_ME_001 / ME-V1-01
  背景：旧版对真实项目 _events 恒返 0 → 大势永不 completed / drift 永不触发 /
  writer 收不到 active_fate_events → 「大势已定」软牵引（北极星③）静默失效。
  纪律：修契约让 fate_engine 能读，但不得把大势变硬锁（仍只 evaluate/drift 顾问，不改 status）。

[#7] 钉死 thread_responded（writer 申报呼应了哪些幕后 NPC 线）有消费方：
  · world_evolution_apply_chapter 据 thread_responded 推进对应 thread（参照 emergent_opportunities）
  · 累计呼应达 expected_responses → 从 active_npc_threads 移除 + 写 consequence_tracker
  · thread_id 不存在只记 missing 不报错（advisory · 不阻断流水线）
"""
import json
import re
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import fate_engine  # noqa: E402
import world_evolution_apply_chapter as wac  # noqa: E402


# ───────────────────── 真实项目大势卡形态（major_events_pool + me_id） ─────────────────────

_REAL_FATE = {
    "schema_version": "v22.cluster.2",
    "major_events_pool": [
        {"me_id": "V1_ME_001", "name": "首案", "description": "青灰色老人案",
         "status": "completed", "completed_at_ch": 4},
        {"me_id": "V1_ME_002", "name": "对赌", "description": "与师姐对赌",
         "status": "completed", "completed_at_ch": 8},
        {"me_id": "V1_ME_007", "name": "反向操纵", "description": "反击审查",
         "status": "pending"},
    ],
}


def _mk_fate_project(tmp: Path, fate: dict) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(json.dumps(fate, ensure_ascii=False), encoding="utf-8")
    return tmp


# ---------- [#6] 键名契约：major_events_pool ----------

def test_events_reads_major_events_pool():
    """真实项目顶层 major_events_pool → _events 必须读到（旧版恒返 0）。"""
    assert len(fate_engine._events(_REAL_FATE)) == 3


def test_events_falls_back_to_major_events_legacy():
    """旧形态 major_events 仍兼容（向后不破坏）。"""
    legacy = {"major_events": [{"id": "ME_001", "status": "scheduled"}]}
    assert len(fate_engine._events(legacy)) == 1


def test_events_filters_non_dict():
    """ME 池混入字符串/None 占位 → 过滤不崩。"""
    dirty = {"major_events_pool": [{"me_id": "V1_ME_001"}, "占位", None, 42]}
    assert len(fate_engine._events(dirty)) == 1


# ---------- [#6] 字段名契约：me_id ----------

def test_event_id_reads_me_id():
    """真实项目 ME 用 me_id → _event_id 命中（旧版读 id 永远 None）。"""
    assert fate_engine._event_id({"me_id": "V1_ME_001"}) == "V1_ME_001"


def test_event_id_prefers_id_then_me_id():
    """两套字段兼容：id 优先，无 id 退 me_id（与 emergence._get_me_id 同范式）。"""
    assert fate_engine._event_id({"id": "ME_009", "me_id": "V1_ME_001"}) == "ME_009"
    assert fate_engine._event_id({"me_id": "V1_ME_001"}) == "V1_ME_001"
    assert fate_engine._event_id({}) == ""
    assert fate_engine._event_id("非dict") == ""


# ---------- [#6] 全链路：dashboard / evaluate / update / drift 都看得到真实 ME ----------

def test_dashboard_sees_real_me_pool():
    """dashboard 必须统计到 3 个 ME（旧版 total_events=0）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_fate_project(Path(d), _REAL_FATE)
        db = fate_engine.dashboard(root)
        assert db["total_events"] == 3
        assert db["by_status"]["completed"] == 2
        assert db["by_status"]["pending"] == 1


def test_evaluate_active_ids_use_real_me_id():
    """evaluate 返回的 active event id 必须是真实 me_id（供 build_manifest 透传 writer）。"""
    with tempfile.TemporaryDirectory() as d:
        # 把 V1_ME_007 设为 scheduled 才会进 active（evaluate 只激活 scheduled）
        fate = json.loads(json.dumps(_REAL_FATE))
        fate["major_events_pool"][2]["status"] = "scheduled"
        root = _mk_fate_project(Path(d), fate)
        r = fate_engine.evaluate(root, 30)
        ids = [e["id"] for e in r["active_fate_events"]]
        assert "V1_ME_007" in ids
        assert r["total_completed"] == 2


def test_update_matches_writer_real_me_id():
    """writer 在 _changes.json 回填真实 me_id → fate_engine.update 必须命中并标 completed。"""
    with tempfile.TemporaryDirectory() as d:
        fate = json.loads(json.dumps(_REAL_FATE))
        fate["major_events_pool"][2]["status"] = "scheduled"
        root = _mk_fate_project(Path(d), fate)
        # 写一个章 _changes.json 申报触发 V1_ME_007
        ch = 30
        ch_dir = root / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / f"第{ch:03d}章_changes.json").write_text(json.dumps({
            "factual": {"fate_events_triggered": [
                {"event_id": "V1_ME_007", "completion": "full", "evidence": "审查通过"}
            ]},
            "self_eval": {},
        }, ensure_ascii=False), encoding="utf-8")
        r = fate_engine.update(root, ch)
        assert r["updated"] == 1
        assert r["event_ids"] == ["V1_ME_007"]
        # 落盘后该 ME status 改为 completed（用真实 me_id 命中）
        after = json.loads((root / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        me7 = next(e for e in after["major_events_pool"] if e["me_id"] == "V1_ME_007")
        assert me7["status"] == "completed"
        assert me7["completed_at_ch"] == ch


def test_soft_pull_not_hard_lock():
    """北极星③：fate_engine 是顾问——evaluate/drift 只读，绝不擅自改 ME status。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_fate_project(Path(d), _REAL_FATE)
        before = (root / "_数据库" / "大势卡.json").read_text(encoding="utf-8")
        fate_engine.evaluate(root, 30)
        fate_engine.drift(root, 30)
        after = (root / "_数据库" / "大势卡.json").read_text(encoding="utf-8")
        assert before == after  # evaluate/drift 不写盘、不改 status


# ---------- [#6] schema event_id pattern 放宽 ----------

def test_changes_schema_event_id_pattern_accepts_real_formats():
    """changes_schema event_id pattern 放宽认真实 ME id（旧 ^ME_\\d+$ 必拒 V1_ME_001）。"""
    sch = json.loads(
        (Path(__file__).resolve().parents[1] / "core" / "claude-home" / "schemas"
         / "changes_schema.json").read_text(encoding="utf-8"))
    pat = (sch["properties"]["factual"]["properties"]["fate_events_triggered"]
           ["items"]["properties"]["event_id"]["pattern"])
    rx = re.compile(pat)
    for good in ("ME_001", "ME_007", "V1_ME_001", "ME-V1-01", "V2.ME01", "V10_ME_099"):
        assert rx.match(good), f"{good} 应匹配"
    for bad in ("", "random_string", "CK_001", "HE_x"):
        assert not rx.match(bad), f"{bad} 不应匹配"


# ───────────────────── [#7] thread_responded 消费方 ─────────────────────

def _mk_world_project(tmp: Path, threads: list, changes: dict) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "世界状态.json").write_text(json.dumps({
        "schema_version": "v20.1",
        "current_world_time": {"ch": 1},
        "active_npc_threads": threads,
    }, ensure_ascii=False), encoding="utf-8")
    # respond_threads 单测不需要 涟漪规则，但 apply_one_chapter / main 预检需要
    (db / "涟漪规则.json").write_text(json.dumps({"ripple_rules": []}, ensure_ascii=False),
                                     encoding="utf-8")
    ch = 5
    ch_dir = tmp / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / f"第{ch:03d}章_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_respond_threads_marks_responded():
    """writer 申报 thread_responded → 对应 thread 标 responded_by_writer=true。"""
    with tempfile.TemporaryDirectory() as d:
        threads = [
            {"thread_id": "NT_001", "npc_id": "审查组", "current_action": "暗中调研",
             "expected_responses": 2, "outcome_if_complete": "审查升级"},
        ]
        tmp = _mk_world_project(Path(d), threads, {})
        r = wac.respond_threads(tmp, 5, ["NT_001"])
        assert r["responded_count"] == 1
        assert r["completed"] == []  # expected_responses=2，呼应 1 次未完成
        world = json.loads((tmp / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        t = world["active_npc_threads"][0]
        assert t["responded_by_writer"] is True
        assert t["responded_count"] == 1
        assert t["responded_at_ch"] == [5]


def test_respond_threads_completes_and_removes():
    """呼应累计达 expected_responses（默认 1）→ 从 active 移除 + 写 consequence。"""
    with tempfile.TemporaryDirectory() as d:
        threads = [
            {"thread_id": "NT_002", "npc_id": "老登记员", "current_action": "找撕掉的页",
             "outcome_if_complete": "登记簿真相浮现"},  # 无 expected_responses → 默认 1
        ]
        tmp = _mk_world_project(Path(d), threads, {})
        r = wac.respond_threads(tmp, 5, ["NT_002"])
        assert r["completed"] == ["NT_002"]
        world = json.loads((tmp / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        assert world["active_npc_threads"] == []  # 已完成被移除
        ct = world["consequence_tracker"]
        key = "ch5_thread_responded_NT_002"
        assert key in ct
        assert ct[key]["outcome"] == "登记簿真相浮现"
        assert ct[key]["npc"] == "老登记员"


def test_respond_threads_missing_is_advisory_not_fatal():
    """thread_id 不存在 → 记 missing 不报错（advisory · 不阻断流水线）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d), [], {})
        r = wac.respond_threads(tmp, 5, ["NT_999"])
        assert r["missing"] == ["NT_999"]
        assert r["responded_count"] == 0
        assert "error" not in r


def test_respond_threads_empty_noop():
    """无 thread_responded → no-op（不读盘不写盘）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_world_project(Path(d), [], {})
        r = wac.respond_threads(tmp, 5, [])
        assert r["responded_count"] == 0
        assert r["missing"] == []


def test_apply_one_chapter_wires_thread_responded():
    """端到端：apply_one_chapter 从 _changes.json.world_state_consumption.thread_responded 消费。"""
    with tempfile.TemporaryDirectory() as d:
        threads = [
            {"thread_id": "NT_003", "npc_id": "师姐", "current_action": "回忆 10 年前",
             "outcome_if_complete": "笔记本真相"},
        ]
        changes = {
            "factual": {
                "world_state_consumption": {"thread_responded": ["NT_003"]},
            },
            "self_eval": {},
        }
        tmp = _mk_world_project(Path(d), threads, changes)
        summary, half = wac.apply_one_chapter(tmp, 5)
        ops = {o["op"]: o for o in summary["ops"]}
        assert "respond_threads" in ops
        assert ops["respond_threads"]["result"]["completed"] == ["NT_003"]
        world = json.loads((tmp / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        assert world["active_npc_threads"] == []
