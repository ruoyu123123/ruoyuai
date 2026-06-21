# -*- coding: utf-8 -*-
"""soundscape_trinity_scanner R20 W9 Batch-BB · P2 · Schafer 声景三分类
确定性·零依赖。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import soundscape_trinity_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("SOUNDSCAPE_TRINITY_MODE", None)
    else:
        os.environ["SOUNDSCAPE_TRINITY_MODE"] = m


def _set_cluster(on):
    if on:
        os.environ["CLUSTER_MODE"] = "1"
    else:
        os.environ.pop("CLUSTER_MODE", None)


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_soundscape_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 三类均衡富声景
_RICH = "风声穿过山谷，雨声打在窗上。铃声忽然响起，门铃也响。" \
        "钟楼远远敲响，梆子声从巷口传来。" * 30

# 单声道：只 keynote
_MONO_KEYNOTE = "风声。雨声。蝉鸣。落叶。鸟鸣。" * 80

# 全静默
_SILENT = "屋子很大，墙上挂画，桌上摆茶，地板光亮，窗外天黑。" * 40


def test_off():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("off")
        out = mod.scan(_write(_RICH), _mk_project())
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_non_cluster_mode_skipped():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(False)
        _set_mode("active")
        out = mod.scan(_write(_RICH), _mk_project())
        assert "非 CLUSTER_MODE" in out.get("note", "")
    finally:
        _set_mode(bak)
        if cbak is not None:
            os.environ["CLUSTER_MODE"] = cbak


def test_silent_text_flagged():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("active")
        out = mod.scan(_write(_SILENT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SOUNDSCAPE_THIN" in codes
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_monotone_keynote_flagged():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("active")
        out = mod.scan(_write(_MONO_KEYNOTE), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        # entropy 应该很低（只有 keynote 桶有 hits）
        assert out["entropy"] < 0.8
        assert "SOUNDSCAPE_MONOTONE" in codes
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_rich_text_pass():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("active")
        out = mod.scan(_write(_RICH), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SOUNDSCAPE_THIN" not in codes
        # 三类都有 hits
        for bn in ("keynote", "signal", "soundmark"):
            assert out["bucket_hits"][bn] > 0
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_shadow_no_violation():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("shadow")
        out = mod.scan(_write(_SILENT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_baseline_override():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("active")
        out = mod.scan(_write(_SILENT),
                       _mk_project(baseline={"sound_per_kcjk_low": 0.0,
                                              "entropy_band": [0.0, 2.0]}))
        codes = {f["code"] for f in out.get("flags", [])}
        assert "SOUNDSCAPE_THIN" not in codes
        assert out["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_short_skipped():
    bak = os.environ.get("SOUNDSCAPE_TRINITY_MODE")
    cbak = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster(True)
        _set_mode("active")
        out = mod.scan(_write("风声。"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)
        if cbak is None:
            _set_cluster(False)
        else:
            os.environ["CLUSTER_MODE"] = cbak


def test_lexicon_loaded():
    lex = mod._load_lexicon()
    assert "buckets" in lex
    assert {"keynote", "signal", "soundmark"} == set(lex["buckets"].keys())


def test_entropy_calc():
    assert mod._entropy([1.0, 0.0, 0.0]) == 0.0
    h = mod._entropy([1.0, 1.0, 1.0])
    assert h > 1.5  # 接近 log2(3) ≈ 1.585
