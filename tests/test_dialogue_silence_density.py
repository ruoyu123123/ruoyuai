# -*- coding: utf-8 -*-
"""dialogue_silence_density 专属回归(2026-06-20·R8 W4 Batch-J·L31)。"""
import json
import os
import sys
import tempfile
import types
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


# ══════════════════════════════════════════════════════════════════════════
# 🔴 VAD 模型接线回归：silence_emotional_context_match 优先用 VAD 窗口强度
# (arousal H/VH bin 或 valence 偏离中性)，模型未启用/不可用 → 回退固定情绪词表（零回归）。
# ══════════════════════════════════════════════════════════════════════════
def test_emotion_context_match_model_hit(monkeypatch):
    """RUOYU_NN_VAD=1 + 假模型高强度 → 窗口命中(即便窗口内无 EMOTION_CONTEXT 关键词)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts: [
            {"valence": 0.5, "arousal": 0.9, "dominance": None, "source": "model"}
            for _ in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    # "沉默片刻"触发 GAP_MARKERS·窗口内无 EMOTION_CONTEXT 词表任何关键词·旧词典路径必为 0
    text = "他沉默片刻，然后转身离开，走进厨房，拿起水杯喝了一口水。"
    assert mod.EMOTION_CONTEXT.search(text) is None  # 前置确认：窗口内确无关键词
    n = mod._emotion_context_match(text)
    assert n == 1


def test_emotion_context_match_model_unavailable_matches_lexicon_fallback(monkeypatch):
    """RUOYU_NN_VAD=1 但模型返回 None(不可用) → 与门控完全关闭时命中数逐一致（零回归）。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    n_model_unavailable = mod._emotion_context_match(_RICH_SILENCE_TEXT)

    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    n_off = mod._emotion_context_match(_RICH_SILENCE_TEXT)
    assert n_model_unavailable == n_off
    assert n_off > 0


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
