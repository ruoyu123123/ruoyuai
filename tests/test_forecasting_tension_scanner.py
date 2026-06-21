# -*- coding: utf-8 -*-
"""forecasting_tension_scanner R20 W9 Batch-CC · P2 · 句级张力梯度 forecasting (entropy proxy)
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
import forecasting_tension_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "forecasting_tension_scanner.py"
_ENV = "FORECASTING_TENSION_MODE"


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
            json.dumps({"forecasting_tension_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 单调重复 (entropy 各块都极低 · 相邻差 ≈ 0 → flat)
_MONOTONE = "他走他走他走" * 500

# 多样块 (各块 entropy 有显著差)
_VARIED = (
    ("他突然开口说话，墙壁震动起来。"
     "完全寂静无声。" * 50
     + "战斗一触即发，刀枪相接如雷霆。火焰漫天而起。"
     + "黑暗中只有钟声。" * 50
     + "万物归一，归于尘土。")
    * 4
)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_VARIED), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "blocks_count" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"), _mk_project())
        assert "草稿太短" in out.get("note", "")
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


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_MONOTONE), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_monotone_detected_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_MONOTONE), _mk_project())
        # 完全 monotone → flatline_ratio 应高
        assert out["flatline_ratio"] > 0.5
    finally:
        _set_mode(bak)


def test_varied_passes_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_VARIED), _mk_project())
        # 多样 → 应不报或 flat 概率低
        assert out["gradient_pstdev"] > 0
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        baseline = {"gradient_pstdev_min": 0.0, "flatline_ratio_max": 1.0}
        out = mod.scan(_write(_MONOTONE), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        # 极宽 baseline → 不报
        codes = {f["code"] for f in out.get("flags", [])}
        assert mod.ISSUE_CODE not in codes
    finally:
        _set_mode(bak)


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_VARIED), _mk_project())
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_shannon_entropy_basic():
    # 单字符 → entropy = 0
    assert mod._shannon_entropy("aaaa") == 0.0
    # 两个等概率 → entropy = 1
    assert abs(mod._shannon_entropy("ab") - 1.0) < 0.01


def test_split_blocks_drops_short_tail():
    blocks = mod._split_blocks("a" * 450, 200)
    # 450 → 200 + 200 + 50(< 100=size//2 不入) → 2 blocks
    assert len(blocks) == 2


def test_gradient_series():
    g = mod._gradient_series([1.0, 2.0, 1.5])
    assert g == [1.0, -0.5]


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
    p = _write(_VARIED)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "forecasting_tension"
