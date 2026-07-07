#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_write_gate.py 回归测试（A2 · 写前 Evolution Gate + 声明式豁免 · 2026-07-07）

契约：
    python core/scripts/pre_write_gate.py <project> --next-key <key>
    - 死亡角色上台 / 销毁道具 / 锁定事实恒定数值冲突 → blocking exit 2（写前拒绝·非审计 issue）
    - 重复事件嫌疑 → warning 只记不拦
    - brief.gate_waivers 声明式豁免 → 放行留痕 waived[]（北极星⑤ 创作声明权）
    - cluster_001 首块 → 优雅 skip exit 0（报告仍落盘）
    - 报告恒落盘 _数据库/.wal/cluster_<key>_pre_write_gate.json
    - plan step1 接线 + event_cluster_schema gate_waivers 契约
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import pre_write_gate as mod  # noqa: E402

DB = "_数据库"
EVENT_CLUSTER = "事件簇.json"
CHAR_CARDS = "人物卡.json"
ITEMS = "道具.json"
FATE = "大势卡.json"


def _mk_project(root: Path, *, brief: dict | None = None,
                characters: list | None = None, items: list | None = None,
                major_events: list | None = None, arc_state: dict | None = None,
                extra_clusters: list | None = None) -> Path:
    db = root / DB
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    clusters = [{"cluster_id": "cluster_001", "chapter_range": [1, 5], "status": "completed",
                 "scope_summary": "主角陈默进入育新中学调查失踪案，发现地下室的红色档案盒"}]
    clusters.extend(extra_clusters or [])
    if brief is not None:
        clusters.append(brief)
    (db / EVENT_CLUSTER).write_text(
        json.dumps({"schema_version": "v23.0", "clusters": clusters}, ensure_ascii=False),
        encoding="utf-8")
    (db / CHAR_CARDS).write_text(
        json.dumps({"characters": characters or []}, ensure_ascii=False), encoding="utf-8")
    (db / ITEMS).write_text(
        json.dumps({"items": items or []}, ensure_ascii=False), encoding="utf-8")
    (db / FATE).write_text(
        json.dumps({"major_events": major_events or []}, ensure_ascii=False), encoding="utf-8")
    if arc_state is not None:
        (db / "character_arc_state.json").write_text(
            json.dumps(arc_state, ensure_ascii=False), encoding="utf-8")
    return root


def _run(project: Path, key: str = "002") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main([str(project), "--next-key", key])
    return code, out.getvalue(), err.getvalue()


def _report(project: Path, key: str = "002") -> dict:
    p = project / DB / ".wal" / f"cluster_{key}_pre_write_gate.json"
    assert p.exists(), f"gate 报告未落盘: {p}"
    return json.loads(p.read_text(encoding="utf-8"))


def _base_brief(**overrides) -> dict:
    brief = {
        "cluster_id": "cluster_002",
        "status": "in_progress",
        "scope_summary": "陈默带着档案盒去南郊墓地，与老周对峙，揭开小李子之死的真相",
        "characters_focus": ["陈默", "老周"],
        "scene_storyboard": [
            {"scene_index": 0, "summary": "陈默在墓地等待", "characters": ["陈默"]},
            {"scene_index": 1, "summary": "老周现身对峙", "characters": ["陈默", "老周"]},
        ],
        "anchor_props": ["红色档案盒 ITM_003"],
    }
    brief.update(overrides)
    return brief


# ───────────────────── ① 死亡角色拦截 ─────────────────────
def test_dead_character_in_cast_blocks():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td), brief=_base_brief(characters_focus=["陈默", "沈铖"]),
            characters=[
                {"id": "C_CHEN", "name": "陈默", "status": "alive"},
                {"id": "C_SHEN", "name": "沈铖", "status": "dead"},
            ])
        code, _out, err = _run(project)
        assert code == 2, f"死亡角色上台应 exit 2, got {code}"
        assert "[FATAL]" in err and "沈铖" in err
        rep = _report(project)
        assert rep["verdict"] == "blocked"
        assert any(f["type"] == "dead_character" and f["target"] == "沈铖"
                   for f in rep["blocking"])


