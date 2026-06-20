# -*- coding: utf-8 -*-
"""prose_chaizi_ledger.py 专属回归测试
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
import prose_chaizi_ledger as mod  # noqa: E402

_TARGET = _SCRIPTS / "prose_chaizi_ledger.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CHAIZI_LEDGER_MODE", None)
    else:
        os.environ["CHAIZI_LEDGER_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genres=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genres is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_genre_packs": genres}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 含拆字模板·字典锚字（"好"=女+子）
_CHAIZI_RICH_DRAFT = ("此乃左女右子的字，谶语应在十年后。\n"
                      "上日下月的字，预兆光明在前。\n"
                      "去掉单人旁，便是个孬字。\n"
                      "加上心字底，成了忐字。\n"
                      "此乃字谜，打一字也，谜底见分晓。\n"
                      "拆字之术，可窥天机字谶之机。\n") * 30

_CLEAN_DRAFT = "他走到了集市，买了菜，回家做饭。\n" * 60
_CLEAN_DRAFT_LONG = ("他走到了集市，买了菜，回家做饭。\n他洗了碗。\n"
                     "他看了书，去公园散步，又回家睡觉。\n") * 500


# ── 基础分支 ────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["xianxia"]))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("太短。" * 3),
                       project_root=_mk_project(genres=["xianxia"]))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── genre 门控 ─────────────────────────────────────
def test_no_genre_skips():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT), project_root=_mk_project())
        assert "题材" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_modern_genre_skips():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["urban_modern"]))
        assert "题材" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_xianxia_genre_active():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["xianxia"]))
        assert out["event_count"] > 0
        assert out["chaizi_density_per_10k"] > 0
    finally:
        _set_mode(bak)


def test_historical_genre_active():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["historical"]))
        assert out["event_count"] > 0
    finally:
        _set_mode(bak)


def test_espionage_genre_active():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["espionage"]))
        assert out["event_count"] > 0
    finally:
        _set_mode(bak)


# ── 命中分支 ────────────────────────────────────────
def test_clean_low_density_thin_flag():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CLEAN_DRAFT_LONG),
                       project_root=_mk_project(genres=["xianxia"]))
        # 长草稿无拆字 → THIN
        codes = [v["code"] for v in out["violations"]]
        assert "CHAIZI_DENSITY_THIN" in codes
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_CLEAN_DRAFT),
                       project_root=_mk_project(genres=["xianxia"]))
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


# ── 功能桶分布 ──────────────────────────────────────
def test_function_buckets_filled():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_CHAIZI_RICH_DRAFT),
                       project_root=_mk_project(genres=["xianxia"]))
        dist = out["chaizi_function_distribution"]
        # 至少 prophecy 桶有命中（谶语）
        assert sum(dist.values()) == out["event_count"]
        assert dist["prophecy"] > 0 or dist["joke"] > 0
    finally:
        _set_mode(bak)


# ── find_chaizi_events ─────────────────────────────
def test_find_split_left_right():
    events = mod.find_chaizi_events("此乃左女右子。")
    assert events and events[0]["template"] == "split_left_right"


def test_find_riddle():
    events = mod.find_chaizi_events("打一字。")
    assert events and events[0]["template"] == "riddle"


def test_find_empty():
    assert mod.find_chaizi_events("纯叙述无关键。") == []


def test_find_dict_verified():
    """窗口内含字典字 → dict_verified=True"""
    events = mod.find_chaizi_events("左女右子，孬字之妙。")
    assert events
    assert any(e["dict_verified"] for e in events)


def test_find_sorted_by_pos():
    events = mod.find_chaizi_events("打一字。\n字谜如此。\n左日右月之妙。")
    positions = [e["pos"] for e in events]
    assert positions == sorted(positions)


# ── _read_genres 全分支 ────────────────────────────
def test_read_genres_none_project():
    assert mod._read_genres(None) == set()


def test_read_genres_missing_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_genres(proj) == set()


def test_read_genres_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{bad", encoding="utf-8")
    assert mod._read_genres(proj) == set()


def test_read_genres_top_not_dict():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("[1,2]", encoding="utf-8")
    assert mod._read_genres(proj) == set()


def test_read_genres_string_form():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"genre": "Xianxia"}), encoding="utf-8")
    assert "xianxia" in mod._read_genres(proj)


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
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("CHAIZI_LEDGER_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ─────────────────────────────────────────────
def test_main_cli_modern_genre_pass():
    p = _write(_CHAIZI_RICH_DRAFT)
    proj = _mk_project(genres=["urban_modern"])
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHAIZI_LEDGER_MODE": "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert "题材" in rep.get("note", "")


def test_main_cli_xianxia_thin_fail():
    p = _write(_CLEAN_DRAFT_LONG)
    proj = _mk_project(genres=["xianxia"])
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CHAIZI_LEDGER_MODE": "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1, r.stderr
