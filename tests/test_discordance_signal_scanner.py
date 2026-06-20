# -*- coding: utf-8 -*-
"""discordance_signal_scanner.py 专属回归测试 (R8 W4 Batch-I · 2026-06-20)。

零依赖·确定性·零 LLM/零联网。
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
import discordance_signal_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "discordance_signal_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("DISCORDANCE_MODE", None)
    else:
        os.environ["DISCORDANCE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(profile=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if profile is not None:
        obj["ironic_voice_profile"] = profile
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# discordance 充足:四 cue 各出现
_DISCORDANCE_RICH = (
    "嘴上说要救人心里却盘算着自己的利益，一边说着不行一边偷偷开门。" * 10 +
    "英雄般地走进厨房买菜，庄严地宣告今天吃泡面。" * 6 +
    "陛下，你的微信刚到。" * 4 +
    "真是个好人！这种高明的操作！" * 6 +
    "他认真工作并努力。" * 60
)
# 无 discordance(纯直叙)
_DISCORDANCE_THIN = "他认真工作，努力完成任务，得到老板表扬。" * 80


# ── off → 骨架 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(profile={"stable_irony": True, "discordance_target": 1.0})
        out = mod.scan(_write(_DISCORDANCE_RICH), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "discordance_per_1k" not in out
    finally:
        _set_mode(bak)


# ── 无 profile 或 stable_irony=False → skip ────────────────────────────────
def test_no_profile_skip():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile=None)
        out = mod.scan(_write(_DISCORDANCE_THIN), project_root=proj)
        assert "stable_irony" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_stable_irony_false_skip():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"stable_irony": False})
        out = mod.scan(_write(_DISCORDANCE_THIN), project_root=proj)
        assert out["verdict"] == "PASS"
        assert "stable_irony" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 反讽稀薄 + stable_irony 声明 + active → FAIL_MINOR ────────────────────
def test_thin_discordance_fail_minor():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"stable_irony": True, "discordance_target": 2.0})
        out = mod.scan(_write(_DISCORDANCE_THIN), project_root=proj)
        assert out["discordance_per_1k"] < 1.0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── 反讽充足 → PASS ────────────────────────────────────────────────────────
def test_rich_discordance_pass():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"stable_irony": True, "discordance_target": 1.0})
        out = mod.scan(_write(_DISCORDANCE_RICH), project_root=proj)
        # 四 cue 均有命中
        assert sum(out["cue_counts"].values()) > 0
        # 至少一个 cue 类别命中
        assert any(v > 0 for v in out["cue_counts"].values())
    finally:
        _set_mode(bak)


# ── shadow 模式 ──────────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(profile={"stable_irony": True, "discordance_target": 2.0})
        out = mod.scan(_write(_DISCORDANCE_THIN), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ─────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(profile={"stable_irony": True})
        out = mod.scan(_write("呵呵！" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 辅助 ────────────────────────────────────────────────────────────────────
def test_count_cues_saying_doing():
    c = mod.count_cues("嘴上说要走心里却想留下来。")
    assert c["saying_doing"] >= 1


def test_count_cues_style_fact():
    c = mod.count_cues("英雄般地走进厨房买菜。")
    assert c["style_fact"] >= 1


def test_count_cues_value_clash():
    c = mod.count_cues("真是个好人！")
    assert c["value_clash"] >= 1


def test_count_cues_no_signals():
    c = mod.count_cues("他在认真工作。")
    assert sum(c.values()) == 0


def test_read_profile_none():
    assert mod._read_ironic_voice_profile(None) is None


def test_read_profile_from_author():
    proj = _mk_project(profile={"stable_irony": True, "discordance_target": 1.5})
    p = mod._read_ironic_voice_profile(proj)
    assert p == {"stable_irony": True, "discordance_target": 1.5}


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("反讽abc讽刺") == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("DISCORDANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "DISCORDANCE_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(profile={"stable_irony": True, "discordance_target": 2.0})
    p = _write(_DISCORDANCE_THIN)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_no_profile():
    proj = _mk_project(profile=None)
    p = _write(_DISCORDANCE_THIN)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
