# -*- coding: utf-8 -*-
"""action_mentalizing_balance_scanner R21 W10 Batch-DD · R21-NB-01 · Motor-Mentalizing 比"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import action_mentalizing_balance_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "action_mentalizing_balance_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ACTION_MENTAL_BALANCE_MODE", None)
    else:
        os.environ["ACTION_MENTAL_BALANCE_MODE"] = m


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
            json.dumps({"action_mental_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 纯动作场景
_PURE_PHYSICAL = (
    "他扑过去，踢腿，挥拳，抓住对方的肩膀，劈下去，拽住腰，按住脊。"
    "他撞，他甩，他砸，他斩，他刺，他戳，他蹿，他跳，他跑，他蹲。"
) * 60

# 纯心智场景
_PURE_MENTAL = (
    "他想了想，觉得不对。他猜测，他怀疑，他担心。他意识到危险，他察觉异样。"
    "他揣摩对方的想法，他默念。他权衡，他犹豫。他笃定地相信。他不解，他疑惑。"
    "他料想未来，他寻思良久。他知道，他记得，他明白，他晓得，他认得。"
) * 30

# 平衡场景
_BALANCED = (
    "他扑过去，又想了想。踢腿之间觉得不对。挥拳的同时怀疑。抓住肩膀又担心。"
) * 50


def test_off_returns_skeleton():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_PURE_PHYSICAL), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_PURE_PHYSICAL), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_pure_physical_flagged():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PURE_PHYSICAL), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        # 纯动作 → DRIFT + (可能)PURE_PHYSICAL
        assert "ACTION_MENTAL_RATIO_DRIFT" in codes
        assert out["overall_ratio"] < 0.2
    finally:
        _set_mode(bak)


def test_active_pure_mental_flagged():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PURE_MENTAL), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        # 纯心智 → DRIFT
        assert "ACTION_MENTAL_RATIO_DRIFT" in codes
        assert out["overall_ratio"] > 0.7
    finally:
        _set_mode(bak)


def test_active_balanced_pass():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BALANCED), _mk_project())
        # 平衡场景：ratio 在 0.35-0.65 → 不应报 DRIFT
        # （但允许偏离 ±0.2 内）
        assert 0.2 < out["overall_ratio"] < 0.8
    finally:
        _set_mode(bak)


def test_scene_split_by_double_newlines():
    text = "他扑过去，他踢腿。\n\n他想了想，他猜测。"
    scenes = mod._split_scenes(text)
    assert len(scenes) == 2


def test_scene_split_no_blank_line():
    text = "他扑过去，他想了想。"
    scenes = mod._split_scenes(text)
    assert len(scenes) == 1


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        baseline = {"target_ratio": 0.05, "drift_band": 0.9,
                    "pure_physical_max": 0.0, "pure_mental_min": 1.0}
        out = mod.scan(_write(_PURE_PHYSICAL), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        # baseline 0.05 + drift_band 0.9 → 0 ratio 通过
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ACTION_MENTAL_RATIO_DRIFT" not in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他扑过去。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ACTION_MENTAL_BALANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_count_hits_overlap_safe():
    text = "想想想"
    n = mod._count_hits(text, ["想"])
    assert n == 3


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ACTION_MENTAL_BALANCE_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_PURE_PHYSICAL)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "action_mentalizing_balance"


def test_strip_changes_marker():
    s = "他扑过去。\n---CHANGES_FACTUAL---\nyada"
    assert "yada" not in mod._strip_changes(s)
