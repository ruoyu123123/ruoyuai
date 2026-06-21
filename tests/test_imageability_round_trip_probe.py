# -*- coding: utf-8 -*-
"""imageability_round_trip_probe · R24 W12 Batch-LL · P2"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import imageability_round_trip_probe as mod  # noqa: E402

_TARGET = _SCRIPTS / "imageability_round_trip_probe.py"
_ENV = "IMAGEABILITY_ROUND_TRIP_MODE"


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


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


# 高具象 · 多 anchor_noun + spatial + sensory（每段 ≥120 CJK · 全 cjk≥500）
_HIGH = (
    "桌子上有茶杯，门外是窗，墙上挂着灯。" * 8
    + "他看着东边的院子，推开北边的门。" * 8
    + "\n\n" +
    "刀握在手里，剑挂墙上，镜子里是床，钥匙锁好屋子。" * 8
    + "他听见树外的水声，闻到火和酒。" * 8
    + "\n\n" +
    "茶在桌上摆好，饭已上桌，鞋脱在门外。" * 8
    + "她瞧着南边的窗，握住椅子背。" * 8
)

# 低具象 · 抽象段无 anchor/sensory token
_LOW = (
    "他思考着意义本质真理理念概念信念原则立场态度情感。" * 10
    + "\n\n" +
    "理论逻辑思想理想幻想观点哲学价值方法体系。" * 10
    + "\n\n" +
    "存在虚无主义意识形态精神现象学根源认识论。" * 10
)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_HIGH), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "round_trip" not in out
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


def test_few_candidates_emit_na():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "短段一。\n\n短段二。\n\n" + "桌子" * 300
        out = mod.scan(_write(text), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        # 仅 1 个长段 < TOP_K=3 → NA info
        assert mod.ISSUE_CODE_NA in codes
    finally:
        _set_mode(bak)


def test_high_retention_detected():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_HIGH), _mk_project())
        assert out["mean_retention"] > 0.3
        # 高具象段·应至少不报 LOW
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_LOW not in codes
    finally:
        _set_mode(bak)


def test_low_retention_flagged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LOW), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        # 抽象段无 anchor/sensory 命中·retention≈0
        assert mod.ISSUE_CODE_LOW in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LOW), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_round_trip_returns_keys():
    rt = mod._round_trip("桌子上有茶杯。门外是窗。" * 10)
    for k in ("prompt_tokens", "caption_tokens", "retention", "para_cjk"):
        assert k in rt
    assert 0.0 <= rt["retention"] <= 1.0


def test_extract_tokens_dedup():
    out = mod._extract_tokens("桌子桌子茶杯", mod._ANCHOR_NOUNS["_words"])
    assert "桌子" in out
    assert "茶杯" in out
    # set 去重
    assert isinstance(out, set)


def test_jaccard_function():
    assert mod._jaccard(set(), set()) == 1.0
    assert mod._jaccard({"a"}, {"a"}) == 1.0
    assert mod._jaccard({"a"}, {"b"}) == 0.0
    assert abs(mod._jaccard({"a", "b"}, {"b", "c"}) - 1 / 3) < 0.01


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HIGH), _mk_project())
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_lexicons_placeholder():
    assert mod._ANCHOR_NOUNS["_placeholder"] is True
    assert mod._SPATIAL_WORDS["_placeholder"] is True
    assert mod._SENSORY_VERBS["_placeholder"] is True


def test_split_paragraphs():
    out = mod._split_paragraphs("段一\n\n段二\n\n段三")
    assert len(out) == 3


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
    for c in (mod.ISSUE_CODE_LOW, mod.ISSUE_CODE_HIGH, mod.ISSUE_CODE_NA):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write(_HIGH)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "imageability_round_trip_probe"


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
