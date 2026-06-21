# -*- coding: utf-8 -*-
"""audio_performance_scanner R23 W11 Batch-II · P2 · 朗读 craft advisory"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import audio_performance_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("AUDIO_PERFORMANCE_MODE", None)
    else:
        os.environ["AUDIO_PERFORMANCE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(relax=False, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    body = {}
    if relax:
        body["audio_performance_relax"] = True
    if baseline:
        body["audio_performance_baseline"] = baseline
    if body:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return proj


# 三分句段密集：每段 4 句末标点
_TRI_HEAVY = ("\n\n".join(["他走了。她追了。雨停了。月亮升起来了。"] * 80))

# 抽象名词堆：每段含「情况状况局面」
_ABSTRACT_HEAVY = ("\n\n".join(["这种情况下，眼前的状况和现实的局面让人无奈。" * 6] * 6))

# 长括弧带过段
_PAREN_LONG = ("\n\n".join(["他走进房间（房间里灯光昏暗他抬头看了天花板上的吊灯发现里面有蜘蛛网密密麻麻还沾满了灰尘）然后坐下。" * 4] * 10))

# 干净文本：每段仅 1 句末标点·零长括弧·零抽象名词堆
_CLEAN = ("\n\n".join(["他走进房间坐下来慢慢喝了一口茶。"] * 20))


def test_off_returns_skeleton():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("off")
        r = mod.scan(_write(_TRI_HEAVY), _mk_project())
        assert r["mode"] == "off" and r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_TRI_HEAVY), _mk_project())
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_active_tri_clause_flagged():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_write(_TRI_HEAVY), _mk_project())
        codes = {v["code"] for v in r["violations"]}
        assert mod.ISSUE_TRI_CLAUSE in codes
    finally:
        _set_mode(bak)


def test_active_abstract_pile_flagged():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_write(_ABSTRACT_HEAVY), _mk_project())
        codes = {v["code"] for v in r["violations"]}
        assert mod.ISSUE_ABSTRACT_NOUN in codes
    finally:
        _set_mode(bak)


def test_active_parenthesis_drag_flagged():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_write(_PAREN_LONG), _mk_project())
        codes = {v["code"] for v in r["violations"]}
        assert mod.ISSUE_PARENTHESIS in codes
    finally:
        _set_mode(bak)


def test_clean_no_violations():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_write(_CLEAN), _mk_project())
        assert r["violations"] == [] or r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_relax_raises_thresholds():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(relax=True)
        r = mod.scan(_write(_TRI_HEAVY), proj)
        assert r["author_relax"] is True
        assert r["thresholds"]["tri_target"] >= mod.TRI_CLAUSE_DENSITY_DEFAULT
    finally:
        _set_mode(bak)


def test_baseline_overrides():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(baseline={
            "tri_clause_density_target": 1.5,
            "abstract_noun_density_target": 100.0,
            "parenthesis_long_ratio_target": 1.5,
        })
        r = mod.scan(_write(_TRI_HEAVY), proj)
        # 阈值已被作者档调高·应不报
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"), _mk_project())
        assert r.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_metrics_present():
    bak = os.environ.get("AUDIO_PERFORMANCE_MODE")
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_ABSTRACT_HEAVY), _mk_project())
        m = r["metrics"]
        assert "tri_clause_density" in m
        assert "abstract_per_kcjk" in m
        assert "long_parenthesis_density" in m
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    text = "他走进房间。\n---CHANGES---\nignored"
    out = mod._strip_changes(text)
    assert "ignored" not in out


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_TRI_CLAUSE not in audit_hub.HARD_GATE_CODES
    assert mod.ISSUE_ABSTRACT_NOUN not in audit_hub.HARD_GATE_CODES
    assert mod.ISSUE_PARENTHESIS not in audit_hub.HARD_GATE_CODES
