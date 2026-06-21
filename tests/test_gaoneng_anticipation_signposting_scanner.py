# -*- coding: utf-8 -*-
"""gaoneng_anticipation_signposting_scanner R24 W12 Batch-JJ · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import gaoneng_anticipation_signposting_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("SIGNPOST_MODE", None)
    else:
        os.environ["SIGNPOST_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_baseline(mean, std):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {
        "signpost_density_baseline": {"mean": mean, "std": std}
    }
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False),
                                      encoding="utf-8")
    return proj


_FLAT_PARA = "他静静地走在路上，望着前方未明的去路，思绪散落如雪。\n\n"

# lead 段满 signpost·peak 段强标点
_LEAD_WITH_SIGNPOSTS = (
    "万籁俱寂，鸦雀无声。\n"
    "他汗如雨下，心跳如鼓，毛骨悚然。\n"
    "那把刀的特写如同被聚焦的镜头。\n"
    "时间凝固了一瞬间，仿佛过了一个世纪。\n"
    "耳畔传来一阵微弱声响。\n\n"
)
# peak 段（高标点+短独行 + ≥12 CJK）
_PEAK = ("惊！\n"
         "怒！\n"
         "落！\n"
         "一切都！结束了！！！？？？……\n"
         "他猛地跌坐在地，眼前发黑！\n"
         "一切结束！！！\n\n")

_DRAFT_WITH_SIGNPOSTS = _FLAT_PARA * 50 + _LEAD_WITH_SIGNPOSTS + _PEAK + _FLAT_PARA * 50
_DRAFT_NO_SIGNPOSTS = _FLAT_PARA * 50 + (_FLAT_PARA * 3) + _PEAK + _FLAT_PARA * 50
_DRAFT_NO_PEAK = _FLAT_PARA * 150


def test_off_returns_skeleton():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_WITH_SIGNPOSTS))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_NO_SIGNPOSTS))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_finds_peaks():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_WITH_SIGNPOSTS))
        # 至少找到 1 个 peak
        assert len(out.get("peaks", [])) >= 1
    finally:
        _set_mode(bak)


def test_active_low_signpost_density_detected():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("active")
        # 用很高 baseline·让 zero signposts 命中 LOW
        proj = _mk_project_with_baseline(mean=10.0, std=1.0)
        out = mod.scan(_write(_DRAFT_NO_SIGNPOSTS), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_LEAD_SIGNPOST_LOW" in codes
    finally:
        _set_mode(bak)


def test_active_signposts_within_band():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("active")
        # 宽 baseline·signposts 在带内
        proj = _mk_project_with_baseline(mean=5.0, std=10.0)
        out = mod.scan(_write(_DRAFT_WITH_SIGNPOSTS), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BURST_LEAD_SIGNPOST_LOW" not in codes
    finally:
        _set_mode(bak)


def test_active_no_peak_found():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_NO_PEAK))
        codes = {v["code"] for v in out.get("violations", [])}
        # 平淡文本可能没有 peak
        assert ("BURST_LEAD_NO_PEAK_FOUND" in codes
                or len(out.get("peaks", [])) == 0)
    finally:
        _set_mode(bak)


def test_load_baseline_from_project():
    proj = _mk_project_with_baseline(mean=2.5, std=0.8)
    mean, std = mod._load_baseline(proj)
    assert mean == 2.5 and std == 0.8


def test_load_baseline_default_when_missing():
    mean, std = mod._load_baseline(None)
    assert mean == mod.DEFAULT_BASELINE_DENSITY
    assert std == mod.DEFAULT_BASELINE_STD


def test_count_signposts_nonzero_for_keywords():
    txt = "万籁俱寂，他汗如雨下，那把刀的特写，瞬间凝固。"
    counts = mod._count_signposts(txt)
    assert counts["_total"] > 0


def test_build_lead_window_indices_aggregated():
    paragraphs = ["段一" * 100, "段二" * 100, "段三" * 100, "peak！"]
    lead = mod._build_lead_window(paragraphs, peak_idx=3)
    assert mod._cjk_count(lead) >= mod.LEAD_WINDOW_CJK_MIN or len(lead) > 0


def test_short_draft_skipped():
    bak = os.environ.get("SIGNPOST_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("SIGNPOST_MODE")
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
    for c in ("BURST_LEAD_SIGNPOST_LOW", "BURST_LEAD_SIGNPOST_HIGH",
              "BURST_LEAD_NO_PEAK_FOUND"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("gaoneng_anticipation_signposting_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_lexicon():
    assert mod._SIGNPOST_LEXICONS.get("_placeholder") is True
