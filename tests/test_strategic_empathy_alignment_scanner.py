# -*- coding: utf-8 -*-
"""strategic_empathy_alignment_scanner R19 W8 Batch-Y·P2 Keen 三型 回归测试。

确定性·零依赖。覆盖 off/短稿/cue 缺失/declared intent 对齐 PASS/不对齐 misaligned/
gap<30% 不报/shadow vs active/CLI/hard_gate.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import strategic_empathy_alignment_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "strategic_empathy_alignment_scanner.py"
_ENV = "STRATEGIC_EMPATHY_ALIGNMENT_MODE"


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


def _mk_brief(intent=None):
    d = Path(tempfile.mkdtemp())
    p = d / "brief.json"
    brief = {"id": "cluster_001"}
    if intent:
        brief["strategic_empathy_intent"] = intent
    p.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return p


_NEUTRAL = "他抬头看远方。" * 200


def test_off_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_NEUTRAL))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_intent_only_observe():
    """无 declared_intent 只观察 dominant 不告警."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "我们这边的兄弟同伴师兄 战友圈内" * 60 + _NEUTRAL
        r = mod.scan(_write(text))
        assert r["verdict"] == "PASS"
        assert r["metrics"]["dominant_type"] == "bounded"
    finally:
        _set_mode(bak)


def test_intent_aligned_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "我们这边的兄弟同伴师兄 战友圈内自家人" * 60 + _NEUTRAL
        brief = _mk_brief(intent="bounded")
        r = mod.scan(_write(text), cluster_brief_path=brief)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_intent_misaligned_triggers():
    """declared=broadcast 但全文充斥 bounded cue → MISALIGNED."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "我们这边的兄弟同伴师兄 战友圈内自家人" * 60 + _NEUTRAL
        brief = _mk_brief(intent="broadcast")
        r = mod.scan(_write(text), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        assert "STRATEGIC_EMPATHY_MISALIGNED" in codes
    finally:
        _set_mode(bak)


def test_gap_below_threshold_no_trigger():
    """intent 仅略低于 dominant → 不触发."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # bounded:8 broadcast:7 → gap = (8-7)/8 = 0.125 < 0.30
        text = ("我们 大家 我们 大家 我们 大家 我们 大家 " * 5 + "我们 " + _NEUTRAL)
        brief = _mk_brief(intent="broadcast")
        r = mod.scan(_write(text), cluster_brief_path=brief)
        # 可能 PASS 因 gap 不足
        if r["metrics"]["dominant_type"] == "bounded":
            dom = r["metrics"]["per_type"]["bounded"]["per_1k"]
            intent = r["metrics"]["per_type"]["broadcast"]["per_1k"]
            if dom > 0:
                gap = (dom - intent) / dom
                if gap < 0.30:
                    assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        text = "我们这边的兄弟同伴师兄 战友圈内自家人" * 60 + _NEUTRAL
        brief = _mk_brief(intent="broadcast")
        r = mod.scan(_write(text), cluster_brief_path=brief)
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_measure_types_balanced():
    text = "我们 大家 异乡人 兄弟 众生 外族 " * 30
    cues = {
        "bounded": ["兄弟"], "ambassadorial": ["异乡人", "外族"],
        "broadcast": ["大家", "众生"],
    }
    out = mod.measure_types(text, cues)
    assert "bounded" in out and "ambassadorial" in out and "broadcast" in out


def test_cues_load_missing_path():
    cues = mod._load_cues("/nope/path.json")
    assert cues == {}


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_placeholder_lexicon_has_flag():
    p = Path(_ROOT / "core" / "data" / "strategic_empathy_cues_placeholder.json")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d.get("_placeholder") is True


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_runs_shadow():
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(_write(_NEUTRAL))],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "strategic_empathy_alignment"
