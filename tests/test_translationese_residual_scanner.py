# -*- coding: utf-8 -*-
"""translationese_residual_scanner R11 W6 MODEST 回归(确定性·零依赖)"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import translationese_residual_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("TRANSLATIONESE_RESIDUAL_MODE", None)
    else:
        os.environ["TRANSLATIONESE_RESIDUAL_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 典型译文味草稿（多层的、被字、长定语、复名）
_HEAVY = ("阿伦的母亲的旧书桌的台灯的灯罩闪着光。\n"
          "阿伦被狠狠地推倒。阿伦被她无情地拒绝。\n"
          "那位在远方田野上久久伫立的孤独的旅人的影子。\n"
          "阿伦看着她。阿伦说话了。阿伦走开了。阿伦回头。阿伦再次说。阿伦怒了。\n") * 12

_CLEAN = ("他走入山门，松针落满石阶。师兄在丹房煮茶。\n"
          "她笑了。月色清亮。江面起风。\n") * 32


def test_off():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_HEAVY))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_heavy_flags():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HEAVY))
        assert out["metrics"]["de_stack_depth_per_1k"] > 0
        assert out["metrics"]["bei_passive_per_1k"] > 0
        assert out["flagged_buckets"]
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_clean_de_stack_low():
    """干净草稿 de_stack_depth/被字 应低·name_overrep 因占位词表可能高(允许)"""
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CLEAN))
        # 至少 de_stack 应该是 0（干净草稿无多层的）
        assert out["metrics"]["de_stack_depth_per_1k"] == 0.0
        assert out["metrics"]["bei_passive_per_1k"] == 0.0
    finally:
        _set_mode(bak)


def test_shadow_heavy_no_violation():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HEAVY))
        assert out["violations"] == []
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_baseline_loaded():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True)
        baseline = {
            "de_stack_depth_per_1k": 5.0, "bei_passive_per_1k": 5.0,
            "pre_modifier_long_per_1k": 5.0, "name_overrep_per_1k": 30.0,
            "sd": {"de_stack_depth_per_1k": 2.0, "bei_passive_per_1k": 2.0,
                   "pre_modifier_long_per_1k": 2.0, "name_overrep_per_1k": 5.0},
        }
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"translationese_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
        out = mod.scan(_write(_HEAVY), proj)
        assert out["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_default_baseline_when_no_author():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HEAVY))
        assert out["baseline_source"] == "default_fallback"
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get("TRANSLATIONESE_RESIDUAL_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
