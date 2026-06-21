# -*- coding: utf-8 -*-
"""paragraph_engagement_heat_predictor R18 W7 Batch-U·P2 段落热度回归。

确定性·零依赖。覆盖 off/短稿/段落不足/通章 cold flat 报/正常 PASS/shadow/
读取失败/_mode/CLI/_heat 维度计算/_split_paragraphs/strip_changes/
hard_gate 注册防污染。
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
import paragraph_engagement_heat_predictor as mod  # noqa: E402

_TARGET = _SCRIPTS / "paragraph_engagement_heat_predictor.py"
_ENV = "PARAGRAPH_ENGAGEMENT_HEAT_MODE"


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


# 通章 cold（无 question / no ambiguous ref / no temporal suspense / no character amb）
_COLD_FLAT = ("\n\n".join(["天空很蓝海水很咸山高路远风吹叶落万物自有规律。" for _ in range(60)]))

# 热度高（带 question + ambiguous ref + temporal suspense + char ambiguity）
_HOT = ("\n\n".join(
    ["怎么会这样？还有十分钟就到了。那人没说话，只是站着。某种声音从远处传来。"
     for _ in range(60)]))


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短稿。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_paragraphs_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 字数够·段落 < 4
        text = "段落一" * 200 + "\n\n" + "段落二" * 200
        r = mod.scan(_write(text))
        assert "段落不足" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_cold_flat_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["verdict"] == "FAIL_MINOR"
        assert "cold flat" in (r["warning"] or "")
    finally:
        _set_mode(bak)


def test_hot_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_HOT))
        # 热度高·verdict PASS
        assert r["verdict"] == "PASS"
        assert r["metrics"]["mean_heat_score"] > 0
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["violations"] == []
        assert r["warning"] is None
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


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("中abc文") == 2


def test_heat_score_zero_for_plain():
    h = mod._heat("天空很蓝海水很咸")
    assert h["score"] >= 0
    assert h["question"] == 0


def test_heat_score_positive_for_question():
    h = mod._heat("为什么会这样？")
    assert h["question"] >= 1


def test_valence_positive():
    assert mod._valence("他微笑温暖安心") == 1


def test_valence_negative():
    assert mod._valence("怒火冷汗死亡") == -1


def test_valence_neutral():
    assert mod._valence("普通描述。") == 0


def test_split_paragraphs():
    assert len(mod._split_paragraphs("a\n\nb\n\nc")) == 3


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_warns_on_cold():
    r = _run_cli(_write(_COLD_FLAT))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
