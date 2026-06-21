# -*- coding: utf-8 -*-
"""rasa_causal_chain_scanner · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import rasa_causal_chain_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "rasa_causal_chain_scanner.py"
_ENV = "RASA_CAUSAL_CHAIN_MODE"


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


def _mk_project(profile=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return proj


# 三段闭环全在 · vibhāva + sthāyī + anubhāva 都齐
_PARA_FULL = (
    "他看见对方突然伸手，怒火腾起。他握紧拳，咬牙后退一步。"
)
# 【A】sthāyī 强但 vibhāva=0（情感突生）
_PARA_NO_V = "怒火盛怒戾气，他咆哮起来，握紧拳头。"
# 【B】sthāyī strong 但 anubhāva=0（未落地，只描写内心）
_PARA_NO_A = "他看见对方动作，怒火腾起，盛怒难抑，戾气在胸中盘旋。"
# 【C】vibhāva 充分但 sthāyī 缺失
_PARA_MISMATCH = "他突然看见对方扑来，传来响起的声音，瞥见身后的影子。"

_PARA_FILLER = "他走在长街上，风很凉。"


def _build_draft(seed_para: str, repeats: int = 5,
                 filler_para: str = _PARA_FILLER,
                 filler_repeats: int = 120) -> str:
    parts = [seed_para] * repeats + [filler_para] * filler_repeats
    return "\n\n".join(parts)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_build_draft(_PARA_FULL)), _mk_project())
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("短"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_full_chain_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_draft(_PARA_FULL)), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_OK in codes
        assert mod.ISSUE_CODE_NO_ANUBHAVA not in codes
    finally:
        _set_mode(bak)


def test_break_a_no_vibhava():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_draft(_PARA_NO_V)), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_NO_VIBHAVA in codes
    finally:
        _set_mode(bak)


def test_break_b_no_anubhava():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_draft(_PARA_NO_A)), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_NO_ANUBHAVA in codes
    finally:
        _set_mode(bak)


def test_break_c_mismatch():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_build_draft(_PARA_MISMATCH)), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_MISMATCH in codes
    finally:
        _set_mode(bak)


def test_tolerance_overrides():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 作者档容忍 5 个 B → 应不报 B
        proj = _mk_project({"causal_chain_tolerance": {"A": 0, "B": 10, "C": 0}})
        out = mod.scan(_write(_build_draft(_PARA_NO_A)), proj)
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_NO_ANUBHAVA not in codes
        assert out["tolerance"]["author_owned"] is True
    finally:
        _set_mode(bak)


def test_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_build_draft(_PARA_NO_V)), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_detect_sthayi():
    label, strength = mod._detect_sthayi("怒火盛怒戾气，他怒喝")
    assert label == "raudra"
    assert strength == "strong"
    label2, strength2 = mod._detect_sthayi("无情感词")
    assert label2 is None and strength2 is None


def test_lexicons_placeholder():
    assert mod._VIBHAVA_LEX["_placeholder"] is True
    assert mod._STHAYI_LEX["_placeholder"] is True
    assert mod._ANUBHAVA_LEX["_placeholder"] is True


def test_load_tolerance_default():
    tol = mod._load_tolerance(None)
    assert tol["author_owned"] is False
    assert tol["A"] == 0


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_NO_VIBHAVA, mod.ISSUE_CODE_NO_ANUBHAVA,
              mod.ISSUE_CODE_MISMATCH, mod.ISSUE_CODE_OK):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write(_build_draft(_PARA_FULL))
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "rasa_causal_chain_scanner"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    text = "正文\n---CHANGES---\n{...}"
    out = mod._strip_changes(text)
    assert "CHANGES" not in out
