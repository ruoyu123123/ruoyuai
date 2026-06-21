# -*- coding: utf-8 -*-
"""dialogue_scene_manager R19 W8 Batch-X·P2 AdaMARP 多人对话编排·回归。

确定性·零依赖。覆盖 off/shadow/active mode/is_active 激活条件/
build_orchestrator_prompt/inject_orchestrator_prompt/append_turn_log/
validate_turn/scan_turn_log/CLI/hard_gate registry 守卫/gen_writer flag。
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
import dialogue_scene_manager as mod  # noqa: E402

_TARGET = _SCRIPTS / "dialogue_scene_manager.py"
_ENV = "DIALOGUE_ORCHESTRATOR_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def test_off_mode_default():
    bak = os.environ.get(_ENV)
    try:
        _set_mode(None)
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


def test_is_active_truthy():
    assert mod.is_active("confrontation", ["A", "B", "C"]) is True
    assert mod.is_active("court_session", ["甲", "乙", "丙", "丁"]) is True


def test_is_active_falsy():
    # 场景类型不在白名单
    assert mod.is_active("daily_chat", ["A", "B", "C"]) is False
    # 角色数 < 3
    assert mod.is_active("confrontation", ["A", "B"]) is False
    # 空
    assert mod.is_active(None, []) is False
    assert mod.is_active("confrontation", None) is False


def test_build_orchestrator_prompt_returns_content():
    p = mod.build_orchestrator_prompt("confrontation", ["小王", "李雷", "韩梅"])
    assert "[Thought]" in p
    assert "(Action)" in p
    assert "<<Environment>>" in p
    assert "Speech" in p
    assert "小王" in p
    assert "confrontation" in p


def test_build_orchestrator_prompt_empty_on_inactive():
    assert mod.build_orchestrator_prompt("daily_chat", ["A", "B", "C"]) == ""
    assert mod.build_orchestrator_prompt("confrontation", ["A"]) == ""


def test_inject_orchestrator_prompt_off_noop():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        manifest = {"x": 1}
        brief = {"scene_storyboard": [
            {"scene_type": "confrontation", "characters": ["A", "B", "C"]}
        ]}
        out = mod.inject_orchestrator_prompt(manifest, brief, {"A", "B", "C"})
        assert "dialogue_orchestrator" not in out
    finally:
        _set_mode(bak)


def test_inject_orchestrator_prompt_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        manifest = {}
        brief = {"scene_storyboard": [
            {"scene_type": "daily_chat", "characters": ["A", "B", "C"]},
            {"scene_type": "confrontation", "characters": ["小王", "李雷", "韩梅"]},
        ]}
        out = mod.inject_orchestrator_prompt(
            manifest, brief, {"小王", "李雷", "韩梅", "A", "B", "C"})
        assert "dialogue_orchestrator" in out
        assert len(out["dialogue_orchestrator"]["activated_scenes"]) == 1
        assert out["dialogue_orchestrator"]["activated_scenes"][0]["scene_idx"] == 1
    finally:
        _set_mode(bak)


def test_append_turn_log_creates_and_appends():
    proj = _mk_project()
    turns = [{"turn_id": 1, "speaker": "小王", "thought": "我必须告诉他",
              "action": "握紧拳头", "environment": "夜风冷冽",
              "speech": "我有事相告。", "word_count": 120}]
    p1 = mod.append_turn_log(proj, "cluster_001", 0, "confrontation",
                             ["小王", "李雷", "韩梅"], turns)
    assert p1.exists()
    data = json.loads(p1.read_text(encoding="utf-8"))
    assert len(data["scenes"]) == 1
    # append 第二次
    mod.append_turn_log(proj, "cluster_001", 1, "negotiation",
                        ["A", "B", "C"], turns)
    data2 = json.loads(p1.read_text(encoding="utf-8"))
    assert len(data2["scenes"]) == 2


def test_validate_turn_ok():
    turn = {"turn_id": 1, "speaker": "X", "thought": "t", "action": "a",
            "environment": "e", "speech": "s", "word_count": 150}
    v = mod.validate_turn(turn)
    assert v["issues"] == []


def test_validate_turn_missing_fields():
    v = mod.validate_turn({"turn_id": 1, "speaker": "X"})
    assert any("missing_thought" in i for i in v["issues"])
    assert any("missing_action" in i for i in v["issues"])


def test_validate_turn_word_count_bounds():
    v_short = mod.validate_turn({"turn_id": 1, "speaker": "X",
                                  "thought": "t", "action": "a",
                                  "environment": "e", "speech": "s",
                                  "word_count": 50})
    assert any("turn_too_short" in i for i in v_short["issues"])
    v_long = mod.validate_turn({"turn_id": 1, "speaker": "X",
                                 "thought": "t", "action": "a",
                                 "environment": "e", "speech": "s",
                                 "word_count": 500})
    assert any("turn_too_long" in i for i in v_long["issues"])


def test_scan_turn_log_off():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan_turn_log(_mk_project())
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_scan_turn_log_missing():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan_turn_log(_mk_project())
        assert r.get("note") == "log_missing"
    finally:
        _set_mode(bak)


def test_scan_turn_log_with_data():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project()
        # 制造 >30% 异常率
        good = {"turn_id": 1, "speaker": "A", "thought": "t", "action": "a",
                "environment": "e", "speech": "s", "word_count": 150}
        bad = {"turn_id": 2, "speaker": "B"}  # 缺字段
        mod.append_turn_log(proj, "cluster_001", 0, "confrontation",
                            ["A", "B", "C"], [good, bad, bad, bad])
        r = mod.scan_turn_log(proj)
        # shadow 不报 violations
        assert r["violations"] == []
        assert r["metrics"]["total_turns"] == 4
        assert r["metrics"]["issue_turns"] == 3
    finally:
        _set_mode(bak)


def test_cli_runs():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project()
        r = subprocess.run(
            [sys.executable, str(_TARGET), "--project", str(proj)],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
        assert r.returncode in (0, 1)
        assert "scanner" in r.stdout
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "DIALOGUE_ORCHESTRATOR_DEGRADED" not in set(data.get("hard_gate_codes", []))


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("dialogue_scene_manager")
    assert entry is not None
    assert entry.get("_new") is True


def test_gen_writer_has_dialogue_orchestrator_mode_flag():
    """守卫：gen_writer --dialogue-orchestrator-mode flag 必须存在。"""
    gw = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "--dialogue-orchestrator-mode" in gw
    assert "DIALOGUE_ORCHESTRATOR_MODE" in gw
