# -*- coding: utf-8 -*-
"""excess_vocab_corpus_zscan R18 W7 Batch-S·P0 LLM 偏置词 z-test 回归

确定性·零依赖。覆盖 off/无词典/短稿/读取失败/hit/active/shadow/_mode/作者档读取/
per_million/mean_std/CLI 退出码。
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
import excess_vocab_corpus_zscan as mod  # noqa: E402

_TARGET = _SCRIPTS / "excess_vocab_corpus_zscan.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("EXCESS_VOCAB_CORPUS_ZSCAN_MODE", None)
    else:
        os.environ["EXCESS_VOCAB_CORPUS_ZSCAN_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_author(slow_update=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if slow_update is not None:
        (db / "作者风格.json").write_text(
            json.dumps({"slow_update": slow_update}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 重度 LLM 偏置词草稿（顿时/此刻/与此同时/然而 高频 → 应触发 hit）
_LLM_LIKE = (
    "他顿时停下脚步，此刻天色已晚，然而风声渐起。"
    "与此同时，远处传来声响，此刻他顿时警觉。"
    "顿时间他似乎听见了什么，然而仿佛又什么都没有。"
    "此刻他的心境淡淡的，缓缓地深吸一口气。"
) * 30

# 中性正常草稿（基本不命中）
_NEUTRAL = "他走进酒馆点了酒。窗外微风。桌上一壶酒一碟花生。" * 80


def test_off_returns_skeleton():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_LLM_LIKE))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_llm_like_draft_active_fail():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LLM_LIKE))
        assert out["hit_count"] >= 1
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"][0]["code"] == "EXCESS_VOCAB_SIGNATURE_HIT"
    finally:
        _set_mode(bak)


def test_neutral_draft_no_hit():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NEUTRAL))
        assert out["hit_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_report():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LLM_LIKE))
        assert out["violations"] == []
        assert out["warning"] is None
        # 但仍记录 hit_count
        assert out["hit_count"] >= 1
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("顿时此刻。" * 10))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_author_freq_high_suppresses_hit():
    """作者档高频该 token → z_author 高 → 不报"""
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("active")
        # 让作者档把 "顿时" 频次拉到极高（远超 μ+1σ → suppress）
        proj = _mk_project_with_author(slow_update={
            "author_vocab_freq_ecdf": {"顿时": 50000.0, "此刻": 50000.0,
                                        "与此同时": 50000.0, "然而": 50000.0,
                                        "仿佛": 50000.0, "似乎": 50000.0,
                                        "深吸一口气": 50000.0, "淡淡": 50000.0,
                                        "缓缓": 50000.0}})
        out = mod.scan(_write(_LLM_LIKE), proj)
        # 这些高频 token 不被报（被作者档保护）
        for h in out.get("hit_samples", []):
            assert h["token"] not in ("顿时", "此刻", "与此同时", "然而")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2


def test_per_million_zero_cjk():
    assert mod._per_million(10, 0) == 0.0


def test_per_million_basic():
    assert mod._per_million(5, 1_000_000) == 5.0


def test_mean_std_empty():
    mu, sigma = mod._mean_std([])
    assert mu == 0.0 and sigma == 0.0


def test_mean_std_basic():
    mu, sigma = mod._mean_std([1, 2, 3, 4, 5])
    assert abs(mu - 3.0) < 1e-9


def test_load_freq_existence():
    freq, ph = mod._load_freq(mod._LLM_FREQ_PATH)
    assert freq   # 词典必须有内容
    assert ph is True   # 占位词典 _placeholder=true


def test_lexicons_contain_human_freq():
    freq, _ = mod._load_freq(mod._HUMAN_FREQ_PATH)
    assert "顿时" in freq


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "EXCESS_VOCAB_CORPUS_ZSCAN_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    r = _run_cli(_write(_LLM_LIKE))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    r = _run_cli(_write(_NEUTRAL))
    assert r.returncode == 0, r.stderr
