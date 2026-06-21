# -*- coding: utf-8 -*-
"""cross_book_rank_scarcity_scanner R19 W8 Batch-V·P0 跨书顶阶稀缺塌缩回归。

确定性·零依赖。覆盖 off/短稿/无 ledger 跳过/inflation 密度比/leapfrog 战斗/
collect_cross_book_hint 入口/shadow/active/CLI/hard_gate registry 守卫。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cross_book_rank_scarcity_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_book_rank_scarcity_scanner.py"
_ENV = "CROSS_BOOK_RANK_SCARCITY_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(ledger=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        (proj / "_数据库" / "series_rank_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


_BASE_TEXT = ("一行人走过荒野。风很大。远处有山起伏不绝。他默默想着事情。" * 100)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_BASE_TEXT))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_ledger_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(ledger=None)
        r = mod.scan(_write(_BASE_TEXT), project_root=proj)
        assert "无 series_rank_ledger" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_inflation_density_ratio():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        ledger = {
            "schema_version": 1,
            "current_book_top_threshold_tier": 5,
            "ranks": [{
                "name": "金丹", "tier": 5,
                "previous_book_top_named": ["李慕婉"],
                "cross_book_baseline_density_per_cluster": 1.0,
            }],
        }
        proj = _mk_project(ledger=ledger)
        # 草稿大量提及"金丹"+ "李慕婉"·密度比 >> 3.0
        text = (("金丹李慕婉金丹李慕婉。" * 50) + _BASE_TEXT)
        r = mod.scan(_write(text), project_root=proj)
        assert r["verdict"] == "FAIL_MINOR"
        codes = [v["code"] for v in r["violations"]]
        assert "CROSS_BOOK_RANK_INFLATION" in codes
        assert r["metrics"]["density_ratio"] >= 3.0
    finally:
        _set_mode(bak)


def test_leapfrog_battle_streak():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        ledger = {
            "schema_version": 1,
            "current_book_top_threshold_tier": 5,
            "ranks": [{
                "name": "金丹", "tier": 5,
                "previous_book_top_named": ["老魔"],
                "cross_book_baseline_density_per_cluster": 100000.0,
            }],
        }
        proj = _mk_project(ledger=ledger)
        scene = ("金丹老魔出现。主角轻松一招击败金丹。" + ("一段过渡描写。" * 500))
        scenes = "\n\n".join([scene] * 4)  # 4 大场景全顶阶轻胜
        r = mod.scan(_write(scenes), project_root=proj)
        assert r["metrics"]["leapfrog_scenes"] >= 3
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_low_density_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        ledger = {
            "schema_version": 1,
            "current_book_top_threshold_tier": 5,
            "ranks": [{
                "name": "金丹", "tier": 5,
                "previous_book_top_named": ["李慕婉"],
                "cross_book_baseline_density_per_cluster": 100.0,  # 基线极高
            }],
        }
        proj = _mk_project(ledger=ledger)
        # 草稿只提一次顶阶
        text = ("李慕婉曾经来过这里。" + _BASE_TEXT)
        r = mod.scan(_write(text), project_root=proj)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        ledger = {
            "schema_version": 1,
            "current_book_top_threshold_tier": 5,
            "ranks": [{
                "name": "金丹", "tier": 5,
                "previous_book_top_named": ["李慕婉"],
                "cross_book_baseline_density_per_cluster": 1.0,
            }],
        }
        proj = _mk_project(ledger=ledger)
        text = (("金丹李慕婉。" * 80) + _BASE_TEXT)
        r = mod.scan(_write(text), project_root=proj)
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_collect_cross_book_hint_returns_dict():
    ledger = {
        "schema_version": 1,
        "current_book_top_threshold_tier": 5,
        "ranks": [{
            "name": "金丹", "tier": 5,
            "previous_book_top_named": ["李慕婉", "老魔"],
            "cross_book_baseline_density_per_cluster": 2.0,
        }],
    }
    proj = _mk_project(ledger=ledger)
    hint = mod.collect_cross_book_hint(proj)
    assert hint is not None
    assert "李慕婉" in hint["previous_book_top_named"]
    assert hint["previous_book_baseline_density_per_cluster"] == 2.0


def test_collect_cross_book_hint_none_when_no_ledger():
    proj = _mk_project(ledger=None)
    assert mod.collect_cross_book_hint(proj) is None


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_split_cluster_into_scenes_nonempty():
    text = ("一段。" * 500)
    scenes = mod._split_cluster_into_scenes(text)
    assert len(scenes) >= 1


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs_shadow():
    r = _run_cli(_write(_BASE_TEXT))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "cross_book_rank_scarcity"
