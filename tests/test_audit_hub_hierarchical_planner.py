# -*- coding: utf-8 -*-
"""audit_hub_hierarchical_planner.py 脚手架测试 (R8 W4 Batch-H · L24 · 2026-06-20)。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import audit_hub_hierarchical_planner as mod  # noqa: E402

_TARGET = _SCRIPTS / "audit_hub_hierarchical_planner.py"


def _mk_project(*, summary_clusters=None, vol_arc=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if summary_clusters is not None:
        (proj / "_数据库" / "故事块摘要.json").write_text(
            json.dumps({"clusters": summary_clusters}, ensure_ascii=False),
            encoding="utf-8")
    if vol_arc is not None:
        (proj / "_数据库" / "大势卡.json").write_text(
            json.dumps(vol_arc, ensure_ascii=False), encoding="utf-8")
    return proj


# ── plan_global_pass ─────────────────────────────────────────────────────────
def test_global_pass_no_project_returns_skeleton():
    r = mod.plan_global_pass(None, "cluster_001")
    assert r["scanner"] == "audit_hub_hierarchical_planner"
    assert r["stage"] == "global_pass"
    assert r["global_risks"] == []
    assert r["priority_hints"] == {}
    assert "无 project_root" in r.get("note", "")


def test_global_pass_with_volume_arc_boosts_hints():
    proj = _mk_project(
        summary_clusters=[{
            "cluster_id": "cluster_001",
            "scope_summary": "本块讲主角进入育新中学。",
            "_me_volume": 1,
        }],
        vol_arc={"volumes": [{"id": 1, "title": "试炼之卷"}]})
    r = mod.plan_global_pass(proj, "cluster_001")
    assert r["has_brief"] is True
    assert any(g["axis"] == "volume_arc" for g in r["global_risks"])
    assert r["priority_hints"].get("VOLUME_ARC_DRIFT") == 1.0
    assert r["priority_hints"].get("PLOT_arc") == 1.0


def test_global_pass_with_causal_chain():
    proj = _mk_project(summary_clusters=[{
        "cluster_id": "cluster_002",
        "scope_summary": "续接前块魂线丢失·调查源头。",
        "cluster_emergence": {"caused_by": ["lost_soul_line"]},
    }])
    r = mod.plan_global_pass(proj, "cluster_002")
    assert any(g["axis"] == "causal_chain" for g in r["global_risks"])
    assert r["priority_hints"].get("FORESHADOWING_HANDOFF_NOT_PAID") == 1.0


def test_global_pass_with_antagonist_ladder():
    proj = _mk_project(summary_clusters=[{
        "cluster_id": "cluster_003",
        "antagonist_ladder": [{"name": "李教官", "level": 1}],
    }])
    r = mod.plan_global_pass(proj, "cluster_003")
    assert any(g["axis"] == "antagonist_ladder" for g in r["global_risks"])
    assert r["priority_hints"].get("ANTAGONIST_FIDELITY_FLAT") == 1.0


def test_global_pass_cluster_not_found_in_summary():
    proj = _mk_project(summary_clusters=[
        {"cluster_id": "cluster_999"},
    ])
    r = mod.plan_global_pass(proj, "cluster_001")
    assert r["has_brief"] is False


# ── coordinate_revision_plan ─────────────────────────────────────────────────
def test_coordinate_empty_issues():
    r = mod.coordinate_revision_plan([], {})
    assert r["total_issues"] == 0
    assert r["revision_items"] == []
    assert r["high_priority_count"] == 0


def test_coordinate_merges_same_code():
    issues = [
        {"code": "STYLE_单段超长", "severity": "minor", "gate_level": "hard_gate",
         "message": "段超长 1"},
        {"code": "STYLE_单段超长", "severity": "minor", "gate_level": "hard_gate",
         "message": "段超长 2"},
        {"code": "PROSE_RHYTHM", "severity": "minor", "gate_level": "advisory",
         "message": "节奏偏离"},
    ]
    r = mod.coordinate_revision_plan(issues, {})
    codes = [m["code"] for m in r["revision_items"]]
    assert "STYLE_单段超长" in codes
    assert "PROSE_RHYTHM" in codes
    # 同 code 合并
    super_long = [m for m in r["revision_items"] if m["code"] == "STYLE_单段超长"][0]
    assert super_long["count"] == 2
    # hard_gate 优先级高
    assert r["revision_items"][0]["code"] == "STYLE_单段超长"
    assert r["revision_items"][0]["priority"] >= 10


def test_coordinate_with_global_hints_boost():
    issues = [
        {"code": "VOLUME_ARC_DRIFT", "severity": "minor",
         "gate_level": "advisory", "message": "卷漂移"},
        {"code": "REPEAT_NOUN_DENSITY", "severity": "minor",
         "gate_level": "advisory", "message": "名词重复"},
    ]
    hints = {"VOLUME_ARC_DRIFT": 1.0}
    r = mod.coordinate_revision_plan(issues, hints)
    # VOLUME_ARC_DRIFT 应排在前 (hint boost)
    assert r["revision_items"][0]["code"] == "VOLUME_ARC_DRIFT"
    assert r["revision_items"][0]["priority"] >= 1.0
    assert r["high_priority_count"] >= 1


def test_coordinate_with_wildcard_hint():
    issues = [
        {"code": "FORESHADOWING_HANDOFF_NOT_PAID", "severity": "minor",
         "gate_level": "advisory", "message": "伏笔未付"},
    ]
    hints = {"FORESHADOWING_HANDOFF_*": 1.0}
    r = mod.coordinate_revision_plan(issues, hints)
    assert r["revision_items"][0]["code"] == "FORESHADOWING_HANDOFF_NOT_PAID"
    assert r["revision_items"][0]["priority"] >= 1.0


def test_coordinate_skips_non_dict_issues():
    issues = ["not a dict", {"code": "X", "gate_level": "advisory",
                             "severity": "minor", "message": ""}]
    r = mod.coordinate_revision_plan(issues, {})
    # 字符串被忽略·只有 1 个 item
    assert len(r["revision_items"]) == 1


# ── run_hierarchical 一站式 ──────────────────────────────────────────────────
def test_run_hierarchical_without_audit_result():
    proj = _mk_project(summary_clusters=[{
        "cluster_id": "cluster_001", "_me_volume": 1,
    }], vol_arc={"volumes": [{"id": 1}]})
    r = mod.run_hierarchical(proj, "cluster_001", audit_result=None)
    assert r["stages"]["global"]["stage"] == "global_pass"
    assert r["stages"]["coordinated"].get("deferred") is True


def test_run_hierarchical_with_audit_result():
    proj = _mk_project(summary_clusters=[{
        "cluster_id": "cluster_001", "_me_volume": 1,
    }], vol_arc={"volumes": [{"id": 1}]})
    audit = {"all_issues": [
        {"code": "VOLUME_ARC_DRIFT", "severity": "minor",
         "gate_level": "advisory", "message": "卷漂移"},
        {"code": "PROSE_RHYTHM", "severity": "minor",
         "gate_level": "advisory", "message": "节奏偏离"},
    ]}
    r = mod.run_hierarchical(proj, "cluster_001", audit)
    assert r["stages"]["coordinated"]["total_issues"] == 2
    # global hints 应该 boost VOLUME_ARC_DRIFT 到前列
    items = r["stages"]["coordinated"]["revision_items"]
    assert items[0]["code"] == "VOLUME_ARC_DRIFT"


# ── _mode 回落 ──────────────────────────────────────────────────────────────
def test_mode_default_shadow():
    bak = os.environ.get("HIERARCHICAL_AUDIT_MODE")
    try:
        os.environ.pop("HIERARCHICAL_AUDIT_MODE", None)
        assert mod._mode() == "shadow"
        os.environ["HIERARCHICAL_AUDIT_MODE"] = "bogus"
        assert mod._mode() == "shadow"
        os.environ["HIERARCHICAL_AUDIT_MODE"] = "active"
        assert mod._mode() == "active"
    finally:
        if bak is None:
            os.environ.pop("HIERARCHICAL_AUDIT_MODE", None)
        else:
            os.environ["HIERARCHICAL_AUDIT_MODE"] = bak


# ── _read_json 容错 ──────────────────────────────────────────────────────────
def test_read_json_missing_file():
    p = Path(tempfile.mkdtemp()) / "nope.json"
    assert mod._read_json(p) == {}


def test_read_json_bad_json():
    p = Path(tempfile.mkdtemp()) / "bad.json"
    p.write_text("{ bad json", encoding="utf-8")
    assert mod._read_json(p) == {}


def test_read_json_top_not_dict():
    p = Path(tempfile.mkdtemp()) / "list.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    assert mod._read_json(p) == {}


# ── CLI subprocess ──────────────────────────────────────────────────────────
def test_cli_runs_without_audit_result():
    proj = _mk_project(summary_clusters=[{"cluster_id": "cluster_001"}])
    r = subprocess.run(
        [sys.executable, str(_TARGET), "--project", str(proj), "--cluster", "cluster_001"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["cluster_key"] == "cluster_001"
    assert "global" in rep["stages"]


def test_cli_with_audit_result_file():
    proj = _mk_project(summary_clusters=[{"cluster_id": "cluster_001", "_me_volume": 1}])
    ar = Path(tempfile.mkdtemp()) / "audit.json"
    ar.write_text(json.dumps({"all_issues": [
        {"code": "PROSE_RHYTHM", "gate_level": "advisory",
         "severity": "minor", "message": "x"}]}, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_TARGET), "--project", str(proj),
         "--cluster", "cluster_001", "--audit-result", str(ar)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["stages"]["coordinated"]["total_issues"] == 1
