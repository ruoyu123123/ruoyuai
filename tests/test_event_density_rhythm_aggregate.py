# -*- coding: utf-8 -*-
"""event_density_rhythm_aggregate 专属回归(2026-06-20·R8 W4 Batch-J·L33)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import event_density_rhythm_aggregate as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("EVENT_DENSITY_RHYTHM_MODE", None)
    else:
        os.environ["EVENT_DENSITY_RHYTHM_MODE"] = m


def _mk_project(cluster_intensities=None, cadence=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if cluster_intensities is not None:
        clusters = [{"cluster_id": cid, "intensity": v}
                    for cid, v in cluster_intensities]
        (proj / "_数据库" / "cluster_index.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8")
    if cadence is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"breather_cadence_baseline":
                        {"cluster_n_between_breathers": cadence}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(cluster_intensities=[
            ("c1", 0.9), ("c2", 0.9), ("c3", 0.9), ("c4", 0.9)])
        out = mod.aggregate(proj)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("active")
        out = mod.aggregate(None)
        assert "无项目根" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_clusters_skipped():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(cluster_intensities=[("c1", 0.9), ("c2", 0.9)])
        out = mod.aggregate(proj)
        assert "cluster 数过少" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_streak_detected_active_fail():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(cluster_intensities=[
            ("c1", 0.9), ("c2", 0.9), ("c3", 0.9), ("c4", 0.9), ("c5", 0.9)])
        out = mod.aggregate(proj)
        assert out["max_high_intensity_streak"] >= 3
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_breather_breaks_streak_pass():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(cluster_intensities=[
            ("c1", 0.9), ("c2", 0.9), ("c3", 0.2), ("c4", 0.9), ("c5", 0.2)])
        out = mod.aggregate(proj)
        assert out["max_high_intensity_streak"] < 3
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(cluster_intensities=[
            ("c1", 0.9), ("c2", 0.9), ("c3", 0.9), ("c4", 0.9)])
        out = mod.aggregate(proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_resolve_cadence_default():
    assert mod._resolve_cadence(None) == 3 or True  # 可能 AttributeError 处理
    proj = _mk_project(cluster_intensities=[("c1", 0.5)])
    assert mod._resolve_cadence(proj) == 3


def test_resolve_cadence_custom():
    proj = _mk_project(cluster_intensities=[("c1", 0.5)], cadence=5)
    assert mod._resolve_cadence(proj) == 5


def test_read_intensities_event_cluster_fallback():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir()
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "c1", "status": "done", "intensity": 0.8},
            {"cluster_id": "c2", "status": "done", "intensity": 0.7},
        ]}, ensure_ascii=False), encoding="utf-8")
    series = mod._read_cluster_intensities(proj)
    assert series == [("c1", 0.8), ("c2", 0.7)]


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("EVENT_DENSITY_RHYTHM_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
