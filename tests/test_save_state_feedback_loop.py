"""save-state 反馈环回写回归测试 — [#6 chapter_hub_log 回写 · #7 storyteller_alignment 消费]。

两个孤儿字段同型：writer 在 _changes.json 申报了，但 save-state 流程无消费方 → 反馈环静默断裂。
参照已修的 thread_responded（world_evolution_apply_chapter.respond_threads）范式：纯数据回写，
不碰模型创作判断（北极星⑤⑥），缺字段/缺文件只 skip 不报错（advisory · 不阻断流水线）。

[#6] 钉死 chapter_hub 回写 chapter_hub_log：
  · writer factual.chapter_hub {hub_id, role} → 枢纽场景.json.chapter_hub_log append
  · 原状态：全仓无任何脚本 append → chapter_hub_log 永停种子值，build_manifest 注入给下章
    writer 的 recent_chapter_roles 永远只有 outline 初始几条 → Hub 节奏闭环断裂
  · 按 ch 去重幂等（cluster 逐章重放 / WAL 续跑第二次进来覆盖而非重复 append）
  · 枢纽场景.json 不存在 / chapter_hub 缺 hub_id/role → skip 不报错

[#7] 钉死 storyteller_alignment.actual_outcome 有消费方：
  · narrator_calibrate.infer_outcome_from_changes 优先取 writer 申报的 actual_outcome
  · 未申报才回退 heuristic（向后兼容）
  · 落 chapter_outcome_log 时标 outcome_source（writer_declared / inferred）供审计
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import world_evolution_apply_chapter as wac  # noqa: E402
import narrator_calibrate as nc  # noqa: E402


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _write_changes(project: Path, ch: int, changes: dict) -> None:
    ch_dir = project / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / f"第{ch:03d}章_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False), encoding="utf-8")


def _mk_hub_project(tmp: Path, seed_log: list | None = None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    hubs = {
        "hubs": [
            {"hub_id": "HUB_home", "label": "公寓", "type": "home"},
            {"hub_id": "HUB_office", "label": "办公室", "type": "workplace"},
        ],
        "chapter_hub_log": seed_log if seed_log is not None else [],
    }
    (db / "枢纽场景.json").write_text(json.dumps(hubs, ensure_ascii=False), encoding="utf-8")
    # apply_one_chapter / main 预检需要世界状态 + 涟漪规则
    (db / "世界状态.json").write_text(json.dumps(
        {"schema_version": "v20.1", "current_world_time": {"ch": 1}}, ensure_ascii=False),
        encoding="utf-8")
    (db / "涟漪规则.json").write_text(json.dumps({"ripple_rules": []}, ensure_ascii=False),
                                     encoding="utf-8")
    return tmp


def _read_hub_log(project: Path) -> list:
    data = json.loads((project / "_数据库" / "枢纽场景.json").read_text(encoding="utf-8"))
    return data.get("chapter_hub_log", [])


# ═══════════════════════ [#6] chapter_hub 回写 ═══════════════════════

def test_append_hub_log_writes_entry():
    """writer 申报 chapter_hub {hub_id, role} → append 到 chapter_hub_log。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d))
        r = wac.append_hub_log(tmp, 7, {"hub_id": "HUB_office", "role": "depart"})
        assert r["appended"] is True
        log = _read_hub_log(tmp)
        assert len(log) == 1
        assert log[0]["ch"] == 7
        assert log[0]["hub_id"] == "HUB_office"
        assert log[0]["role"] == "depart"


def test_append_hub_log_idempotent_by_ch():
    """同 ch 第二次进来覆盖而非重复 append（cluster 逐章重放 / WAL 续跑安全）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d))
        wac.append_hub_log(tmp, 7, {"hub_id": "HUB_office", "role": "depart"})
        wac.append_hub_log(tmp, 7, {"hub_id": "HUB_home", "role": "return"})  # 同 ch 覆盖
        log = _read_hub_log(tmp)
        assert len(log) == 1  # 不重复
        assert log[0]["role"] == "return"  # 后写覆盖前写


def test_append_hub_log_keeps_seed_and_sorts():
    """append 保留 outline 种子条目 + 按 ch 排序（供 build_manifest 取最近 N 条）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d), seed_log=[
            {"ch": 1, "hub_id": "HUB_home", "role": "idle"},
        ])
        wac.append_hub_log(tmp, 3, {"hub_id": "HUB_office", "role": "quest"})
        wac.append_hub_log(tmp, 2, {"hub_id": "HUB_home", "role": "return"})
        log = _read_hub_log(tmp)
        assert [e["ch"] for e in log] == [1, 2, 3]  # 排序
        assert len(log) == 3  # 种子保留


def test_append_hub_log_missing_fields_skips():
    """chapter_hub 缺 hub_id / role → skip 不报错，不污染 log。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d))
        assert wac.append_hub_log(tmp, 7, {"hub_id": "HUB_office"})["appended"] is False
        assert wac.append_hub_log(tmp, 7, {"role": "depart"})["appended"] is False
        assert wac.append_hub_log(tmp, 7, {})["appended"] is False
        assert _read_hub_log(tmp) == []


def test_append_hub_log_no_hubs_file_is_advisory():
    """枢纽场景.json 不存在（未启用 Hub 系统）→ skip 不报错。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        r = wac.append_hub_log(tmp, 7, {"hub_id": "HUB_office", "role": "depart"})
        assert r["appended"] is False
        assert "不存在" in r["reason"]


