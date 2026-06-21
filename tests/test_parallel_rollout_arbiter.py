# -*- coding: utf-8 -*-
"""parallel_rollout_arbiter R19 W8 Batch-Y·P2 K=2 listwise rank 回归测试。

确定性·零依赖。覆盖 off/<2 candidate skip/正常 rank/winner_index/平局
degraded/active vs shadow/CLI/hard_gate 守卫。
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
import parallel_rollout_arbiter as mod  # noqa: E402

_TARGET = _SCRIPTS / "parallel_rollout_arbiter.py"
_ENV = "PARALLEL_ROLLOUT_ARBITER_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_candidate(draft_text, judge_data):
    d = Path(tempfile.mkdtemp())
    dp = d / "draft.txt"
    dp.write_text(draft_text, encoding="utf-8")
    jp = d / "judge.json"
    jp.write_text(json.dumps(judge_data, ensure_ascii=False), encoding="utf-8")
    return {"draft_path": str(dp), "judge_report_path": str(jp)}


def test_off_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.arbitrate([_mk_candidate("一" * 1000, {})])
        assert r["mode"] == "off"
        assert r["winner_index"] is None
    finally:
        _set_mode(bak)


def test_lt_2_candidates_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.arbitrate([_mk_candidate("一" * 1000, {})])
        assert "candidates<2" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_normal_rank_winner():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        good = _mk_candidate("一二三四" * 1500, {
            "scanners": [{"verdict": "PASS"}, {"verdict": "PASS"}],
            "verdict": "PASS"})
        bad = _mk_candidate("一" * 100, {
            "scanners": [{"verdict": "FAIL_HARD"}],
            "verdict": "FAIL_HARD"})
        r = mod.arbitrate([bad, good], target_band=(2000, 8000))
        # good 应胜
        assert r["winner_index"] == 1
        assert r["candidates"][0]["rank_score"] >= r["candidates"][1]["rank_score"]
    finally:
        _set_mode(bak)


def test_tie_degraded_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        c1 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
        c2 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
        r = mod.arbitrate([c1, c2])
        # 完全一样 → top-1 vs top-2 = 0 < 0.02
        codes = [v["code"] for v in r["violations"]]
        assert "PARALLEL_ROLLOUT_ARBITER_DEGRADED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        c1 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
        c2 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
        r = mod.arbitrate([c1, c2])
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_score_helper_band_fit_inside():
    assert mod._length_band_fit(3500, (3000, 4000)) == 1.0


def test_score_helper_band_fit_outside():
    v = mod._length_band_fit(100, (3000, 4000))
    assert 0.0 <= v < 1.0


def test_verdict_penalty():
    assert mod._verdict_penalty({"verdict": "PASS"}) == 1.0
    assert mod._verdict_penalty({"verdict": "FAIL_MINOR"}) == 0.5
    assert mod._verdict_penalty({"verdict": "FAIL_HARD"}) == 0.0


def test_scanner_pass_rate_dict_form():
    judge = {"scanner_results": {"a": {"verdict": "PASS"}, "b": {"verdict": "FAIL_MINOR"}}}
    r = mod._scanner_pass_rate(judge)
    assert 0.0 <= r <= 1.0


def test_lexical_diversity_empty():
    assert mod._lexical_diversity("") == 0.0


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


def test_main_cli_runs_off():
    d = Path(tempfile.mkdtemp())
    c1 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
    c2 = _mk_candidate("一" * 1000, {"scanners": [{"verdict": "PASS"}]})
    sp1 = d / "c1.json"; sp1.write_text(json.dumps(c1), encoding="utf-8")
    sp2 = d / "c2.json"; sp2.write_text(json.dumps(c2), encoding="utf-8")
    env = {**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [sys.executable, str(_TARGET), "--candidates", f"{sp1},{sp2}"],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "parallel_rollout_arbiter"
