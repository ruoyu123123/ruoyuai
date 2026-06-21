# -*- coding: utf-8 -*-
"""scene_gap_probe R20 W9 Batch-BB · P2 · McKee 期望-结果 GAP probe
确定性·零依赖。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import scene_gap_probe as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("SCENE_GAP_PROBE_MODE", None)
    else:
        os.environ["SCENE_GAP_PROBE_MODE"] = m


_FULL_SB = [
    {"scene_index": 0, "expectation": "他以为只是闲聊", "actual_outcome": "对方直接亮枪",
     "gap_type": "reversal"},
    {"scene_index": 1, "expectation": "她准备投降", "actual_outcome": "反咬一口逃脱",
     "gap_type": "revelation"},
    {"scene_index": 2, "expectation": "本以为安全屋稳", "actual_outcome": "刚到就被包围",
     "gap_type": "escalation"},
    {"scene_index": 3, "expectation": "他想救她", "actual_outcome": "她已经死了",
     "gap_type": "ironic"},
]

_EMPTY_SB = [
    {"scene_index": 0, "summary": "走进屋子"},
    {"scene_index": 1, "summary": "倒杯水"},
    {"scene_index": 2, "summary": "看了眼窗外"},
]

_MONOTONE_SB = [
    {"expectation": "A", "actual_outcome": "B", "gap_type": "reversal"},
    {"expectation": "C", "actual_outcome": "D", "gap_type": "reversal"},
    {"expectation": "E", "actual_outcome": "F", "gap_type": "reversal"},
    {"expectation": "G", "actual_outcome": "H", "gap_type": "reversal"},
    {"expectation": "I", "actual_outcome": "J", "gap_type": "revelation"},
]

_ALL_NO_GAP = [
    {"expectation": "A", "actual_outcome": "A", "gap_type": "no_gap"},
    {"expectation": "B", "actual_outcome": "B", "gap_type": "no_gap"},
    {"expectation": "C", "actual_outcome": "C", "gap_type": "no_gap"},
]


def test_off():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("off")
        out = mod.probe(_FULL_SB)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_empty_storyboard():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        out = mod.probe([])
        assert "scene_storyboard 为空" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_full_pass():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_FULL_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_GAP_ABSENT" not in codes
        assert "SCENE_GAP_MONOTONE" not in codes
        assert out["filled_share"] == 1.0
    finally:
        _set_mode(bak)


def test_absent_flagged():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_EMPTY_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_GAP_ABSENT" in codes
    finally:
        _set_mode(bak)


def test_all_no_gap_flagged_absent():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_ALL_NO_GAP)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_GAP_ABSENT" in codes
    finally:
        _set_mode(bak)


def test_monotone_flagged():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_MONOTONE_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_GAP_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("shadow")
        out = mod.probe(_EMPTY_SB)
        assert out["violations"] == []
        # flags 还是会有
        assert out["mode"] == "shadow"
    finally:
        _set_mode(bak)


def test_load_storyboard_from_brief(tmp_path):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "scene_storyboard": _FULL_SB}]},
        ensure_ascii=False), encoding="utf-8")
    sb = mod._load_storyboard(str(path))
    assert sb == _FULL_SB


def test_load_storyboard_direct_list(tmp_path):
    path = tmp_path / "sb.json"
    path.write_text(json.dumps(_FULL_SB, ensure_ascii=False), encoding="utf-8")
    assert mod._load_storyboard(str(path)) == _FULL_SB


def test_invalid_gap_type_counted():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        sb = [{"expectation": "x", "actual_outcome": "y", "gap_type": "bogus"}]
        out = mod.probe(sb)
        assert out["invalid_types"] == 1
    finally:
        _set_mode(bak)
