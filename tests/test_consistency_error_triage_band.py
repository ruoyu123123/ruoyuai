# -*- coding: utf-8 -*-
"""consistency_error_triage_band R11 W6 STRONG 回归(确定性·零依赖)"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import consistency_error_triage_band as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CONSISTENCY_ERROR_TRIAGE_MODE", None)
    else:
        os.environ["CONSISTENCY_ERROR_TRIAGE_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


_DRAFT = ("某夜春风落院深，老者抚琴声悠悠。月色入水鱼欲跃，云气藏星梦未醒。\n\n"
          "他独立桥头，望江北雁阵悄飞远天际。江南柳色已然斑驳，叶上微霜半凝。\n\n"
          "巷口一灯一影一茶香，几声犬吠传破夜色寂寥。") * 8


def test_off():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_failure_skip():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_no_audit_issues_passes():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT))
        assert out["verdict"] == "PASS"
        assert out["audit_issue_count"] == 0
        assert out["cooccurrence_hotspots"] == []
        assert "entropy_top" in out
        assert "act2_band" in out
    finally:
        _set_mode(bak)


def test_active_with_cooccurrence_hotspot():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        issues = [
            {"code": "LOCKED_FACT_CONFLICT", "char_start": 100},
            {"code": "POV_VIOLATION", "char_start": 200},
            {"code": "STYLE_单段超长", "char_start": 250},
        ]
        ip = proj / "issues.json"
        ip.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
        out = mod.scan(_write(_DRAFT), proj, ip)
        assert out["audit_issue_count"] == 3
        assert len(out["cooccurrence_hotspots"]) >= 1
        assert out["violations"]
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["kind"] == "consistency_triage"
    finally:
        _set_mode(bak)


def test_shadow_with_hotspots_no_violation():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("shadow")
        proj = Path(tempfile.mkdtemp())
        issues = [{"code": "A", "char_start": 100}, {"code": "B", "char_start": 200}]
        ip = proj / "issues.json"
        ip.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
        out = mod.scan(_write(_DRAFT), proj, ip)
        assert out["mode"] == "shadow"
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_load_audit_issues_dict_form():
    proj = Path(tempfile.mkdtemp())
    p = proj / "issues.json"
    p.write_text(json.dumps({"issues": [{"code": "X", "char_start": 10}]},
                            ensure_ascii=False), encoding="utf-8")
    issues = mod._load_audit_issues(p)
    assert len(issues) == 1


def test_load_audit_issues_corrupted():
    p = Path(tempfile.mkdtemp()) / "issues.json"
    p.write_text("{ bad json", encoding="utf-8")
    assert mod._load_audit_issues(p) == []


def test_load_audit_issues_none():
    assert mod._load_audit_issues(None) == []


def test_entropy_proxy_short_returns_0():
    assert mod._entropy_proxy("短文。") == 0.0


def test_act2_band():
    band = mod._act2_band("x" * 400)
    assert band["start"] == 100 and band["end"] == 300


def test_mode_invalid_falls_shadow():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_triage_report_written_to_project():
    bak = os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        out = mod.scan(_write(_DRAFT), proj)
        assert "triage_report_path" in out
        assert Path(out["triage_report_path"]).exists()
    finally:
        _set_mode(bak)
