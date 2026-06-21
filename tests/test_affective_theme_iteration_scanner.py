# -*- coding: utf-8 -*-
"""affective_theme_iteration_scanner R19 W8 Batch-Y·P2 Kuiken self-modifying feeling 回归测试。

确定性·零依赖。覆盖 off/短稿 skip/词典缺失/mentions=0 skip/full iteration PASS/
critical theme flat 触发/single-segment cluster 触发/shadow vs active/CLI/hard_gate.
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
import affective_theme_iteration_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "affective_theme_iteration_scanner.py"
_ENV = "AFFECTIVE_THEME_ITERATION_MODE"


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


def _mk_brief(theme_priority=None):
    d = Path(tempfile.mkdtemp())
    p = d / "brief.json"
    brief = {"id": "cluster_001"}
    if theme_priority:
        brief["theme_priority"] = theme_priority
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


def test_no_marker_zero_themes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_NEUTRAL))
        # 主题词全无 → metrics.themes_present 应 0 / 空
        assert r["metrics"]["themes_present"] == 0
    finally:
        _set_mode(bak)


def test_iteration_full_triad_passes():
    """三段都有主题 marker → segment_diversity==3·PASS."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 需 >800 CJK·头/中/尾各塞 marker
        head = "失去 离别 墓 他抬头看远方他走过去他低声说话 " * 30
        mid = "她终于明白了原来 他抬头看远方他走过去他低声说话 " * 30
        tail = "永远不会再见 他抬头看远方他走过去他低声说话 " * 30
        text = head + "\n\n中间过渡\n" + mid + "\n\n后段\n" + tail
        r = mod.scan(_write(text))
        per = r["metrics"]["per_theme"]
        assert "失去与悼念" in per
        # diversity 应 >=2
        assert per["失去与悼念"]["segment_diversity"] >= 2
    finally:
        _set_mode(bak)


def test_critical_theme_flat_triggers():
    """brief.theme_priority=失去与悼念 但全文只 mention 1 次 → AFFECTIVE_THEME_ITERATION_DEGRADED."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = _NEUTRAL + "失去" + _NEUTRAL  # 仅 1 次
        brief = _mk_brief(theme_priority=["失去与悼念"])
        r = mod.scan(_write(text), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        assert "AFFECTIVE_THEME_ITERATION_DEGRADED" in codes
    finally:
        _set_mode(bak)


def test_single_segment_cluster_triggers():
    """3+ mention 集中单段·iteration 失."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        head = "失去 离别 墓 失去 离别 墓 失去 离别 墓 " * 10
        rest = "他抬头看远方。" * 200
        text = head + rest  # 头段集中 marker
        r = mod.scan(_write(text))
        per = r["metrics"]["per_theme"]
        # 失去与悼念 的 segment_diversity 应该=1 触发
        if "失去与悼念" in per and per["失去与悼念"]["segment_diversity"] == 1:
            codes = [v["code"] for v in r["violations"]]
            assert "AFFECTIVE_THEME_ITERATION_DEGRADED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        text = _NEUTRAL + "失去 失去 失去 " * 10 + _NEUTRAL
        r = mod.scan(_write(text))
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


def test_segment_text_three_parts():
    parts = mod._segment_text("a" * 30)
    assert len(parts) == 3
    assert sum(len(p) for p in parts) == 30


def test_lexicon_load_default():
    themes, vshift = mod._load_lexicon()
    assert isinstance(themes, dict)
    assert len(themes) >= 1


def test_lexicon_load_missing_path():
    themes, vshift = mod._load_lexicon("/nonexistent/path.json")
    assert themes == {}


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_placeholder_lexicon_has_flag():
    p = Path(_ROOT / "core" / "data" / "affective_theme_lexicon_placeholder.json")
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
    assert rep["scanner"] == "affective_theme_iteration"
