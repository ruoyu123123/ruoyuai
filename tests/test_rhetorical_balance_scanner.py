# -*- coding: utf-8 -*-
"""rhetorical_balance_scanner R22 W10 Batch-DD · P0 STRONG · 38 格分布 advisory"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import rhetorical_balance_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "rhetorical_balance_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("RHETORICAL_BALANCE_MODE", None)
    else:
        os.environ["RHETORICAL_BALANCE_MODE"] = m


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
            json.dumps({"rhetorical_signature": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 富四类·分布均衡
_RICH_BALANCED = (
    "他像猛虎一样扑过去。万丈光芒之间。渐渐显现的字迹。再再再来一次，相对仗。"
    "她仿佛见鬼。借代用丹青写就。茫茫不可识。又重复，对偶工整。"
) * 25

# 单类垄断·材料类极偏
_MATERIAL_HEAVY = (
    "他像如似仿佛好比犹如宛若好像猛虎。借代巾帼须眉笔杆白发黄口丹青。"
) * 30

# 修辞极稀薄
_THIN = "天气好。屋子大。桌上是茶。" * 50


def test_off_returns_skeleton():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_RICH_BALANCED), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "category_distribution" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_THIN), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_thin_flagged():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_THIN), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "RHETORICAL_INVENTORY_THIN" in codes
    finally:
        _set_mode(bak)


def test_active_material_heavy_drift():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_MATERIAL_HEAVY), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        # 单类垄断 → drift + collapse
        assert "RHETORICAL_BALANCE_DRIFT" in codes
        # material 占比应很高
        assert out["category_distribution"]["material"] > 0.5
    finally:
        _set_mode(bak)


def test_active_rich_balanced_pass():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        baseline = {"distribution": {"material": 0.5, "imagery": 0.2,
                                     "wording": 0.15, "syntax": 0.15},
                    "kl_threshold": 5.0, "thin_per_kcjk": 1.0,
                    "collapse_share": 0.0}
        out = mod.scan(_write(_RICH_BALANCED), _mk_project(baseline=baseline))
        codes = {v["code"] for v in out.get("violations", [])}
        # 极宽 baseline → 不报 DRIFT
        assert "RHETORICAL_BALANCE_DRIFT" not in codes
        assert "RHETORICAL_INVENTORY_THIN" not in codes
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        baseline = {"distribution": {"material": 1.0, "imagery": 0.0,
                                     "wording": 0.0, "syntax": 0.0},
                    "kl_threshold": 100.0, "thin_per_kcjk": 0.0,
                    "collapse_share": 0.0}
        out = mod.scan(_write(_RICH_BALANCED), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他像虎。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    s = "正文段落像虎扑过去。\n---CHANGES---\nyada"
    out = mod._strip_changes(s)
    assert "yada" not in out


def test_collapse_skipped_when_total_hits_low():
    bak = os.environ.get("RHETORICAL_BALANCE_MODE")
    try:
        _set_mode("active")
        # 仅 1 个譬喻触发词·CJK 充分 → total_hits 极低 → collapse 不报
        text = "他像虎。" + "天气好早晨长。" * 200  # 充分 CJK·只 1 个譬喻
        out = mod.scan(_write(text), _mk_project())
        codes = [v["code"] for v in out.get("violations", [])]
        # total_hits 应该极低（只 1 个"像"）
        assert "total_hits" in out
        if out["total_hits"] < 8:
            assert "RHETORICAL_CATEGORY_COLLAPSE" not in codes
    finally:
        _set_mode(bak)


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "RHETORICAL_BALANCE_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_RICH_BALANCED)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "rhetorical_balance"


def test_main_cli_thin_returns_warning():
    p = _write(_THIN)
    r = _run_cli(p, _mk_project())
    rep = json.loads(r.stdout)
    assert rep.get("warning") is not None
