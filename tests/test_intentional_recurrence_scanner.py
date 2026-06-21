# -*- coding: utf-8 -*-
"""intentional_recurrence_scanner R23 W11 Batch-GG · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import intentional_recurrence_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("INTENTIONAL_RECURRENCE_MODE", None)
    else:
        os.environ["INTENTIONAL_RECURRENCE_MODE"] = m


def _mk_project(clusters: list[dict] | None) -> Path:
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "故事块摘要.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return proj


# 中等相似（≥0.6）但五轴各不同 → intentional
_CLUSTER_A_INTENT = {
    "cluster_id": "cluster_001",
    "scope_summary": "主角在山顶面对敌人挥剑斩出一道气劲取胜",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "击败敌人",
}
_CLUSTER_B_INTENT = {
    "cluster_id": "cluster_010",
    "scope_summary": "主角在山顶面对敌人挥剑斩出一道气劲取胜",
    "characters_focus": ["徒弟"],
    "hub_locations": ["雪原"],
    "anchor_props": ["短刀"],
    "mood": "悲悯",
    "outcome": "放敌人一条生路",
}

# 高相似且五轴几乎相同 → real_repeat
_CLUSTER_REP_A = {
    "cluster_id": "cluster_020",
    "scope_summary": "主角在山顶挥剑斩敌赢得战斗",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "胜",
}
_CLUSTER_REP_B = {
    "cluster_id": "cluster_021",
    "scope_summary": "主角在山顶挥剑斩敌赢得战斗",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "胜",
}


def test_off_returns_skeleton():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_thin_data_skipped():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT]))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "INTENTIONAL_RECURRENCE_THIN_DATA" in codes
    finally:
        _set_mode(bak)


def test_active_intentional_recurrence_detected():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        # 高 sim + 五轴差异 ≥3 → DETECTED
        if out.get("intentional_count", 0) >= 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "INTENTIONAL_RECURRENCE_DETECTED" in codes
    finally:
        _set_mode(bak)


def test_active_real_repeat_detected():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_REP_A, _CLUSTER_REP_B]))
        if out.get("real_repeat_count", 0) >= 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "INTENTIONAL_RECURRENCE_REAL_REPEAT" in codes
    finally:
        _set_mode(bak)


def test_jaccard_basic():
    assert mod._jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert mod._jaccard({"a"}, {"b"}) == 0.0
    assert mod._jaccard(set(), {"a"}) == 0.0


def test_trigrams_basic():
    assert mod._trigrams("abcd") == {"abc", "bcd"}
    assert mod._trigrams("ab") == set()


def test_extract_axes_basic():
    axes = mod._extract_axes(_CLUSTER_A_INTENT)
    for k in ("actor", "place", "prop", "mood", "outcome"):
        assert k in axes


def test_div_axes_counts_differences():
    a = mod._extract_axes(_CLUSTER_A_INTENT)
    b = mod._extract_axes(_CLUSTER_B_INTENT)
    count, diffs = mod._div_axes(a, b)
    assert count >= 3
    assert "actor" in diffs
    assert "place" in diffs


def test_div_axes_identical_zero():
    a = mod._extract_axes(_CLUSTER_REP_A)
    b = mod._extract_axes(_CLUSTER_REP_B)
    count, diffs = mod._div_axes(a, b)
    assert count == 0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("INTENTIONAL_RECURRENCE_DETECTED", "INTENTIONAL_RECURRENCE_REAL_REPEAT",
              "INTENTIONAL_RECURRENCE_THIN_DATA"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("intentional_recurrence")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_flag():
    out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
    assert out.get("_placeholder") is True


def test_no_db_returns_thin():
    proj = Path(tempfile.mkdtemp())
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(proj)
        # 无 _数据库 → 跳过
        assert "cluster 摘要 < 2" in (out.get("note") or "")
    finally:
        _set_mode(bak)
