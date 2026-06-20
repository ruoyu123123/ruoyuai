# -*- coding: utf-8 -*-
"""expansion_ratio_gate 专属回归(2026-06-20·R8 W4 Batch-J·L32)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import expansion_ratio_gate as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("EXPANSION_RATIO_MODE", None)
    else:
        os.environ["EXPANSION_RATIO_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(brief_scope="", scene_summaries=None,
                author_baseline=None, status="in_progress",
                cluster_id="cluster_001"):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    storyboard = []
    for s in scene_summaries or []:
        storyboard.append({"summary": s})
    ec = {"clusters": [{"cluster_id": cluster_id, "status": status,
                        "scope_summary": brief_scope,
                        "scene_storyboard": storyboard}]}
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps(ec, ensure_ascii=False), encoding="utf-8")
    if author_baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"expansion_ratio_baseline": author_baseline},
                       ensure_ascii=False), encoding="utf-8")
    return proj


_DRAFT = "古风正文。" * 600  # ≥ 500 CJK


def test_off_returns_skeleton():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(brief_scope="x" * 100)
        out = mod.scan(_write(_DRAFT), proj)
        assert out["mode"] == "off"
        assert "expansion_ratio" not in out
    finally:
        _set_mode(bak)


def test_active_in_band_pass():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        brief = "故事大纲段落。" * 30  # 约 240 chars
        proj = _mk_project(brief_scope=brief)
        out = mod.scan(_write(_DRAFT), proj)
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["expansion_ratio"] > 0
    finally:
        _set_mode(bak)


def test_active_low_ratio_fail():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        # 极长 brief + 短 draft → 低 ratio
        brief = "x" * 5000
        proj = _mk_project(brief_scope=brief)
        # 改成中等长度 draft
        out = mod.scan(_write("中等内容。" * 250), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert "偏低" in (out.get("warning") or "")
    finally:
        _set_mode(bak)


def test_active_high_ratio_fail():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        brief = "x" * 30  # 略 > 20 阈值
        proj = _mk_project(brief_scope=brief)
        long_draft = "扩写正文。" * 2000  # 远超 60x (CJK 8000)
        out = mod.scan(_write(long_draft), proj)
        # brief 字符 30, draft 字符 ~8000 → ratio ~266 > 60
        assert out["verdict"] == "FAIL_MINOR"
        assert "偏高" in (out.get("warning") or "")
    finally:
        _set_mode(bak)


def test_active_no_brief_skips():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir()
        out = mod.scan(_write(_DRAFT), proj)
        assert "无 active cluster brief" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_brief_too_short_skips():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(brief_scope="短")
        out = mod.scan(_write(_DRAFT), proj)
        assert "brief 字符数过少" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_author_baseline_used():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        brief = "x" * 200
        proj = _mk_project(brief_scope=brief,
                            author_baseline={"mean": 20, "std": 2, "n": 10})
        out = mod.scan(_write(_DRAFT), proj)
        assert "author_baseline" in out["band_source"]
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("shadow")
        brief = "x" * 5000
        proj = _mk_project(brief_scope=brief)
        out = mod.scan(_write("中等。" * 200), proj)
        # 低 ratio 但 shadow 不上报
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(brief_scope="x" * 100)
        out = mod.scan(_write("短稿。" * 5), proj)
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_brief_char_count_with_scenes():
    brief = {"scope_summary": "段一",
              "scene_storyboard": [{"summary": "场景1"}, {"summary": "场景2"}]}
    assert mod._brief_char_count(brief) == len("段一场景1场景2")


def test_cluster_key_lookup():
    proj = _mk_project(brief_scope="x" * 100, cluster_id="cluster_007",
                        status="done")
    brief = mod._active_cluster_brief(proj, cluster_key="cluster_007")
    assert brief is not None and brief["cluster_id"] == "cluster_007"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("EXPANSION_RATIO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)
