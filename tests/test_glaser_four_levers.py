# -*- coding: utf-8 -*-
"""glaser_four_levers R24 W12 Batch-JJ · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import glaser_four_levers as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("GLASER_LEVERS_MODE", None)
    else:
        os.environ["GLASER_LEVERS_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# info-dump 段（无任何杠杆·全是抽象概念·确保无 action/sensory/concrete 词）
_DUMP_RAW = (
    "规则制度的层级结构由原理形成，体系建构在协议公约之上，"
    "范畴属于条款定律范畴，机制由理论支撑，概念清晰，"
    "系统层级分明，原理一致，结构稳定。"
) * 3 + "\n\n"

# info-dump 段配上具体物件（fictionalize）
_DUMP_FICTIONALIZED = (
    "规则制度的层级结构由原理推导而来，桌上摆着旧书，灯光昏黄，"
    "墙上挂着木牌，铁钉嵌入石壁。条款公约体系由这些器物承载。"
) * 3 + "\n\n"

_NORMAL_TEXT = "他静静地走在路上，望着前方的灯火。\n\n" * 60


def test_off_returns_skeleton():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DUMP_RAW * 3 + _NORMAL_TEXT))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DUMP_RAW * 3 + _NORMAL_TEXT))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_dump_missing_lever():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DUMP_RAW * 3 + _NORMAL_TEXT))
        # 至少 1 个 dump 段
        assert out["dump_count"] >= 1
        codes = {v["code"] for v in out.get("violations", [])}
        assert "GLASER_LEVER_MISSING" in codes
    finally:
        _set_mode(bak)


def test_active_dump_fictionalized_ok():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DUMP_FICTIONALIZED * 3 + _NORMAL_TEXT))
        # dump_count >=1·但每个 dump 至少 fictionalize+concrete 双杠杆命中
        if out["dump_count"] >= 1:
            assert out["lever_coverage"] > 0.5
    finally:
        _set_mode(bak)


def test_active_no_dump_segments():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NORMAL_TEXT * 2))
        # 无 info-dump
        assert out.get("dump_count", 0) == 0 or "无 info-dump" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_is_info_dump_long_abstract():
    """长段+抽象名词高密度 → info dump"""
    p = "规则制度原理系统机制条款层级机制范畴体系建构。" * 7
    assert mod._is_info_dump(p)


def test_is_info_dump_skips_dialogue():
    """含对话符号 → 非 info dump"""
    p = "「我们的规则制度原理系统机制条款层级机制范畴体系建构。」" * 5
    assert not mod._is_info_dump(p)


def test_is_info_dump_skips_short():
    p = "规则制度。"
    assert not mod._is_info_dump(p)


def test_cheapest_missing_lever_priority():
    scored = {"dramatize": 0, "emotionalize": 0,
              "personalize": 0, "fictionalize": 0}
    # 优先 fictionalize
    assert mod._cheapest_missing_lever(scored) == "fictionalize"
    scored["fictionalize"] = 1
    assert mod._cheapest_missing_lever(scored) == "dramatize"


def test_hit_lever():
    assert mod._hit_lever("他走在路上", ["走", "跑"]) == 1
    assert mod._hit_lever("他思考着", ["走", "跑"]) == 0


def test_short_draft_skipped():
    bak = os.environ.get("GLASER_LEVERS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("GLASER_LEVERS_MODE")
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
    for c in ("GLASER_LEVER_MISSING", "GLASER_LEVER_THIN", "GLASER_LEVER_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("glaser_four_levers")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_lexicon():
    assert mod._LEXICONS.get("_placeholder") is True


def test_build_manifest_injects_glaser_directive():
    """build_manifest 在 GLASER_LEVERS_MODE=active 时注入指令"""
    src = (_SCRIPTS / "build_manifest.py").read_text(encoding="utf-8")
    assert "glaser_four_levers_directive" in src
    assert "GLASER_LEVERS_MODE" in src
