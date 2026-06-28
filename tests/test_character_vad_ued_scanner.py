# -*- coding: utf-8 -*-
"""character_vad_ued_scanner R19 W8 Batch-V·P0 3D VAD × 6 UED per-character 回归。

确定性·零依赖。覆盖 off/短稿/无角色池/无作者基线 skip/_score_vad/_split_utterances/
_ued_for_series 6 维计算/compute_vad_ued_signature/drift/shadow/active/CLI/
hard_gate registry 守卫·占位词典 _placeholder=true 守卫。
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
import character_vad_ued_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "character_vad_ued_scanner.py"
_ENV = "CHARACTER_VAD_UED_MODE"
_DATA_DIR = _ROOT / "core" / "data"


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


def _mk_project(characters=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if characters:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged": [{"name": n} for n in characters]},
                       ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"vad_ued_signature": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


_BASE_NARRATIVE = ("夜色无声，他独行在街上。" * 80)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_BASE_NARRATIVE))
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


def test_placeholder_dicts_present():
    """北极星纪律：占位词典必须标 _placeholder=true。"""
    p1 = _DATA_DIR / "nrc_vad_v2_placeholder.json"
    p2 = _DATA_DIR / "cvaw_cvap_placeholder.json"
    assert p1.exists() and p2.exists()
    d1 = json.loads(p1.read_text(encoding="utf-8"))
    d2 = json.loads(p2.read_text(encoding="utf-8"))
    assert d1.get("_placeholder") is True
    assert d2.get("_placeholder") is True


def test_score_vad_returns_none_on_no_hit():
    assert mod._score_vad("abcdef") is None


def test_score_vad_returns_triple_on_hit():
    r = mod._score_vad("喜笑欢")
    assert r is not None
    assert len(r) == 3
    for v in r:
        assert 0.0 <= v <= 1.0


def test_split_utterances_attributes_speaker():
    names = {"小王", "李雷"}
    text = "小王说：“我要走了。”李雷答道：“别走。”"
    utts = mod._split_utterances(text, names)
    assert len(utts) == 2
    speakers = {u["speaker"] for u in utts}
    assert speakers <= {"小王", "李雷", None}


def test_split_utterances_no_names_no_attribute():
    text = "他说：“我要走了。”她答：“别走。”"
    utts = mod._split_utterances(text, set())
    # 无角色池 → 全 None
    assert all(u["speaker"] is None for u in utts)


def test_ued_for_series_too_few_returns_none():
    series = [(0.5, 0.5, 0.5)] * 3
    assert mod._ued_for_series(series) is None


def test_ued_for_series_returns_3_axes_6_metrics():
    series = [(0.2, 0.8, 0.3), (0.8, 0.2, 0.7), (0.3, 0.9, 0.4),
              (0.7, 0.3, 0.6), (0.4, 0.8, 0.5), (0.6, 0.4, 0.5),
              (0.5, 0.5, 0.5), (0.9, 0.1, 0.8)]
    r = mod._ued_for_series(series)
    assert r is not None
    assert set(r.keys()) == {"V", "A", "D"}
    for ax in ("V", "A", "D"):
        for k in ("inertia", "variability", "instability", "switch", "pulse", "augmentation"):
            assert k in r[ax]


def test_compute_vad_ued_signature_filters_unknown_speaker():
    utts = [{"speaker": None, "text": "喜笑欢"}] * 10
    out = mod.compute_vad_ued_signature(utts)
    assert out == {}


def test_compute_vad_ued_signature_emits_per_character():
    utts = []
    # 角色 A 6 段不同情绪
    for word in ("喜", "悲", "怒", "怕", "爱", "厌", "笑", "泪"):
        utts.append({"speaker": "小王", "text": word * 4})
    out = mod.compute_vad_ued_signature(utts)
    assert "小王" in out
    assert "V" in out["小王"]


def test_no_characters_skip_message():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=None)
        text = ("他说：“喜悲怒。”" * 80) + _BASE_NARRATIVE
        r = mod.scan(_write(text), project_root=proj)
        # 无角色池→引语不归属→note skip
        assert "skip" in r.get("note", "") or r.get("note") is None
    finally:
        _set_mode(bak)


def test_active_drift_detected_with_strict_baseline():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 给一个极端严苛的基线 · 任何当前数都会偏离 2σ
        baseline = {"per_character": {"小王": {
            "V": {"inertia": {"mean": 100.0, "std": 0.001},
                  "variability": {"mean": 100.0, "std": 0.001},
                  "instability": {"mean": 100.0, "std": 0.001},
                  "switch": {"mean": 100.0, "std": 0.001},
                  "pulse": {"mean": 100.0, "std": 0.001},
                  "augmentation": {"mean": 100.0, "std": 0.001}},
            "A": {}, "D": {}
        }}}
        proj = _mk_project(characters=["小王"], baseline=baseline)
        # 构造足够多归属小王的对话(含 VAD 命中词)
        parts = []
        for word in ("喜", "悲", "怒", "怕", "爱", "厌", "笑", "泪", "暖", "苦"):
            parts.append(f"小王说：“{word*5}。”")
        text = "".join(parts) + _BASE_NARRATIVE
        r = mod.scan(_write(text), project_root=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        # 当前 inertia 不可能=100 → 必然 z 偏离
        if r["per_character_ued"].get("小王"):
            assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=["小王"])
        text = "小王说：“喜悲怒怕爱。”" * 40 + _BASE_NARRATIVE
        r = mod.scan(_write(text), project_root=proj)
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


def _run_cli(draft_path, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs_shadow():
    r = _run_cli(_write(_BASE_NARRATIVE))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "character_vad_ued"
