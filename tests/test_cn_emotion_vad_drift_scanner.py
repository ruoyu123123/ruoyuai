# -*- coding: utf-8 -*-
"""cn_emotion_vad_drift_scanner R24 W12 Batch-KK · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cn_emotion_vad_drift_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CN_EMOTION_VAD_MODE", None)
    else:
        os.environ["CN_EMOTION_VAD_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_baseline(cultural_density=2.0, clear_amb_ratio=2.0):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {
        "cn_emotion_baseline": {
            "cultural_density": cultural_density,
            "clear_ambivalent_ratio": clear_amb_ratio,
        }
    }
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False),
                                       encoding="utf-8")
    return proj


_FILLER = "他静静地走在路上，望着前方未明的去路，思绪散落如雪。\n\n" * 25


def test_off_returns_skeleton():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_FILLER))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_no_emotion_words_emits_info():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 全是中性叙述 · 无情感词
        text = "他走在路上，路是直的，天是蓝的，云是白的。" * 100
        out = mod.scan(_write(text))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CN_EMOTION_NO_EMOTION_WORDS" in codes
    finally:
        _set_mode(bak)


def test_anglo_drift_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 仅用 dv 同向词（悲愤+窘迫+惆怅 在 CVAW vs NRC 都是 +0.20）平均突破阈值
        # 避免不同词差正负相消（占位字典本身 design 选了多方向词以求接近真实）
        text = ("他悲愤地看着窘迫的对方，只剩惆怅。\n\n") * 80
        out = mod.scan(_write(text))
        codes = {v["code"] for v in out.get("violations", [])}
        # 至少有命中 drift_words
        assert out["signal_a_anglo_drift"]["drift_words"]
        # 占位词典设的差是显著的 → ANGLO_DRIFT 应触发
        assert "CN_EMOTION_ANGLO_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_cultural_underuse_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 拉高基线到 200 → 草稿密度需<120 / 千 才触发 UNDERUSE
        proj = _mk_project_with_baseline(cultural_density=200.0)
        # 极少量文化特有词（仅 1 处 含蓄）+ 大量非情感铺垫文
        text = ("天空很蓝，云朵很白，他往前走着，景色不错，一切都很普通。\n\n"
                "他抬头看了一眼，又低下头，继续往前走。\n\n") * 80
        text += "他含蓄地点头。\n\n"  # 仅 1 处
        out = mod.scan(_write(text), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        # 触发 UNDERUSE（密度<60% 基线）
        assert "CN_EMOTION_CULTURAL_UNDERUSE" in codes
    finally:
        _set_mode(bak)


def test_binary_polarization_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 拉低基线 → 草稿 clear/ambivalent 比例显著
        proj = _mk_project_with_baseline(clear_amb_ratio=0.5)
        text = ("他大笑起来，又狂怒地暴怒起来，最后嚎啕痛哭。\n\n"
                "她狂喜，他狂怒。\n\n") * 60
        out = mod.scan(_write(text), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CN_EMOTION_BINARY_POLARIZATION" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("shadow")
        text = ("他悲愤地看着窘迫的对方，雀跃的心情消失了，只剩惆怅。\n\n") * 30
        out = mod.scan(_write(text))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_placeholder_lexicons():
    assert mod._CVAW_V2["_placeholder"] is True
    assert mod._NRC_VAD_CN["_placeholder"] is True
    assert mod._CULTURAL_SPECIFIC["_placeholder"] is True


def test_load_baseline_default():
    base = mod._load_baseline(None)
    assert base["_source"] == "default"
    assert base["cultural_density"] == mod.DEFAULT_BASELINE_CULTURAL_DENSITY


def test_load_baseline_from_project():
    proj = _mk_project_with_baseline(cultural_density=5.0, clear_amb_ratio=3.0)
    base = mod._load_baseline(proj)
    assert base["cultural_density"] == 5.0
    assert base["clear_ambivalent_ratio"] == 3.0


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_codes_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("CN_EMOTION_ANGLO_DRIFT", "CN_EMOTION_CULTURAL_UNDERUSE",
              "CN_EMOTION_BINARY_POLARIZATION",
              "CN_EMOTION_NO_EMOTION_WORDS", "CN_EMOTION_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("cn_emotion_vad_drift_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_signal_a_no_emotion_returns_zero():
    """无 CVAW 命中 → avg_dv/avg_da=0"""
    out = mod._signal_a_drift("天空很蓝，云朵飘飘。")
    assert out["avg_dv"] == 0.0
    assert out["avg_da"] == 0.0
    assert out["drift_words"] == []


def test_audit_hub_integrates_scanner():
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "cn_emotion_vad_drift_scanner" in src
    assert "CN_EMOTION_ANGLO_DRIFT" in src
