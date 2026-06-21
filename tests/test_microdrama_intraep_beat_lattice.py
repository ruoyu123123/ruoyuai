# -*- coding: utf-8 -*-
"""microdrama_intraep_beat_lattice · R24 W12 Batch-LL · P2
确定性·零依赖·零 LLM/零联网。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import microdrama_intraep_beat_lattice as mod  # noqa: E402

_TARGET = _SCRIPTS / "microdrama_intraep_beat_lattice.py"
_ENV = "MICRODRAMA_BEAT_LATTICE_MODE"


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


def _mk_project(fmt=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if fmt is not None:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps({"format": fmt}, ensure_ascii=False), encoding="utf-8")
    return proj


# 一个短剧体草稿·head_3s 有动作+矛盾·mid_15s 有反转·tail 有钩子
def _build_shortdrama_draft():
    head = "她抓起巴掌甩出，证据撕碎在桌上。" + "走" * 10
    mid = "原来真相是另一回事，没想到他翻脸。" + "走" * 100
    body = "走" * 200
    tail = "下集见，未完待续，敬请期待。"
    return head + mid + body + tail


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_build_shortdrama_draft()), _mk_project("shortdrama"))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "head_3s_cjk" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("短"), _mk_project("shortdrama"))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_non_shortdrama_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_build_shortdrama_draft()), _mk_project("prose"))
        assert out["format"] == "prose"
        # shadow + 非短剧体 → 不报 violation
        assert out.get("violations") == []
    finally:
        _set_mode(bak)


def test_non_shortdrama_active_emits_info():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_shortdrama_draft()), _mk_project("prose"))
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_NOT_SHORTDRAMA in codes
    finally:
        _set_mode(bak)


def test_shortdrama_good_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_shortdrama_draft()), _mk_project("shortdrama"))
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_HEAD_NO_ACTION not in codes
        assert mod.ISSUE_CODE_MID_NO_TWIST not in codes
        assert mod.ISSUE_CODE_TAIL_NO_HOOK not in codes
    finally:
        _set_mode(bak)


def test_shortdrama_missing_all_anchors():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        flat = "走" * 800
        out = mod.scan(_write(flat), _mk_project("shortdrama"))
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_HEAD_NO_ACTION in codes
        assert mod.ISSUE_CODE_MID_NO_TWIST in codes
        assert mod.ISSUE_CODE_TAIL_NO_HOOK in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations_recorded():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        flat = "走" * 800
        out = mod.scan(_write(flat), _mk_project("shortdrama"))
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_cli_format_override():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 项目=prose·CLI=shortdrama → 实际走短剧体
        out = mod.scan(_write("走" * 800), _mk_project("prose"), "shortdrama")
        assert out["format"] == "shortdrama"
    finally:
        _set_mode(bak)


def test_time_slice_buckets():
    text = "甲" * 700  # 100s 满 grid
    buckets = mod._slice_by_time(text)
    assert "0_3s" in buckets
    assert "3_15s" in buckets
    assert "60_90s" in buckets
    assert len(buckets["0_3s"]) == int(3 * mod.DEFAULT_CHAR_PER_SEC)


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_build_shortdrama_draft()), _mk_project("shortdrama"))
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_lexicons_placeholder():
    assert mod._ACTION_VERBS["_placeholder"] is True
    assert mod._CONFLICT_NOUNS["_placeholder"] is True
    assert mod._TWIST_TRIGGERS["_placeholder"] is True
    assert mod._HOOK_TRIGGERS["_placeholder"] is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_HEAD_NO_ACTION,
              mod.ISSUE_CODE_MID_NO_TWIST,
              mod.ISSUE_CODE_TAIL_NO_HOOK,
              mod.ISSUE_CODE_NOT_SHORTDRAMA):
        assert c not in audit_hub.HARD_GATE_CODES


def test_strip_changes_marker():
    text = "正文。" + "走" * 100 + "\n---CHANGES_FACTUAL---\n{...}"
    out = mod._strip_changes(text)
    assert "CHANGES" not in out


def test_cli_returns_json():
    p = _write(_build_shortdrama_draft())
    proj = _mk_project("shortdrama")
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "microdrama_intraep_beat_lattice"


def test_detect_format_default_prose():
    proj = _mk_project()
    assert mod._detect_format(proj, None) == "prose"


def test_is_shortdrama_aliases():
    assert mod._is_shortdrama("shortdrama")
    assert mod._is_shortdrama("microdrama")
    assert mod._is_shortdrama("script")
    assert mod._is_shortdrama("短剧")
    assert not mod._is_shortdrama("prose")
