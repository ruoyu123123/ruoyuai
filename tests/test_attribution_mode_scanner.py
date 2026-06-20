# -*- coding: utf-8 -*-
"""attribution_mode_scanner R11 W6 MODEST 回归"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import attribution_mode_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("ATTRIBUTION_MODE_MODE", None)
    else:
        os.environ["ATTRIBUTION_MODE_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genres, omniscient=False):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    obj = {"author_genre_packs": genres}
    if omniscient:
        obj["omniscient_narrator"] = True
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 全 inferred(无标记)的段落
_ALL_INFERRED = ("\n\n".join([
    "他在街上走过五点钟的钟楼。这街看起来空荡。" * 4 for _ in range(10)
]) + "\n")

# 5 桶都有的丰富段落
_RICH = ("\n\n".join([
    "我亲眼看到他下了车，目睹了一切的事情发生过程。" * 6,
    "据说那天发生了诡异的事，外间传闻很多版本众说纷纭。" * 6,
    "档案记载此事属实，载于一九四〇年笔记片段中。" * 6,
    "想必他当时心情复杂，应当是欲哭无泪的样子。" * 6,
    "他在街上走过，五点钟的钟楼，灯火寂寥无人。" * 6,
    "我亲耳听到他低声说了一句话。" * 6,
    "坊间传是他亲口承认了这件事情。" * 6,
    "据档案记又有补充材料，文献载之甚详。" * 6,
    "可以想见他的反应必是震惊不已。" * 6,
    "巷口一只猫盯着他看了许久未动。" * 6,
]) + "\n")


def test_off():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ALL_INFERRED))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_trigger_genre_skip():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia"])
        out = mod.scan(_write(_RICH), proj)
        assert "非纪录文学" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_omniscient_exempt():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["reportage"], omniscient=True)
        out = mod.scan(_write(_RICH), proj)
        assert "全知" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_all_inferred_flagged():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["reportage"])
        out = mod.scan(_write(_ALL_INFERRED), proj)
        codes = [f["code"] for f in out.get("flags", [])]
        assert "THOUGHT_ATTRIBUTED_UNDISCLOSED" in codes or \
               "ATTRIBUTION_DROUGHT_LONG" in codes or \
               "ATTRIBUTION_MODE_MONOTONE" in codes
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_rich_attribution_no_flag():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["literary_journalism"])
        out = mod.scan(_write(_RICH), proj)
        # 丰富 5 桶·monotone/drought 不命中(undisclosed 可能命中)
        codes = [f["code"] for f in out.get("flags", [])]
        assert "ATTRIBUTION_MODE_MONOTONE" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["reportage"])
        out = mod.scan(_write(_ALL_INFERRED), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_classify_each_bucket():
    assert mod.classify_paragraph("我亲眼看到他来。") == "direct"
    assert mod.classify_paragraph("据说他来了。") == "paraphrase"
    assert mod.classify_paragraph("档案记载他到访。") == "archived"
    assert mod.classify_paragraph("应当是他来过。") == "reconstructed"
    assert mod.classify_paragraph("纯叙述他来了。") == "inferred"


def test_read_settings_none():
    s = mod._read_settings(None)
    assert s["genres"] == set() and s["omniscient"] is False


def test_few_paragraphs_skip():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["reportage"])
        # 大段单 para
        d = "他在街上走过五点钟的钟楼，灯火寂寥无人。" * 60
        out = mod.scan(_write(d), proj)
        assert "段落 <5" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get("ATTRIBUTION_MODE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
