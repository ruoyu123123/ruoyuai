# -*- coding: utf-8 -*-
"""power_progression_scanner 专属测试 — tier 单调性/突跳/停滞(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import power_progression_scanner as pp  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("POWER_PROGRESSION_MODE", None)
    else:
        os.environ["POWER_PROGRESSION_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(arc=None, style=None, prefs=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if arc is not None:
        (proj / "_数据库" / "角色弧线.json").write_text(
            json.dumps(arc, ensure_ascii=False), encoding="utf-8")
    if style is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(style, ensure_ascii=False), encoding="utf-8")
    if prefs is not None:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    return proj


def _make_series(n, start=1, step=1, regress_at=None, plateau_from=None):
    chars = {"主角": {"role": "protagonist",
                    "protagonist_power_tier": []}}
    for i in range(n):
        tier = start + step * i
        notes = ""
        if plateau_from is not None and i >= plateau_from:
            tier = start + step * plateau_from
        if regress_at is not None and i == regress_at:
            tier = start + step * (i - 1) - 1
        chars["主角"]["protagonist_power_tier"].append(
            {"cluster_id": f"cluster_{i+1:03d}", "tier": tier,
             "notes": notes})
    return {"characters": chars}


def test_off_mode_skeleton():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("off")
        rep = pp.scan(str(_write("x" * 1000)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_arc_state_skips():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert "角色弧线" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_user_preference_off_skips():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(prefs={"power_progression_mode": "off"})
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert "用户偏好" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_genre_skip_romance():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(style={"genre": "romance"})
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert "romance" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_monotonic_progression_passes():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(arc=_make_series(10))
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_regression_without_injury_flag():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(arc=_make_series(6, regress_at=3))
        rep = pp.scan(str(_write("x")), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "POWER_TIER_REGRESSION" in codes
        assert rep["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_regression_with_injury_marker_passes():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        arc = _make_series(6)
        arc["characters"]["主角"]["protagonist_power_tier"][3]["tier"] = 1
        arc["characters"]["主角"]["protagonist_power_tier"][3]["notes"] = \
            "重伤·丹田碎"
        proj = _mk_project(arc=arc)
        rep = pp.scan(str(_write("x")), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "POWER_TIER_REGRESSION" not in codes
    finally:
        _set_mode(bak)


def test_acceleration_spike():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        arc = _make_series(15, step=1)
        # 中段一次大跳
        arc["characters"]["主角"]["protagonist_power_tier"][12]["tier"] = 100
        proj = _mk_project(arc=arc)
        rep = pp.scan(str(_write("x")), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "ESCALATION_ACCELERATION_SPIKE" in codes
    finally:
        _set_mode(bak)


def test_stall_no_plateau_marker():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        # 12 cluster · 后 10 完全 tier=5 无 plateau marker
        chars = {"主角": {"role": "protagonist",
                        "protagonist_power_tier":
                        [{"cluster_id": f"c{i:03d}", "tier": 1, "notes": ""}
                         for i in range(2)]
                        + [{"cluster_id": f"c{i:03d}", "tier": 5,
                            "notes": ""}
                           for i in range(2, 12)]}}
        proj = _mk_project(arc={"characters": chars})
        rep = pp.scan(str(_write("x")), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "PROGRESSION_STALL" in codes
    finally:
        _set_mode(bak)


def test_shadow_mode_no_violations():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(arc=_make_series(6, regress_at=3))
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert rep["violations"] == []
        assert rep["mode"] == "shadow"
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("POWER_TIER_REGRESSION", "ESCALATION_ACCELERATION_SPIKE",
             "PROGRESSION_STALL"):
        assert c not in hgs


def test_mode_invalid_falls_back():
    os.environ["POWER_PROGRESSION_MODE"] = "bogus"
    try:
        assert pp._mode() == "shadow"
    finally:
        os.environ.pop("POWER_PROGRESSION_MODE", None)


def test_short_series_skips():
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        _set_mode("active")
        arc = _make_series(1)
        proj = _mk_project(arc=arc)
        rep = pp.scan(str(_write("x")), project_root=proj)
        assert "过短" in rep.get("note", "")
    finally:
        _set_mode(bak)
