# -*- coding: utf-8 -*-
"""paratext_interpolation_scanner R11 W6 MODEST 回归"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import paratext_interpolation_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("PARATEXT_INTERPOLATION_MODE", None)
    else:
        os.environ["PARATEXT_INTERPOLATION_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genres):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"author_genre_packs": genres}, ensure_ascii=False),
        encoding="utf-8")
    return proj


# 散文叙事·无 paratext
_PLAIN = "他在山顶看着远方的雾。日子像被风带走。他什么也不想说。" * 30
# 含多类 paratext
_RICH = ("据档案记录他在战时常常独行。\n"
         "摄于一九三七年秋的那张照片。\n"
         "笔者采访某位老人，得知此事。\n"
         "档号一二三四五的卷宗已不可考。\n"
         "“这是他的原话。”一位幸存者回忆。\n") * 18


def test_off():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_RICH))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_trigger_genre_skip():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia"])
        out = mod.scan(_write(_RICH), proj)
        assert "非报告" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_trigger_genre_thin_flag():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["reportage"])
        out = mod.scan(_write(_PLAIN), proj)
        codes = [f["code"] for f in out.get("flags", [])]
        assert "PARATEXT_INTERPOLATION_THIN" in codes
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_trigger_genre_with_paratext_no_thin():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["nonfiction_documentary_lit"])
        out = mod.scan(_write(_RICH), proj)
        assert out["paratext_unit_density_per_1k"] > 0
        # thin 应不命中
        codes = [f["code"] for f in out.get("flags", [])]
        assert "PARATEXT_INTERPOLATION_THIN" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["reportage"])
        out = mod.scan(_write(_PLAIN), proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_detect_paratext():
    units = mod.detect_paratext("据档案记录他独行。摄于秋日。档号一二三。")
    kinds = {u["kind"] for u in units}
    assert "recorded_per" in kinds or "photo_caption" in kinds


def test_read_genre_signals_none():
    assert mod._read_genre_signals(None) == set()


def test_read_genre_signals_str_form():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"genre": "reportage"}, ensure_ascii=False), encoding="utf-8")
    sig = mod._read_genre_signals(proj)
    assert "reportage" in sig


def test_mode_invalid():
    bak = os.environ.get("PARATEXT_INTERPOLATION_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
