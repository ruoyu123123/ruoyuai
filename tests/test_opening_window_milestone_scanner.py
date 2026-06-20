# -*- coding: utf-8 -*-
"""opening_window_milestone_scanner 专属回归(2026-06-20·R8 W4 Batch-J·L35)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import opening_window_milestone_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("OPENING_MILESTONE_MODE", None)
    else:
        os.environ["OPENING_MILESTONE_MODE"] = m


def _write(text, name="cluster_001_draft.txt"):
    d = Path(tempfile.mkdtemp())
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genre=None, skip=False):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genre or skip:
        obj = {}
        if genre:
            obj["genre"] = genre
        if skip:
            obj["opening_milestone_profile"] = {"skip": True}
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 大量伏笔+主角 goal/loss/threat
_HOOK_TEXT = (
    "他不知道这是怎么回事。失踪的人究竟在哪？为什么连尸体都不见？真相到底是什么？"
    "他必须找到答案。复仇必须开始。仇人盯上了他，杀机四伏。失去了家人，他绝望了。"
) * 50  # >> 10000 CJK
_FLAT_TEXT = "他走在街上，看到行人。他买了一杯咖啡。他坐下喝。" * 200


def test_off_returns_skeleton():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_HOOK_TEXT))
        assert out["mode"] == "off"
        assert "m1_hit" not in out
    finally:
        _set_mode(bak)


def test_cluster_001_with_hooks_pass():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HOOK_TEXT))
        assert out["m1_hit"] is True
        assert out["m2_hit"] is True
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_cluster_001_flat_fail():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_FLAT_TEXT))
        assert out["m1_hit"] is False
        assert out["m2_hit"] is False
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_non_cluster_001_skips():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        p = _write(_FLAT_TEXT, name="cluster_005_draft.txt")
        out = mod.scan(p)
        assert "非 cluster_001" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_override_skip_via_author_profile():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(skip=True)
        out = mod.scan(_write(_FLAT_TEXT), proj)
        assert "override" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_literary_genre_skips():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="literary")
        out = mod.scan(_write(_FLAT_TEXT), proj)
        assert "override" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_FLAT_TEXT))
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_cjk_prefix_returns_prefix():
    s = mod._cjk_prefix("abc你好世界中文。abc", 3)
    assert "你好世" in s


def test_read_failure_returns_note():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "cluster_001_nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("OPENING_MILESTONE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