def test_apply_one_chapter_wires_chapter_hub():
    """端到端：apply_one_chapter 从 _changes.factual.chapter_hub 回写 chapter_hub_log。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d))
        _write_changes(tmp, 5, {
            "factual": {"chapter_hub": {"hub_id": "HUB_home", "role": "return"}},
            "self_eval": {},
        })
        summary, _half = wac.apply_one_chapter(tmp, 5)
        ops = {o["op"]: o for o in summary["ops"]}
        assert "append_hub_log" in ops
        assert ops["append_hub_log"]["result"]["appended"] is True
        log = _read_hub_log(tmp)
        assert log[-1] == {
            "ch": 5, "hub_id": "HUB_home", "role": "return",
            "_note": "auto-written from _changes.factual.chapter_hub",
        }


def test_apply_one_chapter_no_chapter_hub_noop():
    """无 factual.chapter_hub → 不产生 append_hub_log op（不污染 log）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_hub_project(Path(d))
        _write_changes(tmp, 5, {"factual": {}, "self_eval": {}})
        summary, _half = wac.apply_one_chapter(tmp, 5)
        ops = {o["op"] for o in summary["ops"]}
        assert "append_hub_log" not in ops
        assert _read_hub_log(tmp) == []


# ═══════════════════════ [#7] storyteller_alignment 消费 ═══════════════════════

def test_infer_outcome_prefers_writer_declared():
    """writer 申报 storyteller_alignment.actual_outcome → 优先消费（不再 heuristic 重推断）。"""
    # factual 信号会让 heuristic 推 win（fate_count>=1），但 writer 申报 setback → 应取 setback
    changes = {
        "factual": {"fate_events_triggered": [{"event_id": "V1_ME_001"}]},
        "self_eval": {"storyteller_alignment": {"actual_outcome": "setback"}},
    }
    outcome, intensity = nc.infer_outcome_from_changes(changes)
    assert outcome == "setback"  # writer 申报压过 heuristic
    assert intensity >= 1


def test_infer_outcome_falls_back_to_heuristic():
    """writer 未申报 actual_outcome → 回退 heuristic（向后兼容老 changes）。"""
    changes = {
        "factual": {"fate_events_triggered": [{"event_id": "V1_ME_001"}]},
        "self_eval": {},  # 无 storyteller_alignment
    }
    outcome, _ = nc.infer_outcome_from_changes(changes)
    assert outcome == "win"  # heuristic: fate_count>=1 → win


def test_infer_outcome_ignores_invalid_declared():
    """writer 申报非法值（如 auto / 空）→ 不消费，回退 heuristic。"""
    changes = {
        "factual": {},
        "self_eval": {"storyteller_alignment": {"actual_outcome": "auto"}},  # 非 setback/win/neutral
    }
    outcome, _ = nc.infer_outcome_from_changes(changes)
    assert outcome == "neutral"  # heuristic 兜底（无信号）


def test_calibrate_logs_outcome_source_writer_declared():
    """calibrate 落 chapter_outcome_log 时标 outcome_source=writer_declared（审计可追溯）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = tmp / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "叙事节拍器.json").write_text(json.dumps({
            "storyteller_profile": "cassandra",
            "current_pressure_phase": "rising",
            "since_phase_change_ch": 1,
            "chapter_outcome_log": [],
            "adaptation_factor": {"recent_n_chapters": 10, "expected_setback_per_n_ch": 4,
                                  "tolerance_window": 2},
        }, ensure_ascii=False), encoding="utf-8")
        _write_changes(tmp, 5, {
            "factual": {},
            "self_eval": {"storyteller_alignment": {"actual_outcome": "win"}},
        })
        r = nc.calibrate(tmp, 5)
        assert r["outcome_inferred"] == "win"
        assert r["outcome_source"] == "writer_declared"
        pacer = json.loads((db / "叙事节拍器.json").read_text(encoding="utf-8"))
        entry = next(e for e in pacer["chapter_outcome_log"] if e["ch"] == 5)
        assert entry["outcome"] == "win"
        assert entry["outcome_source"] == "writer_declared"


def test_calibrate_logs_outcome_source_inferred():
    """未申报时 outcome_source=inferred（与 writer_declared 区分）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = tmp / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "叙事节拍器.json").write_text(json.dumps({
            "chapter_outcome_log": [],
            "adaptation_factor": {"recent_n_chapters": 10, "expected_setback_per_n_ch": 4,
                                  "tolerance_window": 2},
        }, ensure_ascii=False), encoding="utf-8")
        _write_changes(tmp, 5, {"factual": {}, "self_eval": {}})
        r = nc.calibrate(tmp, 5)
        assert r["outcome_source"] == "inferred"


# ═══════════════════════ schema 契约 ═══════════════════════

def test_schema_defines_chapter_hub_and_storyteller_alignment():
    """changes_schema.json 必须定义这两个字段（writer 申报契约的来源）。"""
    sch = json.loads((_ROOT / "core" / "claude-home" / "schemas"
                      / "changes_schema.json").read_text(encoding="utf-8"))
    factual = sch["properties"]["factual"]["properties"]
    assert "chapter_hub" in factual
    hub_props = factual["chapter_hub"]["properties"]
    assert "hub_id" in hub_props and "role" in hub_props
    assert set(hub_props["role"]["enum"]) == {"depart", "quest", "return", "idle"}

    se = sch["properties"]["self_eval"]["properties"]
    assert "storyteller_alignment" in se
    sa_props = se["storyteller_alignment"]["properties"]
    assert "actual_outcome" in sa_props
    assert set(sa_props["actual_outcome"]["enum"]) == {"setback", "win", "neutral"}
