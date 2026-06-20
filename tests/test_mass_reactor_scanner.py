# -*- coding: utf-8 -*-
"""mass_reactor_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import mass_reactor_scanner as mr  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("MASS_REACTOR_MODE", None)
    else:
        os.environ["MASS_REACTOR_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_mass_reactor_baseline": baseline},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("off")
        rep = mr.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("active")
        rep = mr.scan(str(_write("短")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_mass_block_detected():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("active")
        text = ("众人议论纷纷\n"
                "\"这是什么情况\"\n"
                "\"真不可思议\"\n"
                "\"我也不知道\"\n"
                "\"完全没办法\"\n"
                + "正文" * 500)
        rep = mr.scan(str(_write(text)))
        assert rep["mass_reactor_blocks"] >= 1
    finally:
        _set_mode(bak)


def test_no_blocks_when_no_mass_speaker():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("active")
        text = "他说话。她回答。" * 200 + "正文" * 200
        rep = mr.scan(str(_write(text)))
        assert rep["mass_reactor_blocks"] == 0
    finally:
        _set_mode(bak)


def test_baseline_drift_low():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(baseline={"density_target": 5.0})
        text = "普通正文。" * 1000
        rep = mr.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "MASS_REACTOR_DENSITY_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "MASS_REACTOR_DENSITY_DRIFT" not in hgs


def test_shadow_mode():
    bak = os.environ.get("MASS_REACTOR_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(baseline={"density_target": 5.0})
        rep = mr.scan(str(_write("正文" * 1000)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_detect_blocks_helper():
    text = ("弹幕飞过\n"
            "\"哇\"\n"
            "\"牛逼\"\n"
            "\"卧槽\"\n")
    blocks = mr.detect_blocks(text)
    assert isinstance(blocks, list)
