# -*- coding: utf-8 -*-
"""premature_reader_reveal_scanner R22 W10 Batch-EE·P1 回归测试"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import premature_reader_reveal_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "premature_reader_reveal_scanner.py"
_ENV = "PREMATURE_READER_REVEAL_MODE"


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


def _mk_project(reader_ledger=None, locked_facts=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if reader_ledger is not None:
        (db / "读者信念账本.json").write_text(
            json.dumps(reader_ledger, ensure_ascii=False), encoding="utf-8")
    if locked_facts is not None:
        (db / "locked_fact.json").write_text(
            json.dumps({"facts": locked_facts}, ensure_ascii=False), encoding="utf-8")
    return proj


# 信号词「众所周知」+ fact_ref「秘密」（占位词典里）
_VIOLATING = "众所周知这个秘密。" * 80
# 信号词后无 fact_ref
_CLEAN = "众所周知的小事情。" * 80


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_VIOLATING), _mk_project())
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("众所周知秘密。"), _mk_project())
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_active_violation_fail_minor_reader_empty():
    """reader_known 为空·violating fact 命中信号词 → 报警"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_VIOLATING), _mk_project())
        assert out["violation_count"] >= 1
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "PREMATURE_READER_REVEAL"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_active_reader_known_no_violation():
    """reader_known 含秘密 → 同样信号词不报警"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(reader_ledger={"reader_known": ["秘密"]})
        out = mod.scan(_write(_VIOLATING), proj)
        # fact_ref 已在 reader_known → 不算 premature
        assert out["violation_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_violation_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_VIOLATING), _mk_project())
        assert out["violations"] == []
        assert out["warning"] is None
        # 仍记录 violation_count
        assert out["violation_count"] >= 1
    finally:
        _set_mode(bak)


def test_clean_draft_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_CLEAN), _mk_project())
        assert out["violation_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_locked_fact_priority_over_placeholder():
    """locked_fact 提供新 fact_ref·占位 fallback 不用"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(locked_facts=[{"key": "藏宝图"}])
        refs = mod._load_fact_refs(proj)
        assert "藏宝图" in refs
    finally:
        _set_mode(bak)


def test_load_fact_refs_fallback():
    refs = mod._load_fact_refs(None)
    assert "秘密" in refs


def test_load_reader_known_missing_ledger():
    proj = _mk_project()
    assert mod._load_reader_known(proj) == set()


def test_load_reader_known_invalid_json():
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "读者信念账本.json").write_text("not json", encoding="utf-8")
    assert mod._load_reader_known(proj) == set()


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


def test_cjk_count():
    assert mod._cjk_count("好abc世界") == 3


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_violation():
    proj = _mk_project()
    r = _run_cli(_write(_VIOLATING), proj)
    assert r.returncode == 1, r.stderr


def test_main_exit_0_on_clean():
    proj = _mk_project()
    r = _run_cli(_write(_CLEAN), proj)
    assert r.returncode == 0, r.stderr
