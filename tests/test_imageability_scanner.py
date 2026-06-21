# -*- coding: utf-8 -*-
"""imageability_scanner R20 W9 Batch-CC · P2 · Paivio dual-coding 具象度 z-band
确定性·零依赖·零 LLM/零联网。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import imageability_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "imageability_scanner.py"
_ENV = "IMAGEABILITY_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


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
            json.dumps({"imageability_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高具象 (大量 high_imageability 词)
_HIGH_CONCRETE = "桌子上有茶杯。门外是窗。墙上挂着灯。床边是镜子。刀剑锁钥匙都在。" * 20

# 低具象 (大量 low_imageability 词)
_LOW_ABSTRACT = "意义本质真理理念概念。信念原则立场态度情感。理论逻辑思想理想幻想。" * 20

# 平衡
_BALANCED = ("桌子上有意义。茶杯里盛着信念。门外的概念。灯下的本质。" * 20)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_HIGH_CONCRETE), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "high_count" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("桌椅。"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LOW_ABSTRACT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_high_concrete_index_positive():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HIGH_CONCRETE), _mk_project())
        assert out["imageability_index"] > 0.5
        assert out["high_count"] > out["low_count"]
    finally:
        _set_mode(bak)


def test_low_abstract_index_negative():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LOW_ABSTRACT), _mk_project())
        assert out["imageability_index"] < -0.5
        assert out["low_count"] > out["high_count"]
    finally:
        _set_mode(bak)


def test_active_abstract_flagged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LOW_ABSTRACT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        # index ≈ -1.0 · default (0, 0.3) · z ≈ -3 → 报
        assert mod.ISSUE_CODE in codes
    finally:
        _set_mode(bak)


def test_author_baseline_overrides():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极宽 baseline → 不报
        out = mod.scan(_write(_LOW_ABSTRACT),
                       _mk_project(baseline={"mean": 0.0, "std": 100.0}))
        assert out["baseline_source"] == "author_profile"
        codes = {f["code"] for f in out.get("flags", [])}
        assert mod.ISSUE_CODE not in codes
    finally:
        _set_mode(bak)


def test_too_few_hits_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # MIN_CJK 够 · 但只有 0/0 词典命中
        text = "走路。走路。走路。" * 200
        out = mod.scan(_write(text), _mk_project())
        assert "样本不足" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_lexicon_loaded():
    lex = mod._load_lexicon()
    assert "high_imageability" in lex
    assert "low_imageability" in lex
    # 60+60 placeholder
    assert len(lex["high_imageability"]) >= 50
    assert len(lex["low_imageability"]) >= 50


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HIGH_CONCRETE), _mk_project())
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_count_terms():
    assert mod._count_terms("桌子桌子椅子", ["桌子"]) == 2


def test_z_function():
    assert mod._z(1.0, 0.0, 0.5) == 2.0
    assert mod._z(0.0, 0.0, 0.0) == 0.0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_CODE not in audit_hub.HARD_GATE_CODES


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_HIGH_CONCRETE)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "imageability"
