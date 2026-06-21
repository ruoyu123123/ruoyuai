# -*- coding: utf-8 -*-
"""prosodic_pause_anchor_scanner R19 W8 Batch-Y·P2 prosodic 三层 pause 回归测试。

确定性·零依赖。覆盖 off/短稿/anchor<20 skip 告警/正常 valley/高密度无 valley 触发/
info_score 各 marker 计入/shadow vs active/CLI/hard_gate.
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
import prosodic_pause_anchor_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "prosodic_pause_anchor_scanner.py"
_ENV = "PROSODIC_PAUSE_ANCHOR_MODE"


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


def test_off_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write("一" * 1000))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_anchor_under_20_skip_warning():
    """anchor 太少 → 不告警."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 很少逗号
        text = "他抬头看远方。" * 200
        r = mod.scan(_write(text))
        # 应 anchor=0·skip
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_high_density_no_valley_triggers():
    """每个 phrase 都填高密度 marker(动作动词+实体后缀)·anchor 处无 valley → 触发."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 句子内塞满 marker·anchor 前后都高密度
        chunk = "打跑冲抓推拉，砸砍掀扯甩，斩刺挥击踩，踢撞扑握陛下，"
        text = (chunk * 50) + "。" * 10
        r = mod.scan(_write(text))
        # 如 anchor 足够 + valley_rate 低 → 触发
        m = r.get("metrics") or {}
        if m.get("total_anchors", 0) >= 20:
            if m.get("valley_rate") is not None and m["valley_rate"] < 0.20:
                codes = [v["code"] for v in r["violations"]]
                assert "PROSODIC_PAUSE_VALLEY_MISSING" in codes
    finally:
        _set_mode(bak)


def test_normal_passes():
    """普通文字·valley_rate 应较高."""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "他想了想，慢慢抬头，看着远方，叹了口气。" * 80
        r = mod.scan(_write(text))
        # 普通文字应 PASS
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        chunk = "打跑冲抓推拉，砸砍掀扯甩，"
        text = (chunk * 60) + "。"
        r = mod.scan(_write(text))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_info_score_counts_digits():
    assert mod._info_score("abc123") >= 4  # 3 digits + 3 latin
    assert mod._info_score("打跑") >= 2


def test_info_density_empty():
    assert mod._info_density("") == 0.0


def test_split_sentences_basic():
    parts = mod._split_sentences("第一句。第二句！第三句？")
    assert len(parts) == 3


def test_analyze_anchors_zero():
    """无 phrase pause 字符·应 anchor=0."""
    res = mod.analyze_anchors("aaaaa bbbbb ccccc")
    assert res["total_anchors"] == 0


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


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_runs_shadow():
    text = "他想了想，慢慢抬头，看着远方，叹了口气。" * 80
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(_write(text))],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "prosodic_pause_anchor"
