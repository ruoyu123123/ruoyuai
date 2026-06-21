# -*- coding: utf-8 -*-
"""check_acr_frustration_consistency R22 W10 Batch-EE·P1 回归测试"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import check_acr_frustration_consistency as mod  # noqa: E402

_TARGET = _SCRIPTS / "check_acr_frustration_consistency.py"
_ENV = "ACR_FRUSTRATION_MODE"


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


def _mk_project(characters=None, profile=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    if profile is not None:
        (proj / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return proj


# autonomy 受挫→反抗反应（匹配）
_MATCH_DRAFT = "张三被迫接受。他抗拒到底，拒绝命令。" * 60

# autonomy 受挫→讨好反应（relatedness 反应）→ mismatch
_MISMATCH_DRAFT = "张三被迫接受命令。他迎合讨好地巴结对方。" * 60


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_MISMATCH_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_character_card_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_MISMATCH_DRAFT), _mk_project())
        assert out["verdict"] == "PASS"
        assert out["character_count"] == 0
    finally:
        _set_mode(bak)


def test_override_flag():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           profile={"_acr_frustration_override": True})
        out = mod.scan(_write(_MISMATCH_DRAFT), proj)
        assert "反类型豁免" in out["note"]
    finally:
        _set_mode(bak)


def test_shadow_mismatch_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_MISMATCH_DRAFT), proj)
        assert out["violations"] == []
        assert out["warning"] is None
        assert out["mismatch_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_mismatch_fail_minor():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_MISMATCH_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "ACR_FRUSTRATION_MISMATCH"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_match_no_mismatch():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_MATCH_DRAFT), proj)
        assert out["mismatch_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write("张三被迫。"), proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"
    assert mod._strip_changes("正文\n---CHANGES_FACTUAL---\nlog") == "正文"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2


def test_scan_character_windows_no_match():
    h = mod._scan_character_windows("无关文本", "张三")
    assert h["autonomy_frus"] == 0
    assert h["wrong_rx_windows"] == []


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三"}])
    r = _run_cli(_write(_MATCH_DRAFT), proj)
    assert r.returncode == 0, r.stderr


def test_main_exit_1_on_mismatch():
    proj = _mk_project(characters=[{"name": "张三"}])
    r = _run_cli(_write(_MISMATCH_DRAFT), proj)
    assert r.returncode == 1, r.stderr
