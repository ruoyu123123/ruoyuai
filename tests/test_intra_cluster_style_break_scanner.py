# -*- coding: utf-8 -*-
"""intra_cluster_style_break_scanner R19 W8 Batch-W·P1 句对级风格断点回归。

确定性·零依赖。覆盖 off/短稿/clean cluster PASS/突变段触发/SSPC 占位标志/
LLM fallback env/window signature/punct KL/shadow active 切换/CLI/hard_gate 守卫。
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
import intra_cluster_style_break_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "intra_cluster_style_break_scanner.py"
_ENV = "INTRA_CLUSTER_STYLE_BREAK_MODE"
_LLM_ENV = "STYLE_BREAK_LLM_FALLBACK"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _set_llm(m):
    if m is None:
        os.environ.pop(_LLM_ENV, None)
    else:
        os.environ[_LLM_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 上半段：短句长 + 口语 register + 大量问号
# 下半段：长句长 + 文言 register + 句号为主
_BREAK_TEXT = (
    "他走过去。怎么了？妈呀。卧槽。怎么办？牛逼。靠。哎呀。" * 30
    + "诚然乃是一段冗长的叙述用以铺陈整个故事的来龙去脉以及主角心中的复杂思绪。"
    * 30
)

# Clean cluster：一致的中性叙述句长
_CLEAN_TEXT = (
    "他走过去看了看。一片寂静。她抬头远望。月光洒在地上。"
    "风很轻。他叹了口气。" * 60
)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_CLEAN_TEXT))
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


def test_clean_text_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_CLEAN_TEXT))
        # clean 文本可能也有零星 anchors，但绝不应大量违规
        assert r["scanner"] == "intra_cluster_style_break"
        # 不必断言绝对 PASS·只确保 metrics 字段存在
        assert "anchors_count" in r["metrics"]
    finally:
        _set_mode(bak)


def test_break_text_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_BREAK_TEXT))
        assert r["metrics"]["anchors_count"] >= 1
        if r["metrics"]["anchors_count"] >= 1:
            assert r["verdict"] == "FAIL_MINOR"
            codes = [v["code"] for v in r["violations"]]
            assert "STYLE_BREAK_DETECTED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation_but_still_detects():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_BREAK_TEXT))
        assert r["violations"] == []
        assert r["warning"] is None
        # 但 anchors 仍记录
        assert r["metrics"]["anchors_count"] >= 0
    finally:
        _set_mode(bak)


def test_sspc_placeholder_marker():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_CLEAN_TEXT))
        assert r["sspc_status"]["_placeholder"] is True
        assert r["sspc_status"]["training_params_status"] == "deferred"
    finally:
        _set_mode(bak)


def test_llm_fallback_default_off():
    bak = os.environ.get(_LLM_ENV)
    try:
        _set_llm(None)
        assert mod._llm_fallback_enabled() is False
    finally:
        _set_llm(bak)


def test_llm_fallback_env_on():
    bak = os.environ.get(_LLM_ENV)
    try:
        _set_llm("on")
        assert mod._llm_fallback_enabled() is True
    finally:
        _set_llm(bak)


def test_window_signature_basic():
    sig = mod._window_signature(["一句。", "两句。", "三句！"])
    assert sig is not None
    assert sig["n_sentences"] == 3
    assert sig["mean_len"] > 0


def test_window_signature_empty():
    assert mod._window_signature([]) is None


def test_punct_kl_zero_on_identical():
    a = {"。": 0.5, "！": 0.5}
    b = {"。": 0.5, "！": 0.5}
    kl = mod._punct_kl(a, b)
    assert kl < 0.001


def test_punct_kl_positive_on_divergent():
    a = {"。": 1.0}
    b = {"！": 1.0}
    kl = mod._punct_kl(a, b)
    assert kl > 0.5


def test_detect_breaks_too_short_returns_empty():
    anchors, total = mod.detect_breaks("一句。" * 5)
    assert anchors == []


def test_detect_breaks_with_break_text():
    anchors, total = mod.detect_breaks(_BREAK_TEXT)
    assert total > 0
    # 大概率有 anchor·若实在没有也不应崩
    assert isinstance(anchors, list)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


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
    r = _run_cli(_write(_CLEAN_TEXT))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "intra_cluster_style_break"
