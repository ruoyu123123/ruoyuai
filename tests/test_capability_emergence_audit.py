# -*- coding: utf-8 -*-
"""capability_emergence_audit.py 专属回归测试
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
import capability_emergence_audit as mod  # noqa: E402

_TARGET = _SCRIPTS / "capability_emergence_audit.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CAPABILITY_EMERGENCE_MODE", None)
    else:
        os.environ["CAPABILITY_EMERGENCE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, ledger=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        (proj / "_数据库" / "capability_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


_USE_DRAFT = ("林轩双手掐诀，催动火球术，向敌人射出。\n"
              "他施展北辰剑诀，剑气如虹。\n"
              "他凝聚雷电法术，劈向魔王。\n"
              "他释放冰封大阵，冻结全场。\n") * 30


# ── 基础分支 ────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger={"entries": []}))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_ledger_skips():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project())
        assert "ledger" in out["note"] or "无" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_empty_ledger_skips():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger={"entries": []}))
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("太短。" * 3),
                       project_root=_mk_project(ledger={"entries": [{"name": "火球术"}]}))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 锚点缺失 → advisory ────────────────────────────
def test_ungrounded_capability_active():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        ledger = {"entries": [
            {"name": "火球术"},  # 无 acquisition
            {"name": "北辰剑诀"},
        ]}
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger=ledger))
        assert out["ungrounded_count"] >= 1
        assert out["verdict"] == "FAIL_MINOR"
        codes = [v["code"] for v in out["violations"]]
        assert "CAPABILITY_EMERGENCE_UNGROUNDED" in codes
    finally:
        _set_mode(bak)


def test_grounded_capability_pass():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        ledger = {"entries": [
            {"name": "火球术", "acquisition_event": "拜师学艺",
             "acquired_at_cluster": "cluster_001"},
            {"name": "北辰剑诀", "acquisition_event": "捡到秘籍",
             "acquired_at_cluster": "cluster_002"},
            {"name": "雷电法术", "acquisition_event": "突破", "acquired_at_cluster": "cluster_003"},
            {"name": "冰封大阵", "acquisition_event": "祖传", "acquired_at_cluster": "cluster_004"},
        ]}
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger=ledger))
        assert out["ungrounded_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_inherent_exempts():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        ledger = {"entries": [
            {"name": "火球术", "inherent": True},
            {"name": "北辰剑诀", "inherent": True},
            {"name": "雷电法术", "inherent": True},
            {"name": "冰封大阵", "inherent": True},
        ]}
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger=ledger))
        assert out["ungrounded_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_unused_capability_not_reported():
    """能力 ledger 中但本 cluster 未使用 → 不报"""
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("active")
        ledger = {"entries": [{"name": "无人提及的能力"}]}
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger=ledger))
        assert out["ungrounded_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("shadow")
        ledger = {"entries": [{"name": "火球术"}]}
        out = mod.scan(_write(_USE_DRAFT), project_root=_mk_project(ledger=ledger))
        assert out["mode"] == "shadow"
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── _read_ledger 全分支 ────────────────────────────
def test_read_ledger_none_project():
    assert mod._read_ledger(None) == []


def test_read_ledger_missing_file():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_ledger(proj) == []


def test_read_ledger_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "capability_ledger.json").write_text("{bad", encoding="utf-8")
    assert mod._read_ledger(proj) == []


def test_read_ledger_list_form():
    proj = _mk_project(ledger=[{"name": "甲"}, {"name": "乙"}])
    out = mod._read_ledger(proj)
    assert len(out) == 2 and out[0]["name"] == "甲"


def test_read_ledger_dict_capabilities_key():
    proj = _mk_project(ledger={"capabilities": [{"name": "丙"}]})
    out = mod._read_ledger(proj)
    assert out and out[0]["name"] == "丙"


def test_read_ledger_top_garbage():
    proj = _mk_project(ledger="纯字符串")
    assert mod._read_ledger(proj) == []


# ── find_first_use ──────────────────────────────────
def test_find_first_use_hit():
    text = "他施展火球术，烧死敌人。\n他又用了北辰剑诀。"
    r = mod.find_first_use(text, "火球术")
    assert r is not None and r["pos"] >= 0
    assert "火球术" in r["snippet"]


def test_find_first_use_miss():
    assert mod.find_first_use("没有能力的纯叙述。", "玄冰诀") is None


def test_find_first_use_empty_name():
    assert mod.find_first_use("任意文本", "") is None


def test_find_first_use_no_verb():
    """有能力名但无使用动词 → 不算 first_use"""
    assert mod.find_first_use("他听说过火球术的传说。", "火球术") is None


# ── _strip / _cjk / _mode ──────────────────────────
def test_strip_changes_factual():
    assert mod._strip_changes("正文\n---CHANGES_FACTUAL---\nx") == "正文"


def test_strip_changes_plain():
    assert mod._strip_changes("正文\n---CHANGES---\nx") == "正文"


def test_strip_changes_none():
    assert mod._strip_changes("无") == "无"


def test_cjk_count():
    assert mod._cjk_count("中文abc") == 2


def test_mode_invalid_falls_back():
    bak = os.environ.get("CAPABILITY_EMERGENCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────
def test_main_cli_pass():
    proj = _mk_project(ledger={"entries": []})
    p = _write(_USE_DRAFT)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CAPABILITY_EMERGENCE_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"


def test_main_cli_fail_minor():
    proj = _mk_project(ledger={"entries": [{"name": "火球术"}, {"name": "北辰剑诀"}]})
    p = _write(_USE_DRAFT)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CAPABILITY_EMERGENCE_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
