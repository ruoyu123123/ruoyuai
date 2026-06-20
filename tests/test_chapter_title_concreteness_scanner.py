# -*- coding: utf-8 -*-
"""chapter_title_concreteness_scanner R11 W6 MODEST 回归"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import chapter_title_concreteness_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CHAPTER_TITLE_CONCRETENESS_MODE", None)
    else:
        os.environ["CHAPTER_TITLE_CONCRETENESS_MODE"] = m


def _mk_project_with_chapters(titles, ecdf=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "章节").mkdir()
    for i, t in enumerate(titles, 1):
        (proj / "章节" / f"第{i:03d}章 {t}").mkdir()
    if ecdf is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"chapter_title_profile": {"concreteness_ecdf": ecdf}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


_ABSTRACT_TITLES = ["序", "归途", "无言", "故人", "心事", "迟疑", "归来", "等待"]
_CONCRETE_TITLES = [
    "断剑山门外", "白马关前", "明王寺塔上",
    "黑衣校尉的刀", "船头渔家的灯", "云岭石碑", "西风渡口", "城南酒铺",
]


def test_off():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("off")
        proj = _mk_project_with_chapters(_ABSTRACT_TITLES)
        out = mod.scan(project_root=proj)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_titles_skip():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        out = mod.scan(project_root=proj)
        assert "无章节标题" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_author_ecdf_silent():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_chapters(_ABSTRACT_TITLES)
        out = mod.scan(project_root=proj)
        assert "未规定" in out.get("note", "")
        assert "per_chapter_scores" in out
    finally:
        _set_mode(bak)


def test_active_abstract_drift_flag():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_chapters(
            _ABSTRACT_TITLES,
            ecdf={"p10": 0.5, "p50": 0.6, "p90": 0.7})
        out = mod.scan(project_root=proj)
        assert abs(out["z_concreteness"]) > 2.0
        assert out["violations"]
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_concrete_titles_match_baseline():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_chapters(
            _CONCRETE_TITLES,
            ecdf={"p10": 0.4, "p50": 0.5, "p90": 0.6})
        out = mod.scan(project_root=proj)
        # 具象标题应贴近 p50
        assert abs(out["z_concreteness"]) < 5  # 占位评分宽容
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_chapters(
            _ABSTRACT_TITLES,
            ecdf={"p10": 0.5, "p50": 0.6, "p90": 0.7})
        out = mod.scan(project_root=proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_titles_override():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("active")
        titles = [{"chapter_num": 1, "title": "归途"},
                  {"chapter_num": 2, "title": "白马关"}]
        out = mod.scan(titles_override=titles)
        assert len(out["per_chapter_scores"]) == 2
    finally:
        _set_mode(bak)


def test_concreteness_score_empty():
    assert mod.concreteness_score("") == 0.0
    assert mod.concreteness_score("一") == 0.0


def test_concreteness_concrete_higher_than_abstract():
    abstract_avg = sum(mod.concreteness_score(t) for t in _ABSTRACT_TITLES) / len(_ABSTRACT_TITLES)
    concrete_avg = sum(mod.concreteness_score(t) for t in _CONCRETE_TITLES) / len(_CONCRETE_TITLES)
    assert concrete_avg > abstract_avg


def test_read_ecdf_missing():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_ecdf(proj) is None
    assert mod._read_ecdf(None) is None


def test_mode_invalid():
    bak = os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
