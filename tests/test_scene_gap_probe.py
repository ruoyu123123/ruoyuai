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


def _write_event_cluster(project_root, clusters):
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")


def test_scan_selects_target_cluster_not_first_match(tmp_path):
    """回归锁：多 cluster 均非空 scene_storyboard 时，scan() 按 cluster_id 精确选中目标
    cluster，不是 first-match-wins（旧 _load_storyboard 的 bug）。cluster_001 是 _EMPTY_SB
    (会触发 SCENE_GAP_ABSENT)，cluster_002 是 _FULL_SB(干净)——若选错会误报或漏报。"""
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _EMPTY_SB},
            {"cluster_id": "cluster_002", "scene_storyboard": _FULL_SB},
        ])
        out = mod.scan(str(tmp_path), "cluster_002")
        assert out["violations"] == []
        assert out["per_cluster"] == [{"cluster_id": "cluster_002", "scenes_total": 4}]
    finally:
        _set_mode(bak)


def test_scan_cluster_001_not_skipped(tmp_path):
    """cluster_001 不像 pre_write_gate 那样被跳过——storyboard 工艺质量对首块同样适用。"""
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _EMPTY_SB},
        ])
        out = mod.scan(str(tmp_path), "cluster_001")
        codes = {v["code"] for v in out["violations"]}
        assert "SCENE_GAP_ABSENT" in codes
    finally:
        _set_mode(bak)


def test_scan_missing_brief_no_crash(tmp_path):
    (tmp_path / "_数据库").mkdir()
    out = mod.scan(str(tmp_path), "cluster_003")
    assert out["violations"] == []
    assert out["verdict"] == "PASS"


def test_scan_shadow_mode_suppresses_violations(tmp_path):
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("shadow")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _EMPTY_SB},
        ])
        out = mod.scan(str(tmp_path), "cluster_001")
        assert out["violations"] == []
        assert out["mode"] == "shadow"
    finally:
        _set_mode(bak)


def test_invalid_gap_type_counted():
    bak = os.environ.get("SCENE_GAP_PROBE_MODE")
    try:
        _set_mode("active")
        sb = [{"expectation": "x", "actual_outcome": "y", "gap_type": "bogus"}]
        out = mod.probe(sb)
        assert out["invalid_types"] == 1
    finally:
        _set_mode(bak)
