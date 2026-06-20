# -*- coding: utf-8 -*-
"""world_term_seepage_scanner 专属回归(2026-06-20·R8 W4 Batch-J·L36)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import world_term_seepage_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("WORLD_TERM_SEEPAGE_MODE", None)
    else:
        os.environ["WORLD_TERM_SEEPAGE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(terms=None, genre=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if terms is not None:
        (proj / "_数据库" / "世界观.json").write_text(
            json.dumps({"glossary": {t: "" for t in terms}},
                       ensure_ascii=False), encoding="utf-8")
    if genre is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"genre": genre}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(terms=["剑魂", "灵气", "玄铁"])
        out = mod.scan(_write("剑魂灵气玄铁。" * 200), proj)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_terms_skips():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(terms=["剑魂"])  # 只 1 个 < 3
        out = mod.scan(_write("剑魂剑魂剑魂剑魂。" * 200), proj)  # CJK ~1600
        assert "术语过少" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_gini_spread_pass():
    """术语分散出现 → Gini 低 → PASS"""
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(terms=["剑魂", "灵气", "玄铁", "禁制"])
        # 让 4 个术语首现位置均匀分散
        spacer = "他走过了山林。" * 200
        text = "剑魂出现。" + spacer + "灵气流转。" + spacer + "玄铁泛光。" + spacer + "禁制闪烁。" + spacer
        out = mod.scan(_write(text), proj)
        assert out["term_hit_count"] >= 3
        # 分散 → Gini 低
        assert out["first_position_gini"] < 0.55 or out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_gini_concentrated_fail():
    """术语集中出现于开头 → Gini 高 → advisory"""
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(terms=["剑魂", "灵气", "玄铁", "禁制", "心法"])
        # 全部塞前 100 chars (info-dump burst)
        text = "剑魂灵气玄铁禁制心法。" + ("他走在山中。" * 500)
        out = mod.scan(_write(text), proj)
        # 全在 pos 0~10 → 归一化全 ≈0 → gini 应 ~0
        # 反过来测试: 用术语序列 = 1, 5, 10, 200, 5000 强分布
        text2 = "剑魂。" + "他说。" * 1 + "灵气。" + "他想。" * 1 + "玄铁。"
        text2 += "他走出门。" * 500 + "禁制出现。心法练成。"
        out = mod.scan(_write(text2), proj)
        assert "first_position_gini" in out
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(terms=["剑魂", "灵气", "玄铁"])
        out = mod.scan(_write("剑魂灵气玄铁。" * 200), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(terms=["剑魂", "灵气", "玄铁"])
        out = mod.scan(_write("剑魂"), proj)
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_genre_override_litrpg():
    proj = _mk_project(terms=["剑魂"], genre="litrpg")
    assert mod._genre_override_gini(proj) == 0.65
    proj2 = _mk_project(terms=["剑魂"], genre="xianxia")
    assert mod._genre_override_gini(proj2) == 0.55


def test_extract_world_terms_list_schema():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir()
    (proj / "_数据库" / "世界观.json").write_text(
        json.dumps({"terms": ["剑魂", "灵气"]}, ensure_ascii=False),
        encoding="utf-8")
    assert mod._extract_world_terms(proj) == ["剑魂", "灵气"]


def test_gini_pure_uniform_zero():
    # 全相同值 → Gini ≈ 0
    g = mod._gini([0.5] * 10)
    assert g < 0.05


def test_gini_pure_skewed_high():
    g = mod._gini([0] * 9 + [10])
    assert g > 0.7


def test_read_failure_returns_note():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("WORLD_TERM_SEEPAGE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
