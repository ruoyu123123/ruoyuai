# -*- coding: utf-8 -*-
"""butler_yearning_4layer_scanner R20 W9 Batch-BB · P2 · Butler 四层 yearning
确定性·零依赖·零 LLM/零联网。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import butler_yearning_4layer_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "butler_yearning_4layer_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("BUTLER_YEARNING_MODE", None)
    else:
        os.environ["BUTLER_YEARNING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_yearning_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高 yearning 密度·四层混合
_RICH_YEARNING = (
    "她想成为本来面目，做回真实的自己。\n\n他想被看见，想证明自己。\n\n"
    "她想回家，回那个再没去过的村。\n\n她只想再见一面，再见你一面。"
) * 25

# 单层垄断·全 connection
_MONOTONE = "她只想见他，想见你，想见她。重逢就好。" * 50

# 全空·无 yearning 词
_ABSENT = "屋子很大。窗外天黑了。桌上有杯茶。墙上挂着一幅画。地上是地毯。" * 40


def test_off():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_RICH_YEARNING), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "yearning_per_kcjk" not in out
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_ABSENT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_absent_flagged():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_ABSENT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_YEARNING_ABSENT" in codes
    finally:
        _set_mode(bak)


def test_active_rich_pass():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_RICH_YEARNING), _mk_project())
        assert out["yearning_per_kcjk"] >= 1.5
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_YEARNING_ABSENT" not in codes
    finally:
        _set_mode(bak)


def test_active_monotone_flagged():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_MONOTONE), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "YEARNING_LAYER_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_declared_dominant_silences_monotone():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(baseline={"dominant_layer": "connection_yearning"})
        out = mod.scan(_write(_MONOTONE), proj)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "YEARNING_LAYER_MONOTONE" not in codes
        assert out["declared_dominant_layer"] == "connection_yearning"
    finally:
        _set_mode(bak)


def test_baseline_overrides():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(baseline={"per_kcjk_low": 0.0})
        out = mod.scan(_write(_ABSENT), proj)
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SCENE_YEARNING_ABSENT" not in codes
        assert out["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_short_skipped():
    bak = os.environ.get("BUTLER_YEARNING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("想回家。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_lexicon_loaded():
    lex = mod._load_lexicon()
    assert "buckets" in lex
    assert {"self_yearning", "identity_yearning", "place_yearning",
            "connection_yearning"} <= set(lex["buckets"].keys())


def test_split_scenes():
    parts = mod._split_scenes("a\n\nb\n\nc")
    assert len(parts) == 3


def test_main_cli():
    p = _write(_ABSENT)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "BUTLER_YEARNING_MODE": "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "butler_yearning_4layer"
