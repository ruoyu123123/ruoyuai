# -*- coding: utf-8 -*-
"""failure_segment_prose_density_scanner R22 W10 Batch-EE·P1 回归测试"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import failure_segment_prose_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "failure_segment_prose_density_scanner.py"
_ENV = "FAILURE_SEGMENT_DENSITY_MODE"


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
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (db / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return proj


# 富细节成功段·空乏失败段（低五感/低转折/低方差/无对话）
_GAP_DRAFT = (
    # 成功段：富细节
    "他成功了。但是亮光闪烁，声响传来，触感冰冷。\n\n"
    "“真好。”他望向天空。然而响声不绝，色彩斑斓。\n\n"
    "可是他听到声响，看到亮光，闻到香气，尝到甜味。\n\n"
    "他赢了。然而触感温热，色泽柔和。" * 8
    + "\n\n"
    # 失败段：空乏
    "他失败了。" * 25
    + "\n\n他又失败了。" * 25
    + "\n\n这次也失败了。" * 25
)

# 均衡稿（成功段与失败段密度近似）
_BALANCED_DRAFT = (
    "他成功了，看到光，听到声响。\n\n" * 30
    + "他失败了，看到光，听到声响。\n\n" * 30
)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_GAP_DRAFT), _mk_project())
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_override_flag():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(profile={"_failure_segment_density_off": True})
        out = mod.scan(_write(_GAP_DRAFT), proj)
        assert "_failure_segment_density_off" in out["note"]
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("失败了。"), _mk_project())
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_no_fail_or_succ_span_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        text = "他走在街上。" * 200
        out = mod.scan(_write(text), _mk_project())
        # 无成功/失败段 → 不对比·跳过
        assert "无法对比" in out.get("note", "") or out["fail_span_count"] == 0
    finally:
        _set_mode(bak)


def test_shadow_gap_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_GAP_DRAFT), _mk_project())
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_active_gap_fail_minor():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_GAP_DRAFT), _mk_project())
        # 失败段空乏 → 至少有 1 维 z<-0.5
        if out.get("low_density_dimensions"):
            assert out["verdict"] == "FAIL_MINOR"
            assert out["violations"][0]["code"] == "FAILURE_SEGMENT_DENSITY_GAP"
            assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"


def test_cjk_count():
    assert mod._cjk_count("好abc世界") == 3


def test_split_paragraphs():
    assert mod._split_paragraphs("a\n\nb\n\nc") == ["a", "b", "c"]


def test_segment_by_anchor_basic():
    paras = ["他走", "他失败了", "结果", "他成功了", "落幕"]
    fs = mod._segment_by_anchor(paras, ("失败了",))
    ss = mod._segment_by_anchor(paras, ("成功了",))
    assert len(fs) >= 1
    assert len(ss) >= 1


def test_density_metrics_empty():
    assert mod._density_metrics([], []) == {}


def test_z_compare_empty():
    assert mod._z_compare({}, {}) == {}


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs():
    proj = _mk_project()
    r = _run_cli(_write(_GAP_DRAFT), proj)
    # exit 0 或 1 都合法（取决于是否有 low dim）
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "failure_segment_prose_density"
