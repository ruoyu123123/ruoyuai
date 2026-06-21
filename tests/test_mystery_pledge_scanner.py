# -*- coding: utf-8 -*-
"""mystery_pledge_scanner R24 W12 Batch-JJ · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import mystery_pledge_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("MYSTERY_PLEDGE_MODE", None)
    else:
        os.environ["MYSTERY_PLEDGE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


_BODY = "他走在路上，看着前方的灯火，思绪却越发零乱。\n\n" * 60

_OPEN_ANOMALY = (
    "今天的事实在太诡异，所有的迹象都不对劲。\n"
    "究竟是谁在背后操纵这一切？莫非有人借刀杀人？\n\n"
)
_OPEN_COLD = "他走在路上，看着前方的灯火，思绪却越发零乱。\n\n"
_END_REVEAL = (
    "他终于明白，原来真相一直就藏在那本旧书里，揭开层层迷雾，"
    "答案竟是如此简单。\n\n"
)
_END_NO_REVEAL = "他依旧走在路上，一切照旧。\n\n"

_DRAFT_PLEDGE_KEPT = _OPEN_ANOMALY + _BODY + _END_REVEAL
_DRAFT_PLEDGE_DANGLING = _OPEN_ANOMALY + _BODY + _END_NO_REVEAL
_DRAFT_COLD_OPEN = _OPEN_COLD + _BODY + _END_NO_REVEAL


def test_off_returns_skeleton():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_PLEDGE_KEPT))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_PLEDGE_DANGLING))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_pledge_kept_info():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_PLEDGE_KEPT))
        assert out["state"] == "pledge_kept"
        codes = {v["code"] for v in out.get("violations", [])}
        assert "MYSTERY_PLEDGE_KEPT" in codes
        # info 不应 FAIL
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_active_pledge_dangling_minor():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_PLEDGE_DANGLING))
        assert out["state"] == "pledge_dangling"
        codes = {v["code"] for v in out.get("violations", [])}
        assert "MYSTERY_PLEDGE_DANGLING" in codes
        # minor 应 FAIL_MINOR
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_active_cold_open_info():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_COLD_OPEN))
        assert out["state"] == "cold_open"
        codes = {v["code"] for v in out.get("violations", [])}
        assert "MYSTERY_COLD_OPEN" in codes
    finally:
        _set_mode(bak)


def test_anomaly_hits_recorded():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_PLEDGE_KEPT))
        assert "诡异" in out["anomaly_seed_hits"] or "不对劲" in out["anomaly_seed_hits"]
        assert any(w in out["pledge_hits"] for w in ("究竟", "莫非"))
    finally:
        _set_mode(bak)


def test_reveal_hits_recorded():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_PLEDGE_KEPT))
        assert "原来" in out["reveal_hits"] or "真相" in out["reveal_hits"]
    finally:
        _set_mode(bak)


def test_take_open_within_limit():
    text = "一" * 500
    o = mod._take_open(text, 200)
    assert mod._cjk_count(o) <= 200


def test_take_end_within_limit():
    text = "一" * 1000
    e = mod._take_end(text, 500)
    assert mod._cjk_count(e) <= 500


def test_short_draft_skipped():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("MYSTERY_PLEDGE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("MYSTERY_PLEDGE_DANGLING", "MYSTERY_COLD_OPEN", "MYSTERY_PLEDGE_KEPT"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("mystery_pledge_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_lexicon():
    assert mod._LEXICONS.get("_placeholder") is True
