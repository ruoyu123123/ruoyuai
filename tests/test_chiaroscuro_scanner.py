# -*- coding: utf-8 -*-
"""chiaroscuro_scanner.py 专属回归测试
(2026-06-20·R12 W6 Batch-Q·确定性·零依赖)"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import chiaroscuro_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "chiaroscuro_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CHIAROSCURO_MODE", None)
    else:
        os.environ["CHIAROSCURO_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if signature is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"luminance_signature": signature}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 强亮草稿（光词密集）
_BRIGHT_DRAFT = ("阳光灿烂，晨光熹微，明月当空。\n"
                 "灯光通明，烛光闪烁，星光皎洁。\n"
                 "光辉夺目，光彩璀璨，光明耀眼。\n") * 40

# 强暗草稿（暗词密集）
_DARK_DRAFT = ("夜幕低垂，黑暗笼罩，幽暗无光。\n"
               "阴影森森，墨黑漆黑，昏暗无月。\n"
               "暗影重重，暮色苍茫，幽深晦暗。\n") * 40

# 平衡草稿
_BALANCED_DRAFT = ("光明与黑暗交替。\n阳光与阴影并存。\n"
                   "明亮与昏暗共生。\n白昼与黑夜轮转。\n") * 40

# 干净（无光暗词）
_CLEAN_DRAFT = "他走到了集市，买了菜，回家做饭。\n" * 60


# ── 基础分支 ────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_BRIGHT_DRAFT), project_root=_mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("太短。" * 5))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_clean_draft_skips():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CLEAN_DRAFT), project_root=_mk_project())
        assert "未命中" in out.get("note", "") or out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ratio 分支 ─────────────────────────────────────
def test_over_bright_active():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BRIGHT_DRAFT), project_root=_mk_project())
        assert out["chiaroscuro_ratio"] > 0.70
        codes = [v["code"] for v in out["violations"]]
        assert "OVER_BRIGHT" in codes
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_over_dark_active():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DARK_DRAFT), project_root=_mk_project())
        assert out["chiaroscuro_ratio"] < 0.30
        codes = [v["code"] for v in out["violations"]]
        assert "OVER_DARK" in codes
    finally:
        _set_mode(bak)


def test_balanced_passes():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BALANCED_DRAFT), project_root=_mk_project())
        assert 0.25 < out["chiaroscuro_ratio"] < 0.75
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_BRIGHT_DRAFT), project_root=_mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 作者档基线 ─────────────────────────────────────
def test_author_signature_band():
    """作者偏亮基线 ratio_p50=0.80·当前亮草稿 0.8+ 应在带内"""
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(signature={"ratio_p50": 0.80})
        out = mod.scan(_write(_BRIGHT_DRAFT), project_root=proj)
        assert out["baseline"]["_source"] == "author_profile"
        assert out["author_p50"] == 0.80
    finally:
        _set_mode(bak)


def test_drift_from_author_flag():
    """作者偏暗（p50=0.10）·实际亮草稿 0.95 → DRIFT_FROM_AUTHOR"""
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(signature={"ratio_p50": 0.10})
        out = mod.scan(_write(_BRIGHT_DRAFT), project_root=proj)
        codes = [v["code"] for v in out["violations"]]
        assert "DRIFT_FROM_AUTHOR" in codes
    finally:
        _set_mode(bak)


# ── 辅助函数 ────────────────────────────────────────
def test_load_lexicon():
    light, dark = mod._load_lexicon()
    assert len(light) >= 40 and len(dark) >= 40
    assert "阳光" in light and "黑暗" in dark


def test_count_terms_basic():
    assert mod._count_terms("光光光暗暗", ["光"]) == 3
    assert mod._count_terms("无任何关键词", ["光"]) == 0
    assert mod._count_terms("", ["光"]) == 0


def test_count_terms_empty_term_skipped():
    assert mod._count_terms("abc", ["", "a"]) == 1


def test_read_author_signature_none():
    assert mod._read_author_signature(None) is None


def test_read_author_signature_missing():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_author_signature(proj) is None


def test_read_author_signature_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{bad", encoding="utf-8")
    assert mod._read_author_signature(proj) is None


def test_read_author_signature_no_key():
    proj = _mk_project()
    (proj / "_数据库" / "作者风格.json").write_text("{}", encoding="utf-8")
    assert mod._read_author_signature(proj) is None


def test_strip_changes_factual():
    assert mod._strip_changes("正文\n---CHANGES_FACTUAL---\nx") == "正文"


def test_strip_changes_plain():
    assert mod._strip_changes("正文\n---CHANGES---\nx") == "正文"


def test_strip_changes_none():
    assert mod._strip_changes("无") == "无"


def test_cjk_count():
    assert mod._cjk_count("中文abc") == 2


def test_mode_invalid_falls_back():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("CHIAROSCURO_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────
def test_main_cli_bright_fail():
    p = _write(_BRIGHT_DRAFT)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHIAROSCURO_MODE": "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_cli_balanced_pass():
    p = _write(_BALANCED_DRAFT)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHIAROSCURO_MODE": "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"
