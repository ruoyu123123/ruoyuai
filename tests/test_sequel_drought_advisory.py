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
    {"unit_type": "scene"}, {"unit_type": "sequel"},
    {"unit_type": "scene"}, {"unit_type": "sequel"},
    {"unit_type": "scene"}, {"unit_type": "sequel"},
]

_LONG_RUN_SB = [
    {"unit_type": "scene"}, {"unit_type": "scene"}, {"unit_type": "scene"},
    {"unit_type": "scene"}, {"unit_type": "scene"}, {"unit_type": "scene"},
    {"unit_type": "scene"}, {"unit_type": "sequel"},
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
        sb = [{"unit_type": "scene"}] * 4 + [{"unit_type": "sequel"}]
        out = mod.advise(sb, proj)
        codes = {f["code"] for f in out.get("flags", [])}
        # 4 > 3 (literary 阈)
        assert "SEQUEL_DROUGHT" in codes
        assert out["genre_class"] == "literary"
    finally:
        _set_mode(bak)


def test_unit_type_missing_flagged():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        out = mod.advise(_UNTYPED_SB)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "UNIT_TYPE_MISSING" in codes
    finally:
        _set_mode(bak)


def test_author_baseline_override():
    bak = os.environ.get("SEQUEL_DROUGHT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre_tags=["严肃"],
                           baseline={"max_scene_run": 20})
        sb = [{"unit_type": "scene"}] * 4 + [{"unit_type": "sequel"}]
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
    # 同 _LONG_RUN_SB · 7 连续 scene + 1 sequel · max_run=7
    assert mod._max_scene_run(_LONG_RUN_SB) == 7
    # 全 sequel = 0
    assert mod._max_scene_run([{"unit_type": "sequel"}] * 5) == 0


def test_load_storyboard(tmp_path):
    path = tmp_path / "sb.json"
    path.write_text(json.dumps({"scene_storyboard": _GOOD_SB}, ensure_ascii=False),
                    encoding="utf-8")
    assert mod._load_storyboard(str(path)) == _GOOD_SB
