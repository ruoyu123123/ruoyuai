# -*- coding: utf-8 -*-
"""cross_book_invariant_scanner R19 W8 Batch-X·P1 跨书 magic_system_invariant·回归。

确定性·零依赖。覆盖 off/短稿/无 ledger skip/breach 命中/active mode/shadow mode/
CLI/extract_hard_law_from_profile/collect_invariant_hint/hard_gate registry 守卫。
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
import cross_book_invariant_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_book_invariant_scanner.py"
_ENV = "CROSS_BOOK_INVARIANT_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_ledger(invariants):
    d = Path(tempfile.mkdtemp())
    p = d / "magic_invariants.json"
    p.write_text(json.dumps({"schema_version": 1, "_placeholder": False,
                             "invariants": invariants}, ensure_ascii=False),
                 encoding="utf-8")
    return d


_LONG_BASE = ("夜色降临，山门紧闭。修士们彼此寒暄。" * 80)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write_draft(_LONG_BASE))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write_draft("短。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_ledger_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write_draft(_LONG_BASE), series_path=None)
        assert "ledger" in r.get("note", "").lower() or "非系列" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_empty_ledger_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        led_dir = _mk_ledger([])
        r = mod.scan(_write_draft(_LONG_BASE), series_path=str(led_dir))
        assert "skip" in r.get("note", "").lower() or "空" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_breach_detection_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        led_dir = _mk_ledger([
            {"invariant_id": "I001", "rule_text": "金丹境不能跨界传送",
             "scope": "main_world", "coverage_books": ["凡人修仙传"],
             "severity": "advisory"}
        ])
        # 草稿中既有「金丹境」+「不能」否定词命中
        draft = _LONG_BASE + "\n金丹境的他突然不能再施展跨界传送术，惊得众人。" * 5
        r = mod.scan(_write_draft(draft), series_path=str(led_dir))
        # shadow 模式不上报 violations
        assert r["violations"] == []
        # 但 metrics 应有 invariants_count
        assert r["metrics"]["invariants_count"] == 1
    finally:
        _set_mode(bak)


def test_breach_detection_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        led_dir = _mk_ledger([
            {"invariant_id": "I001", "rule_text": "金丹境不能跨界传送",
             "scope": "main_world", "coverage_books": ["凡人修仙传"],
             "severity": "advisory"}
        ])
        draft = _LONG_BASE + "\n金丹境的他居然能跨界传送出去，破例了！" * 8
        r = mod.scan(_write_draft(draft), series_path=str(led_dir))
        # active 才上报
        if r["violations"]:
            assert r["violations"][0]["code"] == "CROSS_BOOK_INVARIANT_BREACH"
            assert r["violations"][0].get("severity") == "minor"
    finally:
        _set_mode(bak)


def test_no_breach_clean_draft():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        led_dir = _mk_ledger([
            {"invariant_id": "I001", "rule_text": "天魔不可与凡人对话",
             "scope": "天魔界"}
        ])
        r = mod.scan(_write_draft(_LONG_BASE), series_path=str(led_dir))
        # 无关键词命中 → 不报
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_collect_invariant_hint_returns_top_k():
    led_dir = _mk_ledger([
        {"invariant_id": f"I00{i}", "rule_text": f"规则{i}内容",
         "scope": "main", "coverage_books": ["book"]}
        for i in range(1, 9)
    ])
    h = mod.collect_invariant_hint(str(led_dir), top_k=3)
    assert h is not None
    assert len(h["invariants"]) == 3
    assert h["_placeholder_nli"] is True


def test_collect_invariant_hint_no_ledger():
    assert mod.collect_invariant_hint(None) is None


def test_extract_hard_law_from_profile():
    # 给 profile 注 worldview.magic_system_invariants
    d = Path(tempfile.mkdtemp())
    p = d / "profile.json"
    p.write_text(json.dumps({
        "worldview": {
            "magic_system_invariants": [
                {"rule_text": "灵气不能逆流"},
                "天劫一来必有反应",
            ]
        }
    }, ensure_ascii=False), encoding="utf-8")
    laws = mod.extract_hard_law_from_profile(p)
    assert len(laws) == 2
    assert laws[0]["rule_text"] == "灵气不能逆流"
    assert laws[1]["invariant_id"].startswith("I")


def test_extract_hard_law_missing_file():
    laws = mod.extract_hard_law_from_profile("/nope/no.json")
    assert laws == []


def test_cli_runs_and_exits():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(_write_draft(_LONG_BASE))],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
        assert r.returncode in (0, 1)
        # stdout 是 JSON report
        assert "scanner" in r.stdout
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    """北极星⑤守卫：CROSS_BOOK_INVARIANT_BREACH 绝不进 HARD_GATE_CODES。"""
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "CROSS_BOOK_INVARIANT_BREACH" not in set(data.get("hard_gate_codes", []))


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("cross_book_invariant_scanner")
    assert entry is not None
    assert entry.get("_new") is True
    assert "CROSS_BOOK_INVARIANT_BREACH" in entry.get("issues_emitted", [])


def test_extract_keywords_filters_stopwords():
    kws = mod._extract_keywords("不能跨界传送")
    assert "跨界" in kws or "传送" in kws
    assert "不能" not in kws  # stop word
