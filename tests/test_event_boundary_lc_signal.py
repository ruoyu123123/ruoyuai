# -*- coding: utf-8 -*-
"""event_boundary_lc_signal R23 W11 Batch-HH · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import event_boundary_lc_signal as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("EVENT_BOUNDARY_LC_MODE", None)
    else:
        os.environ["EVENT_BOUNDARY_LC_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_manifest(sharpness=None, finale=False):
    d = Path(tempfile.mkdtemp())
    p = d / "manifest.json"
    ec = {}
    if sharpness:
        ec["event_boundary_sharpness"] = sharpness
    if finale:
        ec["is_volume_finale"] = True
    p.write_text(json.dumps({"event_cluster_context": ec}, ensure_ascii=False),
                 encoding="utf-8")
    return p


_NORMAL_PARA = "他静静地走在路上，望着前方未明的去路，思绪散落如雪，无声地沉到心底深处。\n\n"
_SHARP_TAIL = (
    "猛地！陡然！蓦地！霎时刹那！一瞬间突如其来！出乎意料！\n"
    "猛然！骤然！倏然！豁然！电光石火！措手不及！豁地！\n"
) * 5

_DULL_TAIL = "他继续走着。\n夜色温柔，月光下他想起了昨天的事情。\n他叹了口气。\n" * 30

_SHARP_TEXT = _NORMAL_PARA * 80 + _SHARP_TAIL
_DULL_TEXT = _NORMAL_PARA * 80 + _DULL_TAIL


def test_off_returns_skeleton():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_SHARP_TEXT), None, "sharp")
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DULL_TEXT), None, "sharp")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_sharp_with_sharp_tail_passes():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_SHARP_TEXT), None, "sharp")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "EVENT_BOUNDARY_TOO_DULL" not in codes
    finally:
        _set_mode(bak)


def test_active_sharp_with_dull_tail_fails():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DULL_TEXT), None, "sharp")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "EVENT_BOUNDARY_TOO_DULL" in codes
    finally:
        _set_mode(bak)


def test_active_dull_with_sharp_tail_fails():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_SHARP_TEXT), None, "dull")
        codes = {v["code"] for v in out.get("violations", [])}
        if out["signal"]["score"] > mod.DULL_THRESHOLD:
            assert "EVENT_BOUNDARY_TOO_SHARP" in codes
    finally:
        _set_mode(bak)


def test_volume_finale_forces_sharp():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        # manifest 写 dull 但 is_volume_finale=True 应被强制 sharp
        mf = _mk_manifest(sharpness="dull", finale=True)
        out = mod.scan(_write(_DULL_TEXT), str(mf), None)
        assert out["effective_sharpness"] == "sharp"
    finally:
        _set_mode(bak)


def test_active_missing_sharpness_info():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        # 不传 manifest，不传 sharpness
        out = mod.scan(_write(_DULL_TEXT), None, None)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "EVENT_BOUNDARY_DEFAULT" in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), None, "sharp")
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("EVENT_BOUNDARY_LC_MODE")
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
    for c in ("EVENT_BOUNDARY_TOO_DULL", "EVENT_BOUNDARY_TOO_SHARP",
              "EVENT_BOUNDARY_DEFAULT"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("event_boundary_lc_signal")
    assert s is not None
    assert s.get("_new") is True


def test_pe_lex_disjoint_from_belief_alignment():
    """与 belief_update_alignment 词典正交（防双计共谋）"""
    import belief_update_alignment as bua
    intersect = set(mod._PE_LEX) & set(bua._SURPRISE_LEX)
    assert not intersect, f"词典共谋: {intersect}"
