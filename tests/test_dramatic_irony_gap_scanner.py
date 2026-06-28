# -*- coding: utf-8 -*-
"""dramatic_irony_gap_scanner 回归测试

🔴 2026-06-29 孤儿scanner重接线(名字错配·指向真数据源)：旧版读零 producer 的幻影
读者信念账本.json / 角色信念账本.json（永远 skip = 死码）。重接线后读真数据源
character_belief_ledger.json（producer: apply_archive.apply_belief_updates）：
  - 角色信念 = characters[cid].known_facts
  - 读者信念 = known_facts[].reader_knows==true 的并集
  - 戏剧反讽缺口（桌下炸弹）= 读者已知 ∩ 至少一角色 unaware_of
本套件造 character_belief_ledger.json，验证 dramatic irony 检测不再 skip（端到端激活）。
全 advisory·DRAMATIC_IRONY_GAP_* 绝不进 HARD_GATE_CODES。
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


def _mk_project(belief_ledger=None):
    """造 character_belief_ledger.json（真数据源）。belief_ledger=None → 不建文件（旧书）。"""
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if belief_ledger is not None:
        (db / "character_belief_ledger.json").write_text(
            json.dumps(belief_ledger, ensure_ascii=False), encoding="utf-8")
    return proj


# 长草稿（cjk >= 500·重接线后内容不参与检测·仅过短稿闸）
_LONG_DRAFT = "一段无关的正文内容好长好长用来凑字数。" * 60


def _ledger_reader_knows_all_aware():
    """读者知 F_秘密·唯一角色 C1 也知道·无人 unaware → 无反讽（EMPTY）。"""
    return {
        "schema_version": 1,
        "characters": {
            "C1": {"known_facts": [
                {"fact_id": "F_秘密", "content": "藏起来的真相",
                 "learned_at_cluster": "cluster_001", "reader_knows": True,
                 "can_speak": True}], "unaware_of": []},
        },
        "facts": {"F_秘密": {"content": "藏起来的真相",
                            "first_revealed_cluster": "cluster_001", "subject": "C1"}},
    }


def _ledger_reader_knows_char_unaware(learned="cluster_001"):
    """读者知 F_秘密（C1 知·reader_knows）·C2 unaware_of F_秘密 → 桌下炸弹（GAP>0）。"""
    return {
        "schema_version": 1,
        "characters": {
            "C1": {"known_facts": [
                {"fact_id": "F_秘密", "content": "藏起来的真相",
                 "learned_at_cluster": learned, "reader_knows": True,
                 "can_speak": True}], "unaware_of": []},
            "C2": {"known_facts": [], "unaware_of": ["F_秘密"]},
        },
        "facts": {"F_秘密": {"content": "藏起来的真相",
                            "first_revealed_cluster": learned, "subject": "C1"}},
    }


# ───── 1 off → PASS ──
def test_off_returns_pass():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_LONG_DRAFT), _mk_project(_ledger_reader_knows_char_unaware()))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── 2 短稿 skip ──
def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("秘密。"), _mk_project(_ledger_reader_knows_char_unaware()))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


# ───── 3 无 ledger（旧书）→ 优雅 skip·向后兼容 ──
def test_no_ledger_graceful_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_DRAFT), _mk_project(belief_ledger=None))
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
        assert "无 character_belief_ledger" in out.get("note", "")
        assert out["reader_known_count"] == 0
    finally:
        _set_mode(bak)


# ───── 4 端到端激活：GAP>0（读者知·角色不知）→ 检测不再 skip ──
def test_active_irony_gap_detected_not_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_DRAFT),
                       _mk_project(_ledger_reader_knows_char_unaware()))
        # 不再 skip：有真 reader_known + 桌下炸弹 gap
        assert "note" not in out or "跳过" not in out.get("note", "")
        assert out["reader_known_count"] >= 1
        assert out["irony_gap_count"] >= 1
        assert "藏起来的真相" in out["irony_gap_samples"]
        # gap>0 → 不报 EMPTY
        assert not any(v["code"] == "DRAMATIC_IRONY_GAP_EMPTY" for v in out["violations"])
    finally:
        _set_mode(bak)


# ───── 5 EMPTY：读者知 + 全员角色都知道（无人 unaware）→ empty advisory ──
def test_active_irony_gap_empty_advisory():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_DRAFT),
                       _mk_project(_ledger_reader_knows_all_aware()))
        assert out["reader_known_count"] >= 1
        assert out["irony_gap_count"] == 0
        assert out["verdict"] == "FAIL_MINOR"
        assert any(v["code"] == "DRAMATIC_IRONY_GAP_EMPTY" for v in out["violations"])
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ───── 6 STALE：读者知跨度≥3 cluster 仍有角色不知 → stale advisory ──
def test_stale_advisory_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_DRAFT),
                       _mk_project(_ledger_reader_knows_char_unaware(learned="cluster_001")),
                       cluster_id="cluster_004")
        assert "DRAMATIC_IRONY_GAP_STALE" in [v["code"] for v in out["violations"]]
        assert "藏起来的真相" in out["stale_facts"]
    finally:
        _set_mode(bak)


# ───── 7 STALE 不触发：跨度 < 3 ──
def test_stale_not_triggered_within_window():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_DRAFT),
                       _mk_project(_ledger_reader_knows_char_unaware(learned="cluster_003")),
                       cluster_id="cluster_004")
        assert "DRAMATIC_IRONY_GAP_STALE" not in [v["code"] for v in out["violations"]]
    finally:
        _set_mode(bak)


# ───── 8 shadow + gap → 不上报 ──
def test_shadow_gap_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LONG_DRAFT),
                       _mk_project(_ledger_reader_knows_all_aware()))
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ───── 9 读取失败 ──
def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ───── 10 _mode 非法回落 ──
def test_mode_invalid():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 11 _strip_changes / _cjk_count ──
def test_strip_changes():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"


def test_cjk_count():
    assert mod._cjk_count("好abc世界") == 3


# ───── 12 接真 loader 单测 ──
def test_load_belief_ledger_missing_returns_none():
    assert mod._load_belief_ledger(_mk_project(belief_ledger=None)) is None
    assert mod._load_belief_ledger(None) is None


def test_reader_known_fact_ids():
    led = _ledger_reader_knows_char_unaware()
    assert mod._reader_known_fact_ids(led) == {"F_秘密"}


def test_all_character_known():
    led = _ledger_reader_knows_char_unaware()
    assert mod._all_character_known(led) == {"F_秘密"}


def test_all_unaware_fact_ids():
    led = _ledger_reader_knows_char_unaware()
    assert mod._all_unaware_fact_ids(led) == {"F_秘密"}


def test_cluster_ord():
    assert mod._cluster_ord("cluster_007") == 7
    assert mod._cluster_ord("cluster_001") == 1
    assert mod._cluster_ord("current") is None
    assert mod._cluster_ord(None) is None


def test_fact_label_resolves_content():
    facts = {"F_秘密": {"content": "藏起来的真相"}}
    assert mod._fact_label("F_秘密", facts) == "藏起来的真相"
    assert mod._fact_label("F_未知", facts) == "F_未知"


# ───── 13 CLI subprocess ──
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs():
    proj = _mk_project(_ledger_reader_knows_char_unaware())
    r = _run_cli(_write(_LONG_DRAFT), proj)
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "dramatic_irony_gap"


def test_main_cli_empty_advisory_exit_1():
    proj = _mk_project(_ledger_reader_knows_all_aware())
    r = _run_cli(_write(_LONG_DRAFT), proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert any(v["code"] == "DRAMATIC_IRONY_GAP_EMPTY" for v in rep["violations"])
