# -*- coding: utf-8 -*-
"""chapter_title_couplet_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_title_couplet_scanner as cc  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CHAPTER_TITLE_COUPLET_MODE", None)
    else:
        os.environ["CHAPTER_TITLE_COUPLET_MODE"] = m


def _mk_project(*, enable=True, titles=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if enable is True:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"huimu_couplet": True}, ensure_ascii=False),
            encoding="utf-8")
    elif isinstance(enable, dict):
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(enable, ensure_ascii=False), encoding="utf-8")
    if titles:
        (proj / "章节").mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(titles, 1):
            (proj / "章节" / f"第{i:03d}章_{t}.txt").write_text(
                "正文" * 100, encoding="utf-8")
    return proj


def _write(text="正文" * 50):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_off_mode_skeleton():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("off")
        rep = cc.scan(str(_write()))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_not_enabled_skips():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=False)
        rep = cc.scan(str(_write()), project_root=proj)
        assert "跳过" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_good_couplet_passes():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(enable=True,
                           titles=["林黛玉抛父进京都｜贾雨村夤缘复旧职",
                                   "贾宝玉初试云雨情｜刘姥姥一进荣国府"])
        rep = cc.scan(str(_write()), project_root=proj)
        assert rep["evaluated_count"] >= 1
    finally:
        _set_mode(bak)


def test_no_split_majority_flags():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("active")
        # 4 个标题全无 ｜
        proj = _mk_project(enable=True,
                           titles=["序章", "开篇", "落幕", "终章"])
        rep = cc.scan(str(_write()), project_root=proj)
        assert rep["no_split_ratio"] >= 0.5
        codes = [v["code"] for v in rep["violations"]]
        assert "ZHANGHUI_HUIMU_PARALLELISM_BROKEN" in codes
    finally:
        _set_mode(bak)


def test_broken_parallelism():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("active")
        # 字数严重不对仗
        proj = _mk_project(enable=True,
                           titles=["短｜这是一个非常非常长的下半句不对仗",
                                   "甲｜这边好长好长好长",
                                   "丙｜也是长长长长不对仗的"])
        rep = cc.scan(str(_write()), project_root=proj)
        # 至少 broken_ratio > 0
        assert rep["broken_count"] >= 1
    finally:
        _set_mode(bak)


def test_classical_pastiche_gate():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            enable={"genre": "wuxia", "classical_pastiche": True},
            titles=["甲乙丙丁戊｜己庚辛壬癸"])
        rep = cc.scan(str(_write()), project_root=proj)
        assert rep["gate_reason"]
    finally:
        _set_mode(bak)


def test_shadow_mode_no_violation():
    bak = os.environ.get("CHAPTER_TITLE_COUPLET_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(enable=True,
                           titles=["a", "b", "c", "d"])
        rep = cc.scan(str(_write()), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "ZHANGHUI_HUIMU_PARALLELISM_BROKEN" not in hgs


def test_split_couplet_helper():
    assert cc.split_couplet("林黛玉抛父进京都｜贾雨村夤缘复旧职") is not None
    assert cc.split_couplet("单独一行") is None


def test_parallelism_score_helper():
    s = cc.parallelism_score("林黛玉抛父进京都", "贾雨村夤缘复旧职")
    assert s["length_ok"] is True
    assert s["punct_ok"] is True
