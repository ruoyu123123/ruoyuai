# -*- coding: utf-8 -*-
"""onomatopoeia_density_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import onomatopoeia_density_scanner as od  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("ONOMATOPOEIA_MODE", None)
    else:
        os.environ["ONOMATOPOEIA_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genre="xianxia", baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    style = {"genre": genre}
    if baseline is not None:
        style["author_mimetic_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("off")
        rep = od.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_genre_skip():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="romance")
        rep = od.scan(str(_write("正文" * 500)), project_root=proj)
        assert "非二次元" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_xianxia_genre_active():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        text = "轰隆一声·啪嗒一下·亮晶晶的光\n" + "正文" * 500
        rep = od.scan(str(_write(text)), project_root=proj)
        assert "mimetic_per_kCJK" in rep
    finally:
        _set_mode(bak)


def test_no_mimetic_low_baseline_fallback():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        text = "正文" * 1000
        rep = od.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        # 兜底应触发
        assert "MIMETIC_DENSITY_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_baseline_below_threshold():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia",
                          baseline={"per_kCJK": 10.0})
        text = "轰隆\n" + "正文" * 1000
        rep = od.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "MIMETIC_DENSITY_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_short_skips():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia")
        rep = od.scan(str(_write("短")), project_root=proj)
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("ONOMATOPOEIA_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="xianxia")
        rep = od.scan(str(_write("正文" * 1000)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "MIMETIC_DENSITY_DRIFT" not in hgs


def test_classify_form_helper():
    counts = od.classify_form(["呼呼", "晃晃悠悠", "啪嗒啪嗒"])
    assert "AA" in counts and "ABAB" in counts
