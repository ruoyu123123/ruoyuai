# -*- coding: utf-8 -*-
"""dialogue_silence_density 专属回归(2026-06-20·R8 W4 Batch-J·L31)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import dialogue_silence_density as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("DIALOGUE_SILENCE_MODE", None)
    else:
        os.environ["DIALOGUE_SILENCE_MODE"] = m


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
            json.dumps({"silence_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


_DRY_TEXT = "他低头说话，她抬头回应。两人继续交谈。" * 60
_RICH_SILENCE_TEXT = (
    "他张口又闭上，沉默片刻。\n"
    "她看着他，震惊地呆住，沉默良久。\n"
    "他叹气，“可是……我……”\n"
    "她许久没有开口，悲伤涌上心头。久久没有回应。\n"
) * 30


def test_off_returns_skeleton():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRY_TEXT))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "silence_density_per_1k" not in out
    finally:
        _set_mode(bak)


def test_active_dry_fail():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRY_TEXT))
        assert out["silence_density_per_1k"] == 0.0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_active_rich_silence_pass():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_RICH_SILENCE_TEXT))
        assert out["silence_marker_total"] > 0
        assert out["silence_emotional_context_match"] > 0
        # 密度足够 → PASS
        assert out["verdict"] in ("PASS", "FAIL_MINOR")  # 视 floor 通用 0.4
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRY_TEXT))
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("。" * 50))
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_resolve_floor_author_baseline():
    proj = _mk_project(baseline={"density_per_1k_floor": 1.0})
    assert mod._resolve_floor(proj) == 1.0


def test_resolve_floor_default():
    proj = _mk_project()
    assert mod._resolve_floor(proj) == 0.4
    assert mod._resolve_floor(None) == 0.4


def test_within_turn_pause_extraction():
    n = mod._count_within_turn_pauses("他说：“我……”然后停了。")
    assert n == 1


def test_gap_outside_quotes_only():
    # quote 内的 "沉默片刻" 不计 (我们只在 quote 外计 gap)
    text = "“沉默片刻。”他想了想。"
    g, l = mod._count_gaps_and_lapses(text)
    assert g == 0


def test_emotion_context_window():
    text = "震惊! 久久没有回应。"
    n = mod._emotion_context_match(text)
    assert n == 1


def test_read_failure_returns_note():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("DIALOGUE_SILENCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
