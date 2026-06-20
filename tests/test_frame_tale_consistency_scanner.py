# -*- coding: utf-8 -*-
"""frame_tale_consistency_scanner 专属回归(2026-06-20·R8 W4 Batch-J·L29)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import frame_tale_consistency_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("FRAME_TALE_MODE", None)
    else:
        os.environ["FRAME_TALE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(profile=None, genre=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if profile is not None:
        obj["nested_narrative_profile"] = profile
    if genre is not None:
        obj["genre"] = genre
    if obj:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


_BALANCED_TEXT = (
    "我给你讲讲那段往事。" + "故事正文段落。" * 100 +
    "言归正传，回到主线。"
)
_UNBALANCED_TEXT = (
    "我给你讲讲一个故事。话说当年我看到他。" + "故事内容延续。" * 100
)


def test_off_returns_skeleton():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(profile={"allowed_max_depth": 2,
                                     "entry_exit_required": True})
        out = mod.scan(_write(_BALANCED_TEXT), proj)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_profile_skips():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write(_BALANCED_TEXT), proj)
        assert "无 nested_narrative_profile" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_genre_default_enables_for_regression():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="regression")
        out = mod.scan(_write(_BALANCED_TEXT), proj)
        # 有 entry+exit 平衡 → PASS
        assert out["verdict"] == "PASS"
        assert "genre_default" in out["profile_source"]
    finally:
        _set_mode(bak)


def test_unbalanced_text_fail():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"allowed_max_depth": 2,
                                     "entry_exit_required": True})
        out = mod.scan(_write(_UNBALANCED_TEXT), proj)
        assert out["entry_count"] >= 1
        assert out["exit_count"] == 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_excessive_depth_fail():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"allowed_max_depth": 1,
                                     "entry_exit_required": False})
        text = ("讲起一个故事。" * 5) + ("正文段落延续。" * 100)
        out = mod.scan(_write(text), proj)
        assert out["entry_count"] >= 2
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(profile={"allowed_max_depth": 2,
                                     "entry_exit_required": True})
        out = mod.scan(_write(_UNBALANCED_TEXT), proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"allowed_max_depth": 2})
        out = mod.scan(_write("讲起一个故事。"), proj)
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_resolve_profile_from_genre():
    proj = _mk_project(genre="scheming_politics")
    p = mod._resolve_profile(proj)
    assert p["_default_for_genre"] == "scheming_politics"


def test_read_failure_returns_note():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("FRAME_TALE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
