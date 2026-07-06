# -*- coding: utf-8 -*-
"""parrhesia_density_scanner · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import parrhesia_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "parrhesia_density_scanner.py"
_ENV = "PARRHESIA_DENSITY_MODE"


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


# 三信号齐 hit 段（年轻人 vs 长老 + 引号 truth-claim + 风险姿态）
_PARRHESIA_HIT_PARA = (
    "他望着堂上的长老，被那些「年轻人不知天高地厚」的喝斥包围。\n"
    "他上前一步，迎着长老的目光，昂首拱手：\n"
    "“恕我直言，长老此言差矣。”\n"
    "他站起来，直视对方，不卑不亢。"
)

# 拼装大于 500 CJK 的草稿 · 重复段确保密度信号
_DRAFT_WITH_HITS = (
    "前情铺垫，平淡叙述，无三信号同段。" * 20
    + "\n\n"
    + _PARRHESIA_HIT_PARA
    + "\n\n"
    + "无关段落，继续叙述。" * 20
    + "\n\n"
    + _PARRHESIA_HIT_PARA
    + "\n\n"
    + "尾声铺垫。" * 30
)

_DRAFT_NO_HITS = "前情铺垫，平淡叙述，无三信号同段。" * 60


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_WITH_HITS), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "parrhesia_hits" not in out
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


def test_hit_detected():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_WITH_HITS), _mk_project())
        assert out["parrhesia_hits"] >= 2
        assert out["parrhesia_hits_per_10k_cjk"] > 0
    finally:
        _set_mode(bak)


def test_no_hit_when_signals_missing():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_NO_HITS), _mk_project())
        assert out["parrhesia_hits"] == 0
    finally:
        _set_mode(bak)


def test_overflow_when_density_high():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 作者档极低均值 · 制造 overflow
        proj = _mk_project({"parrhesia_signature": {
            "density_baseline": {"mean": 0.0, "sigma": 0.1}}})
        out = mod.scan(_write(_DRAFT_WITH_HITS), proj)
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_OVERFLOW in codes
        assert out["baseline"]["author_owned"] is True
    finally:
        _set_mode(bak)


def test_thin_when_no_hits_with_positive_baseline():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project({"parrhesia_signature": {
            "density_baseline": {"mean": 5.0, "sigma": 0.5}}})
        out = mod.scan(_write(_DRAFT_NO_HITS), proj)
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_THIN in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_WITH_HITS), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_detect_parrhesia_requires_all_three():
    # 仅 A+B 无 C
    para_ab = "“长老此言差矣，恕我直言。”年轻人继续说。"
    rep = mod._detect_parrhesia(para_ab)
    assert rep["hit"] is False
    # 全三齐
    rep2 = mod._detect_parrhesia(_PARRHESIA_HIT_PARA)
    assert rep2["hit"] is True


def test_quoted_paragraph_check():
    assert mod._is_quoted_paragraph("“话”") is True
    assert mod._is_quoted_paragraph("「话」") is True
    assert mod._is_quoted_paragraph("无引号") is False


def test_baseline_default_fallback():
    mean, sigma, owned = mod._load_baseline(None)
    assert mean == mod.PARRHESIA_BASELINE_MEAN_DEFAULT
    assert sigma == mod.PARRHESIA_BASELINE_SIGMA_DEFAULT
    assert owned is False


def test_lexicons_placeholder():
    assert mod._LEXICONS["_placeholder"] is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_OVERFLOW, mod.ISSUE_CODE_THIN, mod.ISSUE_CODE_OK):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write(_DRAFT_WITH_HITS)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "parrhesia_density_scanner"


def test_strip_changes_marker():
    text = "正文。\n---CHANGES---\n{...}"
    out = mod._strip_changes(text)
    assert "CHANGES" not in out


def test_read_failure_returns_note():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── zero_shot truth_claim 语义补召回（2026-07-04 军火库 3.3）─────────────
import types  # noqa: E402


def test_truth_claim_semantic_true(monkeypatch):
    fake = types.SimpleNamespace(
        classify=lambda text, protos, floor=0.5: {"label": "truth_claim", "score": 0.8})
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    assert mod._truth_claim_semantic("我把话挑明了") is True


def test_truth_claim_semantic_other_or_none(monkeypatch):
    fake = types.SimpleNamespace(
        classify=lambda text, protos, floor=0.5: {"label": "other", "score": 0.7})
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    assert mod._truth_claim_semantic("外面下雨了") is False
    fake2 = types.SimpleNamespace(classify=lambda text, protos, floor=0.5: None)
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake2)
    assert mod._truth_claim_semantic("我把话挑明了") is False


def test_detect_parrhesia_b_semantic_union(monkeypatch):
    """引号段·lexicon 漏检 truth_claim·但 zero_shot 语义命中 → B_ok True·b_semantic 标记。"""
    fake = types.SimpleNamespace(
        classify=lambda text, protos, floor=0.5: {"label": "truth_claim", "score": 0.9})
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    # 引号段·含权力反差+风险姿态但 truth_claim 用 lexicon 外的说法
    para = "“我把这层窗户纸捅破，大人您也别装了。”小卒梗着脖子。"
    r = mod._detect_parrhesia(para)
    assert r["_B_semantic"] is True and r["B_truth_claim_quoted"] is True


def test_detect_parrhesia_backend_off_lexicon_only(monkeypatch):
    """内容后端不可用（classify None）→ b_semantic False·纯 lexicon·零回归。"""
    fake = types.SimpleNamespace(classify=lambda text, protos, floor=0.5: None)
    monkeypatch.setitem(sys.modules, "zero_shot_prototype", fake)
    para = "“我把这层窗户纸捅破。”"
    r = mod._detect_parrhesia(para)
    assert r["_B_semantic"] is False