def test_dead_character_from_arc_state_and_scene_participants_blocks():
    """character_arc_state 死亡源（cee._dead_actor_names 同源）+ storyboard participants 通路。"""
    with tempfile.TemporaryDirectory() as td:
        brief = _base_brief()
        brief["scene_storyboard"].append(
            {"scene_index": 2, "summary": "旧人重逢", "participants": ["李暴躁"]})
        project = _mk_project(
            Path(td), brief=brief,
            characters=[{"id": "C_CHEN", "name": "陈默", "status": "alive"}],
            arc_state={"characters": {"李暴躁": {"status": "牺牲", "current_stage": "died"}}})
        code, _out, _err = _run(project)
        assert code == 2
        rep = _report(project)
        assert any(f["type"] == "dead_character" and f["target"] == "李暴躁"
                   for f in rep["blocking"])


def test_dead_character_text_mention_only_warns():
    """死角色只在文本被提及（未上台）→ warning 不拦（回忆/复仇动机合法）。"""
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td),
            brief=_base_brief(scope_summary="陈默为沈铖复仇，带着档案盒去南郊墓地找老周对峙"),
            characters=[
                {"id": "C_CHEN", "name": "陈默", "status": "alive"},
                {"id": "C_SHEN", "name": "沈铖", "status": "dead"},
            ])
        code, _out, _err = _run(project)
        assert code == 0, "文本提及不应拦截"
        rep = _report(project)
        assert rep["verdict"] == "pass"
        assert any(w["type"] == "dead_character_mention" and w["target"] == "沈铖"
                   for w in rep["warnings"])


# ───────────────────── ② 销毁道具拦截 ─────────────────────
def test_destroyed_item_in_anchor_props_blocks():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td), brief=_base_brief(),
            items=[{"id": "ITM_003", "name": "红色档案盒", "holder": "陈默",
                    "status": "destroyed"}])
        code, _out, err = _run(project)
        assert code == 2
        assert "红色档案盒" in err
        rep = _report(project)
        assert any(f["type"] == "destroyed_item" and f["target"] == "红色档案盒"
                   for f in rep["blocking"])


def test_intact_item_passes():
    """无 status 字段（apply_archive 现状）= 道具健在 → 全过。"""
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td), brief=_base_brief(),
            items=[{"id": "ITM_003", "name": "红色档案盒", "holder": "陈默"}])
        code, _out, _err = _run(project)
        assert code == 0
        assert _report(project)["verdict"] == "pass"


# ───────────────────── ③ 锁定事实恒定数值冲突 ─────────────────────
def test_locked_fact_numeric_conflict_blocks():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td),
            brief=_base_brief(scope_summary="老周五十八岁生日当晚，陈默带着档案盒上门与他对峙摊牌"),
            characters=[
                {"id": "C_CHEN", "name": "陈默", "status": "alive"},
                {"id": "C_ZHOU", "name": "老周", "status": "alive",
                 "locked_facts": [{"fact": "老周今年52岁"}]},
            ])
        code, _out, err = _run(project)
        assert code == 2, f"锁定事实数值冲突应 exit 2, got {code}"
        assert "老周" in err
        rep = _report(project)
        assert any(f["type"] == "locked_fact_conflict" and f["target"] == "老周"
                   for f in rep["blocking"])


def test_locked_fact_consistent_value_passes():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td),
            brief=_base_brief(scope_summary="老周五十二岁生日当晚，陈默带着档案盒上门与他对峙摊牌"),
            characters=[{"id": "C_ZHOU", "name": "老周", "status": "alive",
                         "locked_facts": [{"fact": "老周今年52岁"}]}])
        code, _out, _err = _run(project)
        assert code == 0
        assert _report(project)["verdict"] == "pass"


# ───────────────────── ④ 重复事件 warning 不拦 ─────────────────────
def test_duplicate_event_warns_but_not_blocking():
    with tempfile.TemporaryDirectory() as td:
        scope = "陈默潜入育新中学地下室寻找三十一名临时工死亡名单档案"
        project = _mk_project(
            Path(td), brief=_base_brief(scope_summary=scope),
            major_events=[{"id": "ME-V1-1", "volume": 1, "status": "completed",
                           "title": "陈默潜入育新中学地下室寻找临时工死亡名单档案"}])
        code, _out, _err = _run(project)
        assert code == 0, "重复事件嫌疑是 warning 只记不拦"
        rep = _report(project)
        assert rep["verdict"] == "pass"
        dup = [w for w in rep["warnings"] if w["type"] == "duplicate_event"]
        assert dup and dup[0]["target"] == "ME:ME-V1-1"
        assert dup[0]["similarity"] >= 0.6


