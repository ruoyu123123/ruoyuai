# -*- coding: utf-8 -*-
"""active_character_wm_load_scanner R18 W7 Batch-U·P2 Cowan WM 回归。

确定性·零依赖。覆盖 off/短稿/无角色池 skip/超阈值 advisory/阈值内 PASS/
作者档 wm_load_tolerance 旁路/shadow 不上报/_split_scenes/_load_characters/
strip_changes/_mode/CLI/registry 未污染 hard_gate_codes。
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
import active_character_wm_load_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "active_character_wm_load_scanner.py"
_ENV = "ACTIVE_CHARACTER_WM_MODE"


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


def _mk_project(characters=None, tolerance=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged_characters":
                        [{"name": n} for n in characters]},
                       ensure_ascii=False), encoding="utf-8")
    if tolerance is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"wm_load_tolerance": tolerance}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 6 个活跃角色 · 单场景 · 超 Cowan 上限 5
_SCENE_OVERLOAD = (
    "阿正说话，阿丙听着，阿丁也来了。" * 30
    + "阿戊和阿己一起进门，阿庚也跟着。" * 30)

# 3 个活跃角色 · 单场景 · 在阈值内
_SCENE_OK = (
    "阿正说话，阿丙听着。" * 30 + "阿丁也来了。" * 30)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_SCENE_OK))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短稿。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_characters_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        r = mod.scan(_write(_SCENE_OVERLOAD), project_root=proj)
        assert "无角色池" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_overload_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(
            characters=["阿正", "阿丙", "阿丁", "阿戊", "阿己", "阿庚"])
        r = mod.scan(_write(_SCENE_OVERLOAD), project_root=proj)
        # 单场景 6 个角色 > 5 → 报
        assert r["verdict"] == "FAIL_MINOR"
        assert "认知" in (r["warning"] or "") or "Cowan" in (r["warning"] or "")
    finally:
        _set_mode(bak)


def test_within_threshold_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=["阿正", "阿丙", "阿丁"])
        r = mod.scan(_write(_SCENE_OK), project_root=proj)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_tolerance_bypass():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 阈值放到 10 → 6 角色不报
        proj = _mk_project(
            characters=["阿正", "阿丙", "阿丁", "阿戊", "阿己", "阿庚"],
            tolerance=10)
        r = mod.scan(_write(_SCENE_OVERLOAD), project_root=proj)
        assert r["author_threshold"] == 10
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(
            characters=["阿正", "阿丙", "阿丁", "阿戊", "阿己", "阿庚"])
        r = mod.scan(_write(_SCENE_OVERLOAD), project_root=proj)
        assert r["violations"] == []
        assert r["warning"] is None
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


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_load_characters_aliases():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "角色池.json").write_text(
        json.dumps({"emerged_characters":
                    [{"name": "阿正", "aliases": ["小正", "正哥"]}]},
                   ensure_ascii=False), encoding="utf-8")
    names = mod._load_characters(proj)
    assert "阿正" in names
    assert "小正" in names


def test_load_tolerance_default():
    proj = _mk_project()
    assert mod._load_tolerance(proj) == 5


def test_load_tolerance_clamps_out_of_range():
    proj = _mk_project(tolerance=99)
    # 超 12 → 用默认
    assert mod._load_tolerance(proj) == 5


def test_split_scenes_with_marker():
    text = "# scene 1\n场景一内容\n# scene 2\n场景二内容"
    scenes = mod._split_scenes(text)
    assert len(scenes) >= 2


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_warns():
    proj = _mk_project(characters=["阿正", "阿丙", "阿丁", "阿戊", "阿己", "阿庚"])
    r = _run_cli(_write(_SCENE_OVERLOAD), project=proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
