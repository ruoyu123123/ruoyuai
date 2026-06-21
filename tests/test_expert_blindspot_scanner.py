# -*- coding: utf-8 -*-
"""expert_blindspot_scanner R24 W12 Batch-JJ · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import expert_blindspot_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("EXPERT_BLINDSPOT_MODE", None)
    else:
        os.environ["EXPERT_BLINDSPOT_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 引入一个 quoted 术语，然后大段无锚定文本（纯抽象概念词·零 sensory/concrete/action 词）
_FILLER = ("规则制度的体系由概念形成，理论从范畴而来，"
           "层级建构在条款公约之上，定律决定一切，机制依赖结构。" * 5
           + "\n\n") * 30  # 大段无具体锚定的抽象文字

_DRAFT_DRIFT = (
    "他第一次提及「夜行规则」是在那个夜晚，定律被概念解释了一遍。\n\n"
    + _FILLER  # 约 2250 CJK 抽象文字
    + "「夜行规则」再次浮现在脑海中。\n\n"
    + _FILLER
)

_DRAFT_ANCHORED = (
    "他第一次见到「夜行规则」是在小屋里，桌上点着灯。\n\n"
    "他伸手敲门，灯光照在墙上。他握住「夜行规则」的旧书。\n\n"
    "他听见门外的声音，看着窗外的影。他又一次想起了「夜行规则」。\n\n"
) * 30  # 每段都有 sensory/concrete


def test_off_returns_skeleton():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_DRIFT))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_DRIFT))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_drift_detected():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_DRIFT))
        # 命中 drift
        codes = {v["code"] for v in out.get("violations", [])}
        assert ("EXPERT_BLINDSPOT_DRIFT" in codes
                or "EXPERT_BLINDSPOT_DRIFT_COMPLEX" in codes)
    finally:
        _set_mode(bak)


def test_active_anchored_ok():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_ANCHORED))
        codes = {v["code"] for v in out.get("violations", [])}
        # 每次出现都有锚定·应无 drift
        assert "EXPERT_BLINDSPOT_OK" in codes or "EXPERT_BLINDSPOT_DRIFT" not in codes
    finally:
        _set_mode(bak)


def test_term_complex_detection():
    assert mod._term_is_complex("夜行三条规则")
    assert mod._term_is_complex("第七规则")
    assert not mod._term_is_complex("普通词汇")


def test_extract_quoted_terms():
    text = "「夜行规则」是核心，他想起了「白事条款」。"
    cands = mod._extract_term_candidates(text)
    assert "夜行规则" in cands
    assert "白事条款" in cands


def test_find_term_positions():
    text = "他「夜行规则」开始，又一次「夜行规则」结束。"
    pos = mod._find_term_positions(text, "夜行规则")
    assert len(pos) == 2
    assert pos[0] < pos[1]


def test_pov_window_skipped():
    text = "他想：「夜行规则」到底是什么？"
    # 位置 4 附近含 POV marker
    assert mod._is_pov_window(text, 4)


def test_has_anchor_in_window():
    # 包含 sensory/concrete/action 词
    assert mod._has_anchor_in_window("他伸手抚过桌上的灯", 0, 100)
    assert not mod._has_anchor_in_window("规则制度系统层级机制条款", 0, 100)


def test_cjk_gap():
    text = "一二三abc四五六"
    # text[0:9] = "一二三abc四五六" → 6 CJK
    assert mod._cjk_gap(text, 0, 9) == 6


def test_short_draft_skipped():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("EXPERT_BLINDSPOT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("EXPERT_BLINDSPOT_DRIFT", "EXPERT_BLINDSPOT_DRIFT_COMPLEX",
              "EXPERT_BLINDSPOT_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("expert_blindspot_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_lexicon():
    assert mod._ANCHOR_WORDS.get("_placeholder") is True


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")
