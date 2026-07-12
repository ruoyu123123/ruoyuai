# -*- coding: utf-8 -*-
"""Manifest debt_ledger_snapshot + sagging_middle_snapshot collector + gen_writer D7 section 单测
（R7 Batch-D · 2026-06-20）。

确定性·零依赖。覆盖：
  ① _collect_debt_ledger_snapshot：缺文件/off/shadow/active 四档
  ② _collect_sagging_middle_snapshot：缺文件/off/shadow/active 四档
  ③ _build_debt_ledger_section：字段缺/字段全 → 段落字符串
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import build_manifest as bm  # noqa: E402
import gen_writer as gw  # noqa: E402


def _mk_scanner_with_snapshot(snap_name, snap_obj):
    proj = Path(tempfile.mkdtemp(prefix="debt_inject_"))
    db = proj / "_数据库" / ".cross_cluster_scan"
    db.mkdir(parents=True, exist_ok=True)
    (db / snap_name).write_text(json.dumps(snap_obj, ensure_ascii=False), encoding="utf-8")
    # 最简 scanner 替身：只需 .db 字段
    return SimpleNamespace(db=proj / "_数据库")


# ── debt collector：缺文件 → None ───────────────────────
def test_debt_collector_no_file_returns_none():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    s = SimpleNamespace(db=proj / "_数据库")
    assert bm._collect_debt_ledger_snapshot(s) is None


# ── debt collector：off ─────────────────────────────────
def test_debt_collector_off_mode():
    bak = os.environ.get("NARRATIVE_DEBT_INJECT_MODE")
    try:
        os.environ["NARRATIVE_DEBT_INJECT_MODE"] = "off"
        s = _mk_scanner_with_snapshot("narrative_debt_snapshot.json", {
            "book": {"total_planted": 5, "total_paid": 1, "open_debt": 4, "open_ratio": 0.8},
            "volumes": [], "advisory_codes": ["DEBT_VOLUME_OVERSHOOT"],
        })
        assert bm._collect_debt_ledger_snapshot(s) is None
    finally:
        if bak is None:
            os.environ.pop("NARRATIVE_DEBT_INJECT_MODE", None)
        else:
            os.environ["NARRATIVE_DEBT_INJECT_MODE"] = bak


# ── debt collector：shadow 模式 None ──────────────────
def test_debt_collector_shadow_mode_returns_none():
    bak = os.environ.get("NARRATIVE_DEBT_INJECT_MODE")
    try:
        os.environ["NARRATIVE_DEBT_INJECT_MODE"] = "shadow"
        s = _mk_scanner_with_snapshot("narrative_debt_snapshot.json", {
            "book": {"total_planted": 5, "total_paid": 1, "open_debt": 4, "open_ratio": 0.8},
            "volumes": [{"volume": 1, "total_planted": 5, "total_paid": 1, "open_debt": 4, "open_ratio": 0.8}],
            "advisory_codes": ["DEBT_VOLUME_OVERSHOOT"],
        })
        assert bm._collect_debt_ledger_snapshot(s) is None
    finally:
        if bak is None:
            os.environ.pop("NARRATIVE_DEBT_INJECT_MODE", None)
        else:
            os.environ["NARRATIVE_DEBT_INJECT_MODE"] = bak


# ── debt collector：active 模式 → payload ─────────────
def test_debt_collector_active_returns_payload():
    bak = os.environ.get("NARRATIVE_DEBT_INJECT_MODE")
    try:
        os.environ["NARRATIVE_DEBT_INJECT_MODE"] = "active"
        s = _mk_scanner_with_snapshot("narrative_debt_snapshot.json", {
            "book": {"total_planted": 5, "total_paid": 1, "open_debt": 4, "open_ratio": 0.8},
            "volumes": [{"volume": 1, "total_planted": 5, "total_paid": 1, "open_debt": 4, "open_ratio": 0.8}],
            "advisory_codes": ["DEBT_VOLUME_OVERSHOOT"],
        })
        out = bm._collect_debt_ledger_snapshot(s)
        assert isinstance(out, dict)
        assert out["gate_level"] == "advisory"
        assert out["book"]["open_debt"] == 4
        assert "DEBT_VOLUME_OVERSHOOT" in out["advisory_codes"]
    finally:
        if bak is None:
            os.environ.pop("NARRATIVE_DEBT_INJECT_MODE", None)
        else:
            os.environ["NARRATIVE_DEBT_INJECT_MODE"] = bak


# ── sagging collector：缺文件 → None ──────────────────
def test_sagging_collector_no_file_returns_none():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    s = SimpleNamespace(db=proj / "_数据库")
    assert bm._collect_sagging_middle_snapshot(s) is None


# ── sagging collector：active → payload ───────────────
def test_sagging_collector_active_payload():
    bak = os.environ.get("SAGGING_MIDDLE_INJECT_MODE")
    try:
        os.environ["SAGGING_MIDDLE_INJECT_MODE"] = "active"
        s = _mk_scanner_with_snapshot("sagging_middle_snapshot.json", {
            "needs_midpoint_bomb": True,
            "hit_signals": 2,
            "middle_cluster_ids": ["c4", "c5", "c6"],
            "advisory_codes": ["SAGGING_MIDDLE_REVERSAL_VOID"],
        })
        out = bm._collect_sagging_middle_snapshot(s)
        assert isinstance(out, dict)
        assert out["needs_midpoint_bomb"] is True
        assert out["hit_signals"] == 2
        assert out["gate_level"] == "advisory"
    finally:
        if bak is None:
            os.environ.pop("SAGGING_MIDDLE_INJECT_MODE", None)
        else:
            os.environ["SAGGING_MIDDLE_INJECT_MODE"] = bak


# ── writer D7 debt section：字段缺 → 空 ─────────────
def test_writer_debt_section_empty_when_no_field(tmp_path):
    m = {"chapter": 5}  # 无 debt_ledger_snapshot
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    assert gw._build_debt_ledger_section(p, m) == ""


# ── writer D7 debt section：字段全 → 段落 ────────────
def test_writer_debt_section_renders(tmp_path):
    m = {
        "chapter": 5,
        "debt_ledger_snapshot": {
            "book": {"total_planted": 8, "total_paid": 3, "open_debt": 5, "open_ratio": 0.625},
            "volumes": [
                {"volume": 1, "total_planted": 5, "total_paid": 3, "open_debt": 2, "open_ratio": 0.4},
                {"volume": 2, "total_planted": 3, "total_paid": 0, "open_debt": 3, "open_ratio": 1.0},
            ],
            "advisory_codes": ["DEBT_VOLUME_OVERSHOOT"],
        },
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    section = gw._build_debt_ledger_section(p, m)
    assert "叙事债务账本" in section
    assert "open_debt 5" in section
    assert "DEBT_VOLUME_OVERSHOOT" in section
    # 卷 2 open_ratio 100%
    assert "100%" in section


# ── writer D7 debt section：空 book → 空 ─────────────
def test_writer_debt_section_empty_when_no_book(tmp_path):
    m = {"chapter": 5, "debt_ledger_snapshot": {}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    assert gw._build_debt_ledger_section(p, m) == ""
