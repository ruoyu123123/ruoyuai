# -*- coding: utf-8 -*-
"""bibliotherapy_arc_scanner R19 W8 Batch-W·P1 Shrodes 三阶段回归测试。

确定性·零依赖。覆盖 off/短稿/无 brief skip/三相全填 PASS/各相缺位/scene 索引
越界/三相顺序错位/shadow active 切换/CLI/hard_gate 守卫。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import bibliotherapy_arc_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "bibliotherapy_arc_scanner.py"
_ENV = "BIBLIOTHERAPY_ARC_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_brief(arc=None, storyboard_len=5):
    d = Path(tempfile.mkdtemp())
    p = d / "brief.json"
    brief = {
        "id": "cluster_001",
        "scene_storyboard": [{"id": f"scene_{i}"} for i in range(storyboard_len)],
    }
    if arc is not None:
        brief["bibliotherapy_arc"] = arc
    p.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return p


_TEXT = ("一段普通文字。她抬头看远方。" * 80)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_TEXT))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_brief_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_TEXT))
        assert "无 cluster brief" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_full_triad_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(arc={
            "identification": [0, 1],
            "catharsis": [2, 3],
            "insight": [4],
        }, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_missing_identification():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(arc={
            "catharsis": [2, 3],
            "insight": [4],
        }, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        assert "BIBLIOTHERAPY_ARC_TRIAD_MISSING" in codes
        assert "identification" in r["metrics"]["missing_phases"]
    finally:
        _set_mode(bak)


def test_missing_all_three_when_arc_absent():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(arc=None, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        assert "BIBLIOTHERAPY_ARC_TRIAD_MISSING" in codes
        assert set(r["metrics"]["missing_phases"]) == {"identification", "catharsis", "insight"}
    finally:
        _set_mode(bak)


def test_scene_out_of_range():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(arc={
            "identification": [0],
            "catharsis": [2],
            "insight": [99],  # 越界
        }, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        assert "BIBLIOTHERAPY_ARC_TRIAD_MISSING" in codes
        assert r["metrics"]["out_of_range_phases"]
    finally:
        _set_mode(bak)


def test_phase_order_swapped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # catharsis 在 identification 之前
        brief = _mk_brief(arc={
            "identification": [3, 4],
            "catharsis": [1, 2],
            "insight": [0],
        }, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        assert r["metrics"]["order_error"] is not None
        codes = [v["code"] for v in r["violations"]]
        assert "BIBLIOTHERAPY_ARC_TRIAD_MISSING" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        brief = _mk_brief(arc=None, storyboard_len=5)
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_project_fallback_brief():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        ec = {"clusters": [{
            "id": "cluster_001",
            "scene_storyboard": [{"id": "s0"}, {"id": "s1"}, {"id": "s2"}],
            "bibliotherapy_arc": {
                "identification": [0], "catharsis": [1], "insight": [2],
            },
        }]}
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps(ec, ensure_ascii=False), encoding="utf-8")
        r = mod.scan(_write(_TEXT), project_root=proj)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_validate_arc_helper_empty():
    missing, oor, order = mod._validate_arc(None, 5)
    assert missing == ["identification", "catharsis", "insight"]
    assert order is None


def test_validate_arc_helper_clean():
    missing, oor, order = mod._validate_arc(
        {"identification": [0], "catharsis": [1], "insight": [2]}, 5)
    assert missing == []
    assert oor == []
    assert order is None


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs_shadow():
    r = _run_cli(_write(_TEXT))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "bibliotherapy_arc"
