# -*- coding: utf-8 -*-
"""zeugma_scanner R22 W10 Batch-FF · P1 · 拈连单格 advisory"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import zeugma_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "zeugma_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ZEUGMA_MODE", None)
    else:
        os.environ["ZEUGMA_MODE"] = m


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


# 拈连密集（"飞鸟" common + "飞阿Q" unusual）
_ZEUGMA_RICH = ("他飞鸟过山岗。然后飞阿Q远去无形。" * 60)

# 拈连缺席（平凡叙述）
_THIN = ("他写了一封信。她看了一会儿。桌上是茶。" * 80)


def test_off_returns_skeleton():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "zeugma_count" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_zeugma_detected():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ZEUGMA_DETECTED" in codes
        assert out["zeugma_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_thin_no_detection():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_THIN), _mk_project())
        # 无拈连命中
        assert out["zeugma_count"] == 0
    finally:
        _set_mode(bak)


def test_under_baseline_flagged_when_comedy_author():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        baseline = {"zeugma_per_kcj": 0.6}  # 搞笑流作者
        out = mod.scan(_write(_THIN), _mk_project(baseline=baseline))
        codes = {v["code"] for v in out.get("violations", [])}
        # 搞笑流但 0 命中 → UNDER
        assert "ZEUGMA_UNDER_BASELINE" in codes
    finally:
        _set_mode(bak)


def test_baseline_dict_format():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        baseline = {"zeugma_per_kcj": {"mean": 0.4, "std": 0.1}}
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        assert out["baseline"] == 0.4
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他飞鸟。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_lexicon_placeholder_flag():
    lex = mod.load_lexicon()
    # 占位词典 _placeholder=true（确保 advisory 不被当真词典）
    assert lex.get("_placeholder") is True


def test_strip_changes_marker():
    s = "正文飞鸟过山岗。\n---CHANGES---\nyada"
    out = mod._strip_changes(s)
    assert "yada" not in out


def test_detect_pairs_zero_when_only_common():
    text = "他飞鸟过去。然后飞鸟回来了。" * 40  # 全是 common·非拈连
    pairs = mod.detect_zeugma_pairs(text, mod.load_lexicon())
    assert pairs == [] or all(p["obj1"] != p["obj2"] for p in pairs)


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ZEUGMA_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_ZEUGMA_RICH)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "zeugma"


def test_main_cli_thin_no_warning():
    p = _write(_THIN)
    r = _run_cli(p, _mk_project(), mode="shadow")
    rep = json.loads(r.stdout)
    # shadow 模式不上报
    assert rep.get("warning") is None
