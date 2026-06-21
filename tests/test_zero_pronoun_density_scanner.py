# -*- coding: utf-8 -*-
"""zero_pronoun_density_scanner R18 W7 Batch-U·P2 中文 pro-drop ZP 回归。

确定性·零依赖。覆盖 off/短稿/作者档 z-band/通用兜底/翻译腔报警/clean PASS/
shadow/读取失败/_mode/CLI/_is_zero_pronoun_clause/_scan_clauses/
strip_changes/_load_baseline·registry 未污染 hard_gate_codes。
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
import zero_pronoun_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "zero_pronoun_density_scanner.py"
_ENV = "ZERO_PRONOUN_DENSITY_MODE"


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


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"zp_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 翻译腔（每个 clause 都有显式人称代词）
_TRANSLATIONESE = ("他走到门口，他推开门。他看见屋里黑着，他点亮了灯。"
                   "他坐下来，他叹了口气。他想起了她，他想起了往事。") * 25

# 真中文 paratactic（同句多 clause 大量 ZP）
_PARATAXIS = ("他走到门口，推开。屋里黑着，点灯。"
              "灯亮了，照见桌上一封信。拆开看。手有点抖。又看一遍。") * 25


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_TRANSLATIONESE))
        assert r["mode"] == "off"
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


def test_translationese_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_TRANSLATIONESE))
        # 显式主语过密 → 同句 ZP 率 < 0.60 兜底
        assert r["metrics"]["same_sentence_zp_ratio"] < 0.60
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_parataxis_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_PARATAXIS))
        # 同句 ZP 率高 → PASS
        assert r["metrics"]["same_sentence_zp_ratio"] >= 0.60
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极严苛基线 0.95±0.01·翻译腔 z<-2 必报
        proj = _mk_project(baseline={
            "same_sentence_zp_rate_mean": 0.95,
            "same_sentence_zp_rate_std": 0.01,
        })
        r = mod.scan(_write(_TRANSLATIONESE), project_root=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_lenient_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极宽容基线·一切 PASS
        proj = _mk_project(baseline={
            "same_sentence_zp_rate_mean": 0.30,
            "same_sentence_zp_rate_std": 0.50,
        })
        r = mod.scan(_write(_TRANSLATIONESE), project_root=proj)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_TRANSLATIONESE))
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
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_zero_pronoun_clause_explicit():
    # 有显式主语·非 ZP
    assert mod._is_zero_pronoun_clause("他走到门口") is False
    assert mod._is_zero_pronoun_clause("我看见") is False


def test_zero_pronoun_clause_implicit():
    # 谓语开头·ZP
    assert mod._is_zero_pronoun_clause("走到门口") is True
    assert mod._is_zero_pronoun_clause("点灯") is True


def test_scan_clauses_counts():
    total, zp, st, sz = mod._scan_clauses("他走到门口，点灯。灯亮了，看见。")
    assert total >= 3
    assert sz >= 1  # 至少 1 个同句 ZP


def test_dialogue_para_detection():
    assert mod._is_dialogue_para("“你好。”") is True
    assert mod._is_dialogue_para("叙述句。") is False


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


def test_main_cli_warns_on_translationese():
    r = _run_cli(_write(_TRANSLATIONESE))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
