# -*- coding: utf-8 -*-
"""acw_drift_scanner R20 W9 Batch-CC · P2 · Activity-Centric Writing 段中心活动漂移
确定性·零依赖·零 LLM/零联网。
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
import acw_drift_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "acw_drift_scanner.py"
_ENV = "ACW_MODE"


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


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"acw_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 段内主语稳定 (ACW 合规 · drift 应低 · 单主语「他」)
_STABLE_PARAGRAPHS = "\n\n".join([
    "他走进房间慢慢看了一圈。他放下手中的茶杯。他坐到椅子上靠着背。"
    "他抬起头看着窗外的雪。他叹了一口气低声说道。"
] * 20)

# 段内主语频繁切换 (ACW drift · 每段 5+ 不同主语)
_DRIFTING_PARAGRAPHS = "\n\n".join([
    "他走进房间。她抬起头。茶杯掉到地上。猫叫了一声。窗外刮风了。"
    "门吱呀一声开了。灯灭了。火苗摇晃。狗吠声响起。风停了。"
] * 20)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_STABLE_PARAGRAPHS), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "drift_paragraph_ratio" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("他走。"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_paragraphs_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 字数够但段落 < 5
        text = "他走。" * 200 + "\n\n" + "她走。" * 200
        out = mod.scan(_write(text), _mk_project())
        assert "段落不足" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRIFTING_PARAGRAPHS), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_stable_paragraphs_pass():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_STABLE_PARAGRAPHS), _mk_project())
        # 全段主语稳定 → drift_ratio 应低
        assert out["drift_paragraph_ratio"] < 0.30
    finally:
        _set_mode(bak)


def test_drifting_paragraphs_detected():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRIFTING_PARAGRAPHS), _mk_project())
        # 频繁切换 → drift_ratio 应高
        assert out["drift_paragraph_ratio"] > 0.5
        codes = {f["code"] for f in out.get("flags", [])}
        assert mod.ISSUE_CODE in codes
    finally:
        _set_mode(bak)


def test_author_baseline_overrides():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRIFTING_PARAGRAPHS),
                       _mk_project(baseline={"drift_paragraph_ratio_max": 1.0}))
        assert out["baseline_source"] == "author_profile"
        # 极宽 → 不报
        codes = {f["code"] for f in out.get("flags", [])}
        assert mod.ISSUE_CODE not in codes
    finally:
        _set_mode(bak)


def test_paragraph_drift_stable_paragraph():
    p = "他走进房间。他放下茶杯。他坐下。他抬头。他叹气。"
    r = mod._paragraph_drift(p)
    assert r["sentences"] == 5
    assert r["distinct_heads"] == 1  # 都是「他」
    assert r["is_drift"] is False


def test_paragraph_drift_drifting_paragraph():
    p = "他走进。她抬头。茶杯掉了。猫叫了。风吹了。"
    r = mod._paragraph_drift(p)
    assert r["sentences"] == 5
    assert r["distinct_heads"] == 5  # 5 个不同主语
    assert r["is_drift"] is True


def test_paragraph_drift_too_short():
    p = "他走。她跑。"
    r = mod._paragraph_drift(p)
    assert r["is_drift"] is False
    assert r["sentences"] == 2


def test_sentence_head_strips_punct():
    # HEAD_LEN=1 · 取首 CJK 字符做主语 proxy
    h = mod._sentence_head("\"他抬头说道")
    assert h == "他"


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_STABLE_PARAGRAPHS), _mk_project())
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_CODE not in audit_hub.HARD_GATE_CODES


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_STABLE_PARAGRAPHS)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "acw_drift"
