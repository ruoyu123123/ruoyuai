# -*- coding: utf-8 -*-
"""sequel_drought_advisory R20 W9 Batch-BB · P2 · Swain Scene/Sequel sequel drought
确定性·零依赖。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import sequel_drought_advisory as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("SEQUEL_DROUGHT_MODE", None)
    else:
        os.environ["SEQUEL_DROUGHT_MODE"] = m


def _mk_project(genre_tags=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    payload = {}
    if genre_tags:
        payload["genre_tags"] = genre_tags
    if baseline:
        payload["author_sequel_baseline"] = baseline
    if payload:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return proj


_GOOD_SB = [
    {"scene_type": "proactive_scene"}, {"scene_type": "reactive_sequel"},
    {"scene_type": "proactive_scene"}, {"scene_type": "reactive_sequel"},
    {"scene_type": "proactive_scene"}, {"scene_type": "reactive_sequel"},
]

_LONG_RUN_SB = [
    {"scene_type": "proactive_scene"}, {"scene_type": "proactive_scene"},
    {"scene_type": "proactive_scene"}, {"scene_type": "proactive_scene"},
    {"scene_type": "proactive_scene"}, {"scene_type": "proactive_scene"},
    {"scene_type": "proactive_scene"}, {"scene_type": "reactive_sequel"},
]

_UNTYPED_SB = [{"summary": "x"} for _ in range(6)]


def test_off():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("off")
        out = mod.advise(_GOOD_SB)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_empty():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        out = mod.advise([])
        assert "scene_storyboard 为空" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_good_pass():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        out = mod.advise(_GOOD_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SEQUEL_DROUGHT" not in codes
        assert out["max_scene_run"] == 1
    finally:
        _set_mode(bak)


def test_long_run_webnovel_default_passes():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre_tags=["爽文"])
        out = mod.advise(_LONG_RUN_SB, proj)
        # max_run=7 > 5 (web_novel 阈) → flag
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SEQUEL_DROUGHT" in codes
        assert out["max_scene_run"] == 7
        assert out["genre_class"] == "webnovel"
    finally:
        _set_mode(bak)


def test_long_run_literary_stricter():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre_tags=["严肃"])
        sb = [{"scene_type": "proactive_scene"}] * 4 + [{"scene_type": "reactive_sequel"}]
        out = mod.advise(sb, proj)
        codes = {f["code"] for f in out.get("flags", [])}
        # 4 > 3 (literary 阈)
        assert "SEQUEL_DROUGHT" in codes
        assert out["genre_class"] == "literary"
    finally:
        _set_mode(bak)


def test_scene_type_missing_flagged():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        out = mod.advise(_UNTYPED_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_TYPE_MISSING" in codes
    finally:
        _set_mode(bak)


def test_author_baseline_override():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre_tags=["严肃"],
                           baseline={"max_scene_run": 20})
        sb = [{"scene_type": "proactive_scene"}] * 4 + [{"scene_type": "reactive_sequel"}]
        out = mod.advise(sb, proj)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SEQUEL_DROUGHT" not in codes
        assert out["baseline_source"] == "author_profile"
        assert out["max_run_threshold"] == 20
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("shadow")
        out = mod.advise(_UNTYPED_SB)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_max_scene_run_calc():
    # 同 _LONG_RUN_SB · 7 连续 proactive_scene + 1 reactive_sequel · max_run=7
    assert mod._max_scene_run(_LONG_RUN_SB) == 7
    # 全 reactive_sequel = 0
    assert mod._max_scene_run([{"scene_type": "reactive_sequel"}] * 5) == 0


def _write_event_cluster(project_root, clusters):
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")


def test_scan_selects_target_cluster_not_first_match(tmp_path):
    """回归锁：多 cluster 均非空 scene_storyboard 时，scan() 按 cluster_id 精确选中目标
    cluster，不是 first-match-wins（旧 _load_storyboard 的 bug）。"""
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _LONG_RUN_SB},
            {"cluster_id": "cluster_002", "scene_storyboard": _GOOD_SB},
        ])
        out = mod.scan(str(tmp_path), "cluster_002")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_scan_cluster_001_not_skipped(tmp_path):
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        _write_event_cluster(tmp_path, [
            {"cluster_id": "cluster_001", "scene_storyboard": _LONG_RUN_SB},
        ])
        out = mod.scan(str(tmp_path), "cluster_001")
        codes = {v["code"] for v in out["violations"]}
        assert "SEQUEL_DROUGHT" in codes
    finally:
        _set_mode(bak)


def test_scan_missing_brief_no_crash(tmp_path):
    (tmp_path / "_数据库").mkdir()
    out = mod.scan(str(tmp_path), "cluster_003")
    assert out["violations"] == []
    assert out["verdict"] == "PASS"
