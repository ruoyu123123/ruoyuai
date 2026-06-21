# -*- coding: utf-8 -*-
"""choice_consequence_ledger · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import choice_consequence_ledger as mod  # noqa: E402

_TARGET = _SCRIPTS / "choice_consequence_ledger.py"
_ENV = "CHOICE_CONSEQUENCE_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_append_entry_writes_ledger():
    proj = _mk_project()
    e = mod.append_entry(proj, "cluster_005", "card_b",
                         "主角揭露 X", "faction", 3,
                         ["仇视", "X 派", "排挤"])
    assert e["cluster_id"] == "cluster_005"
    assert e["stakes_tier"] == "faction"
    assert e["expected_visibility_window"] == 3
    assert e["expires_at_cluster_idx"] == 8
    p = mod._ledger_path(proj)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["_namespace"] == "choice_consequence"
    assert len(data["entries"]) == 1


def test_append_invalid_tier_raises():
    proj = _mk_project()
    try:
        mod.append_entry(proj, "cluster_005", "card_b",
                         "x", "invalid_tier", 1, [])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "stakes_tier" in str(e)


def test_view_entries_filter():
    proj = _mk_project()
    mod.append_entry(proj, "cluster_005", "card_a", "s1",
                     "life", 2, ["kw1"])
    mod.append_entry(proj, "cluster_006", "card_b", "s2",
                     "moral", 2, ["kw2"])
    all_ = mod.view_entries(proj)
    assert len(all_) == 2
    f5 = mod.view_entries(proj, "cluster_005")
    assert len(f5) == 1
    assert f5[0]["choice_key"] == "card_a"


def test_scan_visible_when_resonance_hit():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_b",
                         "揭露 X", "faction", 3, ["仇视", "X 派"])
        draft = _write_draft("于是众人对他生出仇视·X 派开始反扑。" * 30)
        rep = mod.scan(proj, "cluster_006", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_VISIBLE in codes
        # ledger entry status 已更新
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "visible"
    finally:
        _set_mode(bak)


def test_scan_starving_after_window_expires():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        # cluster_005·窗口 2·过期 cluster_idx=7
        mod.append_entry(proj, "cluster_005", "card_a",
                         "选择", "faction", 2, ["不会出现的keyword"])
        draft = _write_draft("正文与 keyword 无关。" * 40)
        # cluster_008 = 已过期
        rep = mod.scan(proj, "cluster_008", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_STARVING in codes
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "starving"
    finally:
        _set_mode(bak)


def test_scan_pending_within_window():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "preference", 5, ["不出现的"])
        draft = _write_draft("正文。" * 30)
        # cluster_006 · 窗口未到
        rep = mod.scan(proj, "cluster_006", str(draft))
        codes = {v["code"] for v in rep["violations"]}
        assert mod.ISSUE_CODE_PENDING in codes
        assert mod.ISSUE_CODE_STARVING not in codes
        entries = mod.view_entries(proj)
        assert entries[0]["status"] == "pending"
    finally:
        _set_mode(bak)


def test_scan_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "life", 2, ["不出现"])
        draft = _write_draft("正文。" * 30)
        rep = mod.scan(proj, "cluster_010", str(draft))
        assert rep["mode"] == "off"
        assert "starving_entries" not in rep
    finally:
        _set_mode(bak)


def test_scan_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project()
        mod.append_entry(proj, "cluster_005", "card_a",
                         "x", "life", 2, ["不出现"])
        draft = _write_draft("正文。" * 30)
        rep = mod.scan(proj, "cluster_010", str(draft))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_namespace_separation():
    proj = _mk_project()
    mod.append_entry(proj, "cluster_005", "card_a", "x", "life", 2, [])
    p = mod._ledger_path(proj)
    data = json.loads(p.read_text(encoding="utf-8"))
    # 命名空间分立 = choice_consequence · 不是 author_planted
    assert data["_namespace"] == "choice_consequence"
    assert "author_planted" not in data


def test_cluster_idx_parse():
    assert mod._cluster_idx("cluster_005") == 5
    assert mod._cluster_idx("cluster_010") == 10
    assert mod._cluster_idx("") == 0
    assert mod._cluster_idx(None) == 0


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_STARVING, mod.ISSUE_CODE_VISIBLE,
              mod.ISSUE_CODE_PENDING):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_append_view_scan():
    proj = _mk_project()
    # append
    r = subprocess.run(
        [sys.executable, str(_TARGET), "append", str(proj), "cluster_005",
         "--choice-key", "card_b", "--summary", "s",
         "--tier", "faction", "--window", "2",
         "--keywords", "kw1,kw2"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    entry = json.loads(r.stdout)
    assert entry["choice_key"] == "card_b"

    # view
    r2 = subprocess.run(
        [sys.executable, str(_TARGET), "view", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r2.returncode == 0, r2.stderr
    entries = json.loads(r2.stdout)
    assert len(entries) == 1

    # scan
    draft = _write_draft("命中 kw1 命中 kw2。" * 20)
    r3 = subprocess.run(
        [sys.executable, str(_TARGET), "scan", str(proj),
         "cluster_006", str(draft)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r3.returncode in (0, 1), r3.stderr
    rep = json.loads(r3.stdout)
    assert rep["scanner"] == "choice_consequence_visibility_scanner"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_load_ledger_missing_returns_skeleton():
    proj = _mk_project()
    data = mod._load_ledger(proj)
    assert data["entries"] == []
    assert data["_namespace"] == "choice_consequence"