# ───────────────────── 声明式豁免 ─────────────────────
def test_waiver_releases_dead_character_with_trace():
    """gate_waivers 声明（叙事手法别名 flashback）→ 放行 exit 0 + waived[] 留痕。"""
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td),
            brief=_base_brief(
                characters_focus=["陈默", "沈铖"],
                gate_waivers=[{"type": "flashback", "target": "沈铖", "reason": "闪回场景"}]),
            characters=[
                {"id": "C_CHEN", "name": "陈默", "status": "alive"},
                {"id": "C_SHEN", "name": "沈铖", "status": "dead"},
            ])
        code, out, _err = _run(project)
        assert code == 0, "有对应豁免应放行"
        assert "豁免" in out
        rep = _report(project)
        assert rep["verdict"] == "pass"
        assert not rep["blocking"]
        waived = [w for w in rep["waived"] if w["type"] == "dead_character"]
        assert waived and waived[0]["waived_by"]["reason"] == "闪回场景"


def test_blanket_waiver_without_target_does_not_bypass_blocking():
    """blocking 类豁免缺 target = 非法（禁整类空白支票）→ 仍拦 + invalid_waivers 留痕。"""
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td),
            brief=_base_brief(
                characters_focus=["沈铖"],
                gate_waivers=[{"type": "dead_character", "reason": "都是闪回"}]),
            characters=[{"id": "C_SHEN", "name": "沈铖", "status": "dead"}])
        code, _out, _err = _run(project)
        assert code == 2, "无 target 的 blocking 豁免不得放行"
        rep = _report(project)
        assert rep["invalid_waivers"], "非法豁免必须留痕"


# ───────────────────── skip / 干净通过 / 契约 ─────────────────────
def test_cluster_001_graceful_skip_with_report():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(Path(td))
        code, out, _err = _run(project, key="001")
        assert code == 0
        assert "skip" in out.lower()
        rep = _report(project, key="001")
        assert rep["skipped"] is True and rep["verdict"] == "skipped"


def test_clean_brief_passes_and_reports():
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(
            Path(td), brief=_base_brief(),
            characters=[{"id": "C_CHEN", "name": "陈默", "status": "alive"},
                        {"id": "C_ZHOU", "name": "老周", "status": "alive"}])
        code, out, _err = _run(project)
        assert code == 0
        assert "PASS" in out
        rep = _report(project)
        assert rep["verdict"] == "pass"
        assert rep["blocking"] == [] and rep["waived"] == []


def test_missing_brief_is_fatal():
    """cluster_002+ 在 事件簇.json 找不到 brief = 流水线顺序契约破损 → exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        project = _mk_project(Path(td), brief=None)
        code, _out, err = _run(project)
        assert code == 2
        assert "[FATAL]" in err
        rep = _report(project)
        assert any(f["type"] == "brief_missing" for f in rep["blocking"])


def test_plan_step1_wires_gate_between_apply_card_and_fate_draw():
    """cluster-write.plan.json step1：gate 行位于 world_evolution_apply_card 之后、
    auto_fate_draw 之前；expected_outputs 含 gate 产物。"""
    plan = json.loads((_ROOT / "core" / "claude-home" / "plans" / "cluster-write.plan.json")
                      .read_text(encoding="utf-8"))
    step1 = next(s for s in plan["steps"] if s["n"] == 1)
    scripts = step1["scripts"]
    idx = {}
    for i, line in enumerate(scripts):
        for name in ("world_evolution_apply_card.py", "pre_write_gate.py", "auto_fate_draw.py"):
            if name in line:
                idx[name] = i
    assert set(idx) == {"world_evolution_apply_card.py", "pre_write_gate.py", "auto_fate_draw.py"}
    assert idx["world_evolution_apply_card.py"] < idx["pre_write_gate.py"] < idx["auto_fate_draw.py"]
    assert "--next-key {key}" in scripts[idx["pre_write_gate.py"]]
    assert "_数据库/.wal/cluster_{key}_pre_write_gate.json" in step1["expected_outputs"]


def test_event_cluster_schema_declares_gate_waivers():
    """event_cluster_schema.json：gate_waivers optional 字段存在且 items 要求 type+reason。"""
    schema = json.loads((_ROOT / "core" / "claude-home" / "schemas" / "event_cluster_schema.json")
                        .read_text(encoding="utf-8"))
    props = schema["properties"]["clusters"]["items"]["properties"]
    gw = props.get("gate_waivers")
    assert gw is not None, "schema 缺 gate_waivers 字段"
    assert gw["type"] == "array"
    assert set(gw["items"]["required"]) == {"type", "reason"}
    required = schema["properties"]["clusters"]["items"]["required"]
    assert "gate_waivers" not in required, "gate_waivers 必须 optional（历史产物无此字段合法）"


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
