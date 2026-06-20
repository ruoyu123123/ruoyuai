# -*- coding: utf-8 -*-
"""status_block_density_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import status_block_density_scanner as sb  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("STATUS_BLOCK_MODE", None)
    else:
        os.environ["STATUS_BLOCK_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genre="litrpg", baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    style = {"genre": genre}
    if baseline is not None:
        style["status_block_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("off")
        rep = sb.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_genre_skip():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="romance")
        rep = sb.scan(str(_write("正文" * 500)), project_root=proj)
        assert "非 LitRPG" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_blocks_detected():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="litrpg")
        text = ("【任务面板】请前往黑森林\n"
                "【任务面板】请前往沼泽\n"
                "系统提示:你升级了\n"
                "+10 经验\n" + "正文" * 1000)
        rep = sb.scan(str(_write(text)), project_root=proj)
        assert rep["block_count"] >= 3
    finally:
        _set_mode(bak)


def test_baseline_zero_unauthorized_intro():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="litrpg",
                          baseline={"per_kCJK": 0})
        text = "【面板】" * 30 + "正文" * 500
        rep = sb.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "STATUS_BLOCK_UNAUTHORIZED_INTRODUCTION" in codes
    finally:
        _set_mode(bak)


def test_baseline_below_threshold():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="litrpg",
                          baseline={"per_kCJK": 10.0})
        text = "正文" * 1000
        rep = sb.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "STATUS_BLOCK_DENSITY_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="litrpg",
                          baseline={"per_kCJK": 10.0})
        rep = sb.scan(str(_write("正文" * 1000)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "STATUS_BLOCK_DENSITY_DRIFT" not in hgs
    assert "STATUS_BLOCK_UNAUTHORIZED_INTRODUCTION" not in hgs


def test_short_skips():
    bak = os.environ.get("STATUS_BLOCK_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="litrpg")
        rep = sb.scan(str(_write("短")), project_root=proj)
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)
