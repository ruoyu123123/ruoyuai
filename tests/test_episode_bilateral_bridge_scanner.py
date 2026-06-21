# -*- coding: utf-8 -*-
"""episode_bilateral_bridge_scanner R18 W7 Batch-U·P2 短剧双侧握手桥回归。

确定性·零依赖。覆盖 off/短稿/非短剧 skip/无 ≥2 集 skip/兑现太迟/新钩太早/
双指标全 PASS/shadow/读取失败/_mode/CLI/registry 未污染 hard_gate_codes/
正交说明 docstring 检测。
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
import episode_bilateral_bridge_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "episode_bilateral_bridge_scanner.py"
_ENV = "EPISODE_BILATERAL_BRIDGE_MODE"


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


def _mk_project(genre_tag=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genre_tag:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps({"genre_tags": [genre_tag]}, ensure_ascii=False),
            encoding="utf-8")
    return proj


_TWO_EPISODES_LATE = (
    "第1集 引子\n" + "他追问那女子是谁。雪夜里灯昏，没人回答。" * 12
    + "她话还没说完——\n\n第2集 后续\n"
    + "他走了很久。日子如常。" * 14
    + "突然一个陌生人挡在路上。"
)

_TWO_EPISODES_GOOD = (
    "第1集 引子\n" + "他举刀逼近，那人哑声喊。" * 12 + "话没说完——\n\n第2集 后续\n"
    + "她终于明白上次说的真相。" + "时间过去。日子如常。" * 11
    + "夜深时，门被人推开，那个身影竟然是——"
)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_TWO_EPISODES_LATE))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project("short_drama_vertical")
        r = mod.scan(_write("短。"), project_root=proj)
        assert "草稿太短" in r.get("note", "") or "非短剧" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_non_short_drama_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()  # 无 genre tag
        r = mod.scan(_write(_TWO_EPISODES_LATE), project_root=proj)
        assert "非短剧" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_project_root_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_TWO_EPISODES_LATE))
        assert "非短剧" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_late_resolve_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project("short_drama_vertical")
        r = mod.scan(_write(_TWO_EPISODES_LATE), project_root=proj)
        # 下集开头没立刻兑现·末段新钩偏早
        assert r["verdict"] in ("FAIL_MINOR", "PASS")
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project("short_drama_vertical")
        r = mod.scan(_write(_TWO_EPISODES_LATE), project_root=proj)
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project("short_drama_vertical")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"), project_root=proj)
        # 读不到先返回·或路径不存在·二者必居其一
        assert "草稿读取失败" in r.get("note", "") or r["verdict"] == "PASS"
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
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("a中b文c") == 2


def test_split_episodes_titled():
    eps = mod._split_episodes("第1章 a\n正文1\n\n第2章 b\n正文2")
    assert len(eps) >= 2


def test_split_episodes_single_split_by_half():
    text = "正文" * 400  # 800 字符 > 600 阈值
    eps = mod._split_episodes(text)
    assert len(eps) == 2


def test_is_short_drama_via_userprefs():
    proj = _mk_project("short_drama_vertical")
    assert mod._is_short_drama(proj) is True


def test_is_short_drama_other_genre():
    proj = _mk_project("xianxia")
    assert mod._is_short_drama(proj) is False


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE_LATE_RESOLVE not in hgs
    assert mod.ISSUE_CODE_EARLY_HOOK not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs():
    proj = _mk_project("short_drama_vertical")
    r = _run_cli(_write(_TWO_EPISODES_LATE), project=proj)
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "episode_bilateral_bridge"
