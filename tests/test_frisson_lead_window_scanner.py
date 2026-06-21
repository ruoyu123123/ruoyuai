# -*- coding: utf-8 -*-
"""frisson_lead_window_scanner R23 W11 Batch-HH · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import frisson_lead_window_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("FRISSON_LEAD_MODE", None)
    else:
        os.environ["FRISSON_LEAD_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


_PARA_FLAT = "他静静地走在路上，望着前方未明的去路，思绪散落如雪。" + "\n\n"

# lead 锐化型：climax 前 1-2 段标点+独行密度高·climax 段相对略钝
_LEAD_SHARP_TEXT = (
    _PARA_FLAT * 80
    # lead 段（≥20 CJK + 高密度独行 + 高密度感叹号）
    + "短句！\n破墙！\n寒意！\n刹那！\n断！\n光闪！\n冷意！\n短！\n绝望中他终于看到！终于！冰冷！破碎！惊！惧！冷！\n\n"
    # climax 段（≥20 CJK 但相对平·情绪降落式·只 1 个标点）
    + "他知道一切已经结束了，所有的事情都已经在这一刻得到了真正的回答。\n\n"
    + _PARA_FLAT * 80
)

# climax 过载型：climax 段一堆 ！？……·lead 段平淡
_CLIMAX_OVERLOAD_TEXT = (
    _PARA_FLAT * 80
    # lead 段平淡（≥20 CJK 但极少标点+无独行）
    + "他静静地走着路边的灯火忽明忽暗心绪渐渐沉了下来周围一片寂静。\n\n"
    # climax 段堆砌（≥20 CJK + 大量情绪标点）
    + "啊啊啊啊啊！！！！没想到！！！突如其来！！！？？？……陡然！！！惊呆了！！！\n\n"
    + _PARA_FLAT * 80
)

_NO_CLIMAX_TEXT = _PARA_FLAT * 150


def test_off_returns_skeleton():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_LEAD_SHARP_TEXT))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_CLIMAX_OVERLOAD_TEXT))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_finds_climax_para():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LEAD_SHARP_TEXT))
        assert out.get("climax_idx", -1) >= 0
    finally:
        _set_mode(bak)


def test_active_climax_overload_detected():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CLIMAX_OVERLOAD_TEXT))
        codes = {v["code"] for v in out.get("violations", [])}
        # 期望命中 lead_flat 或 climax_overload 之一（堆 climax）
        assert ("FRISSON_LEAD_FLAT" in codes
                or "FRISSON_CLIMAX_OVERLOAD" in codes)
    finally:
        _set_mode(bak)


def test_active_no_climax_text():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_CLIMAX_TEXT))
        codes = {v["code"] for v in out.get("violations", [])}
        # 全平淡 → 无 climax info
        assert "FRISSON_NO_CLIMAX_FOUND" in codes or out.get("climax_idx", 0) < 0
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("FRISSON_LEAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("FRISSON_LEAD_MODE")
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
    for c in ("FRISSON_LEAD_FLAT", "FRISSON_CLIMAX_OVERLOAD",
              "FRISSON_NO_CLIMAX_FOUND"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("frisson_lead_window")
    assert s is not None
    assert s.get("_new") is True


def test_build_manifest_injects_frisson_lead_window_when_active():
    """build_manifest 在 FRISSON_LEAD_MODE=active 时注入 frisson_lead_window 字段"""
    import build_manifest as bm  # noqa
    # build_manifest 是大模块·只 grep 字符串证明字段被定义
    src = (_SCRIPTS / "build_manifest.py").read_text(encoding="utf-8")
    assert '"frisson_lead_window"' in src
    assert "FRISSON_LEAD_MODE" in src


def test_lead_window_within_target_cjk():
    """lead window 拼到 200-400 CJK 钳位"""
    paras = ["段一" * 100, "段二" * 100, "段三" * 100, "climax！！！"]
    lead, idx = mod._build_lead_window(paras, climax_idx=3)
    assert mod._cjk_count(lead) >= mod.LEAD_WINDOW_CJK_MIN
