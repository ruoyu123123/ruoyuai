# -*- coding: utf-8 -*-
"""agenda_drift_scanner R24 W12 Batch-KK · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import agenda_drift_scanner as mod  # noqa: E402
import writer_intent_anchor as wia  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("AGENDA_DRIFT_MODE", None)
    else:
        os.environ["AGENDA_DRIFT_MODE"] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_anchor(want="救妹妹", antagonist="无脸者",
                            stake="灵魂被吞", tone_word="冷峻"):
    proj = Path(tempfile.mkdtemp())
    wia.cmd_create(SimpleNamespace(
        project=str(proj), cluster_key="001",
        want=want, antagonist=antagonist,
        stake=stake, tone_word=tone_word, force=False))
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write_draft("xxx"), None, "001")
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_anchor_emits_info_active():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        out = mod.scan(_write_draft("正文" * 100), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_NO_ANCHOR" in codes
    finally:
        _set_mode(bak)


def test_drift_emits_advisory():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_anchor(
            want="救妹妹", antagonist="无脸者",
            stake="灵魂被吞", tone_word="冷峻")
        # 草稿与 4 字段完全无关
        draft_text = "甜蜜蜜的婚礼在春天举行。" * 200
        out = mod.scan(_write_draft(draft_text), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_AGENDA_DRIFT" in codes
        assert len(out["drift_fields"]) > 0
    finally:
        _set_mode(bak)


def test_aligned_emits_ok():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_anchor(
            want="救妹妹", antagonist="无脸者",
            stake="灵魂被吞", tone_word="冷峻")
        # 草稿包含全部 4 字段字符
        draft_text = "救妹妹·无脸者·灵魂被吞·冷峻\n" * 200
        out = mod.scan(_write_draft(draft_text), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "WRITER_INTENT_OK" in codes
        assert "WRITER_INTENT_AGENDA_DRIFT" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_anchor()
        out = mod.scan(_write_draft("xxx" * 100), proj, "001")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_coverage_full_match():
    assert mod._coverage("ab", "..a..b..") == 1.0


def test_coverage_no_match():
    assert mod._coverage("救妹妹", "甜蜜婚礼") == 0.0


def test_coverage_partial():
    # 救妹妹 = {救,妹}（妹去重）·draft 中只有"妹" → 0.5
    score = mod._coverage("救妹妹", "妹的故事")
    assert 0.4 < score < 0.6


def test_coverage_empty_field():
    assert mod._coverage("", "anything") == 1.0


def test_codes_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("WRITER_INTENT_AGENDA_DRIFT", "WRITER_INTENT_NO_ANCHOR",
              "WRITER_INTENT_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("agenda_drift_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AGENDA_DRIFT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_drift_threshold_constant():
    assert mod.DRIFT_THRESHOLD == 0.62


def test_audit_hub_integrates_scanner():
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "agenda_drift_scanner" in src
    assert "WRITER_INTENT_AGENDA_DRIFT" in src
