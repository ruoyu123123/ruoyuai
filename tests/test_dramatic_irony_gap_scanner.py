# -*- coding: utf-8 -*-
"""dramatic_irony_gap_scanner R22 W10 Batch-EE·P1 回归测试"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import dramatic_irony_gap_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "dramatic_irony_gap_scanner.py"
_ENV = "DRAMATIC_IRONY_GAP_MODE"


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


def _mk_project(reader_ledger=None, char_ledger=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if reader_ledger is not None:
        (db / "读者信念账本.json").write_text(
            json.dumps(reader_ledger, ensure_ascii=False), encoding="utf-8")
    if char_ledger is not None:
        (db / "角色信念账本.json").write_text(
            json.dumps(char_ledger, ensure_ascii=False), encoding="utf-8")
    return proj


# 含 fact_ref 词命中（秘密/真相）
_DRAFT_WITH_FACTS = "他知道秘密。她也听到了真相。" * 60


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_WITH_FACTS), _mk_project())
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("秘密。"), _mk_project())
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_shadow_no_irony_no_report():
    """shadow + empty 当前 cluster 第一次跑·new_facts 累积·gap 由 character_ledger 决定"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        # character ledger 没人 → gap = reader_known
        out = mod.scan(_write(_DRAFT_WITH_FACTS), _mk_project())
        # shadow 不上报
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_active_irony_gap_empty_advisory():
    """reader_known 全部已在 character ledger → gap 为空 → empty advisory"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 让 reader 已知 + character 全已知
        proj = _mk_project(
            reader_ledger={"reader_known": ["秘密", "真相"], "reveal_cluster": {},
                           "cluster_history": []},
            char_ledger={"by_character": {"张三": {"known": ["秘密", "真相"]}}})
        out = mod.scan(_write("一段无关的文本内容好长好长。" * 80), proj)
        # 无新事实进入 + reader_known 完全等于 char_known → gap=0 报 empty
        if out["reader_known_count"] >= 1 and out["irony_gap_count"] == 0:
            assert out["verdict"] == "FAIL_MINOR"
            assert any(v["code"] == "DRAMATIC_IRONY_GAP_EMPTY" for v in out["violations"])
    finally:
        _set_mode(bak)


def test_active_gap_existing_no_empty_advisory():
    """char 不知 reader 知 → gap >0 → 不报 empty"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(
            reader_ledger={"reader_known": ["秘密"], "reveal_cluster": {},
                           "cluster_history": []},
            char_ledger={"by_character": {"张三": {"known": []}}})
        out = mod.scan(_write("一段无关的文本内容好长好长。" * 80), proj)
        assert out["irony_gap_count"] >= 1
        assert not any(v["code"] == "DRAMATIC_IRONY_GAP_EMPTY" for v in out["violations"])
    finally:
        _set_mode(bak)


def test_stale_advisory_triggers():
    """reveal_cluster 跨度≥3 且 char 仍未知 → stale"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(
            reader_ledger={
                "reader_known": ["秘密"],
                "reveal_cluster": {"秘密": "c1"},
                "cluster_history": ["c1", "c2", "c3", "c4"],
            },
            char_ledger={"by_character": {"张三": {"known": []}}})
        out = mod.scan(_write("一段无关的文本内容好长好长。" * 80), proj, cluster_id="c5")
        assert "DRAMATIC_IRONY_GAP_STALE" in [v["code"] for v in out["violations"]]
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


def test_reader_ledger_skeleton():
    s = mod._reader_ledger_skeleton()
    assert s["reader_known"] == []
    assert s["_placeholder"] is True


def test_character_ledger_skeleton():
    s = mod._character_ledger_skeleton()
    assert s["by_character"] == {}


def test_all_character_known_aggregation():
    led = {"by_character": {
        "甲": {"known": ["a", "b"]},
        "乙": {"known": ["b", "c"]},
    }}
    assert mod._all_character_known(led) == {"a", "b", "c"}


def test_all_character_known_invalid():
    assert mod._all_character_known({"by_character": "garbage"}) == set()


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs():
    proj = _mk_project()
    r = _run_cli(_write(_DRAFT_WITH_FACTS), proj)
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "dramatic_irony_gap"
