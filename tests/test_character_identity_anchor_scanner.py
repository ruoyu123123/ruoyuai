#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_identity_anchor_scanner tests."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import character_identity_anchor_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "character_identity_anchor_scanner.py"


def _set_mode(mode):
    if mode is None:
        os.environ.pop("CHARACTER_IDENTITY_ANCHOR_MODE", None)
    else:
        os.environ["CHARACTER_IDENTITY_ANCHOR_MODE"] = mode


def _write_draft(text: str) -> Path:
    root = Path(tempfile.mkdtemp())
    path = root / "draft.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _mk_project(characters) -> Path:
    root = Path(tempfile.mkdtemp())
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_active_detects_hair_color_drift_from_identity_anchors():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟推开门，雨水顺着金发往下淌。他没有回头。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "CHARACTER_IDENTITY_ANCHOR_DRIFT"
        assert out["violations"][0]["character"] == "池迟"
        assert out["violations"][0]["anchor_type"] == "hair_color"
        assert out["violations"][0]["expected"] == "黑发"
        assert out["violations"][0]["observed"] == "金发"
    finally:
        _set_mode(bak)


def test_shadow_records_samples_but_does_not_fail():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("shadow")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"eye_color": "黑眼"}},
        ])
        draft = _write_draft("池迟抬起蓝眼，看向被雨冲开的巷口。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "PASS"
        assert out["violations"] == []
        assert out["drift_count"] == 1
        assert out["drift_samples"][0]["observed"] == "蓝眼"
    finally:
        _set_mode(bak)


def test_explicit_forbidden_terms_detect_mark_drift():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {
                "name": "老钟",
                "identity_anchors": [
                    {"type": "mark", "expected": "左脸刀疤", "forbidden": ["左脸黑痣"]},
                ],
            }
        ])
        draft = _write_draft("老钟摸了摸左脸黑痣，像是在确认旧伤还在。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["anchor_type"] == "mark"
        assert out["violations"][0]["observed"] == "左脸黑痣"
    finally:
        _set_mode(bak)


def test_appearance_and_locked_facts_can_seed_anchors():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {
                "name": "林砚",
                "appearance": "黑发，灰眼，左眉有疤",
                "locked_facts": ["林砚一直是黑发"],
            }
        ])
        draft = _write_draft("林砚把斗篷拉低，那双蓝眼在阴影里一闪。")

        out = mod.scan(draft, project)

        assert out["anchor_count"] >= 2
        assert out["verdict"] == "FAIL_MINOR"
        assert any(v["anchor_type"] == "eye_color" and v["observed"] == "蓝眼"
                   for v in out["violations"])
    finally:
        _set_mode(bak)


def test_no_anchors_default_safe_skip():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([{"name": "池迟"}])
        out = mod.scan(_write_draft("池迟走进雨里。"), project)
        assert out["verdict"] == "PASS"
        assert out["anchor_count"] == 0
        assert "no identity anchors" in out["note"]
    finally:
        _set_mode(bak)


def test_negated_conflict_does_not_report():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟不是金发，雨水贴着黑发落进衣领。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["drift_count"] == 0
    finally:
        _set_mode(bak)


def test_audit_hub_integrates_scanner():
    """[2026-07-05 孤儿接线回归锁] audit_hub cluster-mode tasks 真调本 scanner
    （参考 test_agenda_drift_scanner 同款源码断言模式）。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "character_identity_anchor_scanner" in src
    assert "CHARACTER_IDENTITY_ANCHOR_DRIFT" in src


def test_code_never_in_hard_gate_codes():
    """北极星⑤：身份锚点漂移是 advisory·绝不进 HARD_GATE_CODES。"""
    import audit_hub
    assert "CHARACTER_IDENTITY_ANCHOR_DRIFT" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("CHARACTER_IDENTITY_ANCHOR_DRIFT", "error") == "advisory"


def test_cli_exits_one_when_active_warning():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟甩开金发上的雨。")
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(draft), "--project", str(project)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)
