# -*- coding: utf-8 -*-
"""anadiplosis_scanner R22 W10 Batch-FF · P1 · 顶真单格 advisory"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import anadiplosis_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "anadiplosis_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ANADIPLOSIS_MODE", None)
    else:
        os.environ["ANADIPLOSIS_MODE"] = m


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
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    return proj


# 顶真密集 (末2字 = 首2字)
_ANADIP_RICH = ("这就是命运。命运无法改变。改变需要勇气。勇气来自信念。" * 40)

# 顶真缺席（普通叙述无首尾重叠）
_THIN = ("他写了一封长信。她拿过来看了。桌上还放着茶杯。" * 60)


def test_off_returns_skeleton():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ANADIP_RICH), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "anadiplosis_count" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_ANADIP_RICH), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_anadiplosis_detected():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_ANADIP_RICH), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ANADIPLOSIS_DETECTED" in codes
        assert out["anadiplosis_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_thin_no_detection():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_THIN), _mk_project())
        # 普通叙述无顶真命中（容差: 偶发命中 < 3）
        assert out["anadiplosis_count"] <= 3
    finally:
        _set_mode(bak)


def test_under_baseline_flagged_when_high_baseline_author():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        baseline = {"anadiplosis_per_kcj": 0.8}
        out = mod.scan(_write(_THIN), _mk_project(baseline=baseline))
        codes = {v["code"] for v in out.get("violations", [])}
        # 顶真型作者高基线 + 本 cluster 0 命中 → UNDER (前提密度极低)
        if out["anadiplosis_count"] == 0:
            assert "ANADIPLOSIS_UNDER_BASELINE" in codes
    finally:
        _set_mode(bak)


def test_baseline_dict_format_with_connectors():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        baseline = {"anadiplosis_per_kcj": {"mean": 1.0, "std": 0.2,
                                            "top_connectors": ["然后", "于是"]}}
        out = mod.scan(_write(_ANADIP_RICH), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        assert out["baseline"] == 1.0
        assert "然后" in out["top_connectors"]
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("命运。命运。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ANADIPLOSIS_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    s = "命运。命运无法改变。\n---CHANGES---\nyada"
    out = mod._strip_changes(s)
    assert "yada" not in out


def test_overlap_min_k():
    # k=1 不算（避免「的」「了」类误判）
    assert mod._max_overlap_len("xxx的", "的yyy") == 0
    # k=2 算
    assert mod._max_overlap_len("xxx命运", "命运yyy") == 2
    # k=4 优先长 match
    assert mod._max_overlap_len("xxx一脉相承", "一脉相承yyy") == 4


def test_overlap_drops_function_head_tail():
    # 句尾以助词结尾 → 不算
    assert mod._max_overlap_len("xxx了的", "了的yyy") == 0


def test_pairs_basic():
    text = "他走向命运。命运指引方向。方向决定结局。" * 30
    pairs = mod.detect_anadiplosis_pairs(text)
    # 应有 命运→命运 / 方向→方向 命中（k=2）
    assert pairs, f"expected pairs, got 0"
    assert any(len(p["seg"]) >= 2 for p in pairs)


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ANADIPLOSIS_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_ANADIP_RICH)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "anadiplosis"


def test_main_cli_shadow_no_warning():
    p = _write(_ANADIP_RICH)
    r = _run_cli(p, _mk_project(), mode="shadow")
    rep = json.loads(r.stdout)
    assert rep.get("warning") is None
