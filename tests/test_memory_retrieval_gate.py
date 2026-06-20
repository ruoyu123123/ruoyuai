# -*- coding: utf-8 -*-
"""R7 W2 Batch-E·memory_retrieval_gate 脚手架单测·确定性·零依赖。

覆盖：
  · env 解析 / 默认值 / 非法回落
  · status() 探针
  · estimate_tokens 数值边界
  · filter_candidates: off 路径 / 预算未超阈 / shadow 不应用 / active 应用 top-k / score_fn 异常降级
  · GateDecision.to_dict
  · CLI: --status / --filter
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

import memory_retrieval_gate as mod  # noqa: E402


_ENV_KEYS = ("MEMORY_RETRIEVAL_GATE_MODE",
             "MEMORY_RETRIEVAL_GATE_TOPK",
             "MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS")


def _clear_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


# ── env / config ───────────────────────────────────────────────────────
def test_default_mode_off():
    _clear_env()
    assert mod.get_mode() == "off"


def test_invalid_mode_falls_back_off():
    _clear_env()
    os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = "bogus"
    try:
        assert mod.get_mode() == "off"
    finally:
        _clear_env()


def test_shadow_active_recognized():
    _clear_env()
    for m in ("shadow", "active", "SHADOW", "Active"):
        os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = m
        assert mod.get_mode() == m.strip().lower()
    _clear_env()


def test_topk_default_and_clamp():
    _clear_env()
    assert mod.get_topk() == mod._DEFAULT_TOPK
    os.environ["MEMORY_RETRIEVAL_GATE_TOPK"] = "100"
    assert mod.get_topk() == 64   # 钳到上界
    os.environ["MEMORY_RETRIEVAL_GATE_TOPK"] = "0"
    assert mod.get_topk() == 1    # 钳到下界
    os.environ["MEMORY_RETRIEVAL_GATE_TOPK"] = "notnum"
    assert mod.get_topk() == mod._DEFAULT_TOPK
    _clear_env()


def test_budget_default_and_clamp():
    _clear_env()
    assert mod.get_budget_tokens() == mod._DEFAULT_BUDGET
    os.environ["MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"] = "500"   # 太小
    assert mod.get_budget_tokens() == 1000
    os.environ["MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"] = "garbage"
    assert mod.get_budget_tokens() == mod._DEFAULT_BUDGET
    _clear_env()


# ── status ─────────────────────────────────────────────────────────────
def test_status_returns_full_config():
    _clear_env()
    s = mod.status()
    assert s["mode"] == "off"
    assert s["topk"] == mod._DEFAULT_TOPK
    assert s["budget_tokens"] == mod._DEFAULT_BUDGET
    assert "env" in s
    assert all(k in s["env"] for k in _ENV_KEYS)
    assert "_scaffold_note" in s


# ── estimate_tokens ────────────────────────────────────────────────────
def test_estimate_tokens_empty():
    assert mod.estimate_tokens("") == 0
    assert mod.estimate_tokens(None) == 0


def test_estimate_tokens_cjk_one_per_char():
    assert mod.estimate_tokens("你好世界") == 4


def test_estimate_tokens_ascii_four_per_token():
    assert mod.estimate_tokens("abcdefgh") == 2   # 8 // 4


def test_estimate_tokens_mixed():
    assert mod.estimate_tokens("你好abcd") == 2 + 1   # 2 CJK + 1 token


# ── filter_candidates ─────────────────────────────────────────────────
def test_filter_off_mode_fallback():
    _clear_env()
    cands = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]
    d = mod.filter_candidates(cands)
    assert d.mode == "off"
    assert d.applied is False
    assert d.fallback_to_static is True
    assert d.kept_candidates == cands
    assert d.reason == "mode_off"


def test_filter_empty_candidates():
    _clear_env()
    d = mod.filter_candidates([])
    assert d.reason == "no_candidates"
    assert d.fallback_to_static is True


def test_filter_invalid_candidates_type():
    _clear_env()
    d = mod.filter_candidates(None)
    assert d.reason == "no_candidates"


def test_filter_under_budget_no_apply():
    """总 token < budget → 不应用·全保留。"""
    _clear_env()
    os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = "active"
    try:
        cands = [{"id": str(i), "text": "短"} for i in range(5)]
        d = mod.filter_candidates(cands)
        assert d.applied is False
        assert d.reason == "under_budget"
        assert d.fallback_to_static is True
    finally:
        _clear_env()


def test_filter_active_over_budget_applies_topk():
    _clear_env()
    os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = "active"
    os.environ["MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"] = "1000"
    os.environ["MEMORY_RETRIEVAL_GATE_TOPK"] = "2"
    try:
        big = "你" * 500
        cands = [{"id": str(i), "text": big} for i in range(5)]   # 2500 tokens > 1000
        scores = lambda L, anchor: [(c, float(i)) for i, c in enumerate(L)]
        d = mod.filter_candidates(cands, scene_anchor="hello",
                                   _score_fn=scores)
        assert d.applied is True
        assert d.reason == "topk_applied"
        assert d.fallback_to_static is False
        assert len(d.kept_candidates) == 2
        # 最高分两个 = id "4" 和 "3"
        kept_ids = {c["id"] for c in d.kept_candidates}
        assert kept_ids == {"3", "4"}
        assert len(d.dropped_candidates) == 3
    finally:
        _clear_env()


def test_filter_shadow_no_apply():
    _clear_env()
    os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = "shadow"
    os.environ["MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"] = "1000"
    try:
        big = "你" * 500
        cands = [{"id": str(i), "text": big} for i in range(5)]
        d = mod.filter_candidates(cands)
        assert d.applied is False
        assert d.reason == "shadow_no_apply"
        assert d.fallback_to_static is True
        assert d.kept_candidates == cands
    finally:
        _clear_env()


def test_filter_score_fn_exception_degrades():
    _clear_env()
    os.environ["MEMORY_RETRIEVAL_GATE_MODE"] = "active"
    os.environ["MEMORY_RETRIEVAL_GATE_BUDGET_TOKENS"] = "1000"
    try:
        big = "你" * 500
        cands = [{"id": str(i), "text": big} for i in range(5)]

        def boom(L, anchor):
            raise RuntimeError("score crashed")
        d = mod.filter_candidates(cands, _score_fn=boom)
        assert d.applied is False
        assert d.fallback_to_static is True
        assert "score_fn_failed" in d.reason
    finally:
        _clear_env()


# ── GateDecision.to_dict ──────────────────────────────────────────────
def test_decision_to_dict_serializable():
    _clear_env()
    d = mod.GateDecision(mode="off", applied=False, reason="x",
                         kept_candidates=[{"id": "a", "text": "yy"}, "raw"],
                         dropped_candidates=[{"id": "b"}],
                         fallback_to_static=True)
    out = d.to_dict()
    json.dumps(out)   # 必须可序列化
    assert out["kept"] == ["a", "raw"]
    assert out["dropped"] == ["b"]
    assert out["fallback_to_static"] is True


# ── CLI ───────────────────────────────────────────────────────────────
def _run_cli(args, env=None):
    e = dict(os.environ)
    for k in _ENV_KEYS:
        e.pop(k, None)
    if env:
        e.update(env)
    e["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "memory_retrieval_gate.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=e)


def test_cli_status():
    r = _run_cli(["--status"])
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["mode"] == "off"


def test_cli_filter_pass_through_off():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cands.json"
        p.write_text(json.dumps([{"id": "a", "text": "x"}],
                                ensure_ascii=False), encoding="utf-8")
        r = _run_cli(["--filter", str(p), "--scene-anchor", "anchor"])
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["mode"] == "off"
        assert data["applied"] is False
        assert data["fallback_to_static"] is True


def test_cli_filter_bad_json_returns_error():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cands.json"
        p.write_text("{ not json", encoding="utf-8")
        r = _run_cli(["--filter", str(p)])
        assert r.returncode == 2


def test_cli_filter_not_list_returns_error():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cands.json"
        p.write_text(json.dumps({"x": 1}), encoding="utf-8")
        r = _run_cli(["--filter", str(p)])
        assert r.returncode == 2
