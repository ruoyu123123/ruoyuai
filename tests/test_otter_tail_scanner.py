# -*- coding: utf-8 -*-
"""otter_tail_scanner R23 W11 Batch-GG · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import otter_tail_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("OTTER_TAIL_MODE", None)
    else:
        os.environ["OTTER_TAIL_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(pacing_style: str | None = None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if pacing_style is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_pacing_style": pacing_style}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高张力主体（情绪标点 + 短句）+ 平静獭尾末段
_HIGH_TENSION_BODY = (
    "他冲过来！剑光闪烁！\n"
    "嘶吼连连！\n"
    "倒下了！\n"
    "血迹遍地！\n"
) * 200

_OTTER_TAIL = (
    "战斗结束了。\n"
    "他静静地站着，看着天空中飘过的云。\n"
    "转身离开战场，背影在夕阳里慢慢拉长。\n"
    "多年后，他偶尔会想起这一天，但已经不再有当初的火气。\n"
    "时光流逝，岁月把锋芒磨成了温润的玉。\n"
    "从此以后，他踏上了另一条路，那条路通向哪里，他自己也不知道。\n"
)
_OTTER_TAIL_TEXT = _HIGH_TENSION_BODY + _OTTER_TAIL * 12

# 高密度强爽点收尾（无獭尾特征）
_KICKER_TEXT = (_HIGH_TENSION_BODY + "突然！他猛地出手！" * 60)


def test_off_returns_skeleton():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_OTTER_TAIL_TEXT), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_OTTER_TAIL_TEXT), _mk_project("悠长余韵"))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_otter_tail_detected():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_OTTER_TAIL_TEXT), _mk_project("悠长余韵"))
        # 三特征至少 2 命中（distancing + future · 张力衰减视长度而定）
        assert out["feature_count"] >= 2
        codes = {v["code"] for v in out.get("violations", [])}
        assert "OTTER_TAIL_DETECTED" in codes
    finally:
        _set_mode(bak)


def test_active_missing_for_otter_author():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_KICKER_TEXT), _mk_project("古典留白"))
        codes = {v["code"] for v in out.get("violations", [])}
        if out["feature_count"] == 0:
            assert "OTTER_TAIL_MISSING" in codes
    finally:
        _set_mode(bak)


def test_active_incompatible_kicker_author():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_OTTER_TAIL_TEXT), _mk_project("强爽点"))
        if out["feature_count"] >= 2:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "OTTER_TAIL_INCOMPATIBLE" in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_no_author_pacing_no_missing():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_KICKER_TEXT), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        # 无作者档 → 不报 MISSING（不主张推荐）
        assert "OTTER_TAIL_MISSING" not in codes
    finally:
        _set_mode(bak)


def test_features_keys_present():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_OTTER_TAIL_TEXT), _mk_project())
        feats = out.get("features") or {}
        for k in ("tension_decay", "distancing_drift", "future_anchor"):
            assert k in feats
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("OTTER_TAIL_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("OTTER_TAIL_DETECTED", "OTTER_TAIL_MISSING", "OTTER_TAIL_INCOMPATIBLE"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("otter_tail")
    assert s is not None
    assert s.get("_new") is True


def test_distancing_lex_hits():
    text = "他转身离开，不再回头，放下心结，走出大门。"
    hits = sum(text.count(w) for w in mod._DISTANCING_LEX)
    assert hits >= 4


def test_future_lex_hits():
    text = "多年后，他在某个春去秋来的日子想起这件事。从此岁月静好。"
    hits = sum(text.count(w) for w in mod._FUTURE_LEX)
    assert hits >= 2
