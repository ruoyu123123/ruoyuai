# -*- coding: utf-8 -*-
"""value_polarity_probe R20 W9 Batch-BB · P2 · Coyne/McKee 价值极性
确定性·零依赖。"""
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import value_polarity_probe as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("VALUE_POLARITY_MODE", None)
    else:
        os.environ["VALUE_POLARITY_MODE"] = m


_TURN_FULL = [
    {"value_axis": "生/死", "start_polarity": "positive", "end_polarity": "strongly_negative"},
    {"value_axis": "信任/背叛", "start_polarity": "neutral", "end_polarity": "negative"},
    {"value_axis": "自由/束缚", "start_polarity": "negative", "end_polarity": "positive"},
    {"value_axis": "希望/绝望", "start_polarity": "positive", "end_polarity": "strongly_positive"},
]

_NO_TURN = [
    {"value_axis": "x", "start_polarity": "neutral", "end_polarity": "neutral"},
    {"value_axis": "y", "start_polarity": "positive", "end_polarity": "positive"},
    {"value_axis": "z", "start_polarity": "neutral", "end_polarity": "neutral"},
]

_MIXED = [
    {"value_axis": "x", "start_polarity": "neutral", "end_polarity": "neutral"},
    {"value_axis": "y", "start_polarity": "negative", "end_polarity": "positive"},
    {"value_axis": "z", "start_polarity": "neutral", "end_polarity": "neutral"},
    {"value_axis": "w", "start_polarity": "positive", "end_polarity": "negative"},
]

_EMPTY = [{"summary": "x"} for _ in range(5)]


def test_off():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("off")
        out = mod.probe(_TURN_FULL)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_empty_storyboard():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        out = mod.probe([])
        assert "scene_storyboard 为空" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_full_turn_pass():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_TURN_FULL)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "VALUE_NO_TURN" not in codes
        assert "TURN_FIDELITY_LOW" not in codes
        assert out["turn_fidelity_rate"] == 1.0
    finally:
        _set_mode(bak)


def test_no_turn_flagged():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_NO_TURN)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "VALUE_NO_TURN" in codes
        assert "TURN_FIDELITY_LOW" in codes
        assert out["turn_fidelity_rate"] == 0.0
    finally:
        _set_mode(bak)


def test_mixed_low_fidelity():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_MIXED)
        # 2 turn / 4 filled = 0.5 < 0.7
        codes = {f["code"] for f in out.get("flags", [])}
        assert out["turn_fidelity_rate"] == 0.5
        assert "TURN_FIDELITY_LOW" in codes
    finally:
        _set_mode(bak)


def test_empty_fields_not_counted():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        out = mod.probe(_EMPTY)
        assert out["scenes_filled"] == 0
    finally:
        _set_mode(bak)


def test_invalid_polarity_caught():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        sb = [{"value_axis": "x", "start_polarity": "bogus", "end_polarity": "neutral"}]
        out = mod.probe(sb)
        assert out["invalid_polarity"] == 1
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("shadow")
        out = mod.probe(_NO_TURN)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def _write_event_cluster(project_root, clusters):
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")


def test_scan_selects_target_cluster_not_first_match(tmp_path):
    """回归锁：多 cluster 均非空 scene_storyboard 时，scan() 按 cluster_id 精确选中目标
    cluster，不是 first-match-wins（旧 _load_storyboard 的 bug）。"""
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _NO_TURN},
            {"cluster_id": "cluster_002", "scene_storyboard": _TURN_FULL},
        ])
        out = mod.scan(str(tmp_path), "cluster_002")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_scan_cluster_001_not_skipped(tmp_path):
    bak = os.environ.get("VALUE_POLARITY_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _NO_TURN},
        ])
        out = mod.scan(str(tmp_path), "cluster_001")
        codes = {v["code"] for v in out["violations"]}
        assert "VALUE_NO_TURN" in codes
    finally:
        _set_mode(bak)


def test_scan_missing_brief_no_crash(tmp_path):
    (tmp_path / "_数据库").mkdir()
    out = mod.scan(str(tmp_path), "cluster_003")
    assert out["violations"] == []
    assert out["verdict"] == "PASS"


def test_polarity_rank_complete():
    assert set(mod.POLARITY_RANK.keys()) == mod.VALID_POLARITY
