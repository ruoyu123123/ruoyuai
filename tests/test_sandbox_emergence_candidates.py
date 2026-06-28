# -*- coding: utf-8 -*-
"""sandbox_emergence_candidates R19 W8 Batch-X·P2 StoryBox 沙盒涌现包裹·回归。

确定性·零依赖。覆盖 off/shadow/active mode/K/T clamp/_sandbox_simulate/
emerge_with_sandbox top-down 包裹/active 合并 candidates/CLI/registry 守卫。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import sandbox_emergence_candidates as mod  # noqa: E402

_TARGET = _SCRIPTS / "sandbox_emergence_candidates.py"
_ENV = "SANDBOX_EMERGENCE_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project(characters=None, factions=None, with_event_cluster=True):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters:
        (db / "角色池.json").write_text(
            json.dumps({"emerged": [{"name": n} for n in characters]},
                       ensure_ascii=False), encoding="utf-8")
    if factions:
        (db / "世界状态.json").write_text(
            json.dumps({"factions": [{"name": f} for f in factions]},
                       ensure_ascii=False), encoding="utf-8")
    # 给 top-down emergence 一个最小 事件簇 + 大势卡 让它能跑
    if with_event_cluster:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": []}, ensure_ascii=False), encoding="utf-8")
        (db / "大势卡.json").write_text(
            json.dumps({}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_mode_default():
    bak = os.environ.get(_ENV)
    try:
        _set_mode(None)
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


def test_clamp_helper():
    assert mod._clamp(1, 3, 5) == 3
    assert mod._clamp(10, 3, 5) == 5
    assert mod._clamp(4, 3, 5) == 4


def test_load_characters_no_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._load_characters(proj) == []


def test_load_characters_with_pool():
    proj = _mk_project(characters=["A", "B", "C"])
    assert set(mod._load_characters(proj)) == {"A", "B", "C"}


def test_load_world_state_no_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._load_world_state(proj) == {}


def test_load_world_state_with_factions():
    proj = _mk_project(characters=["A"], factions=["势力甲"])
    ws = mod._load_world_state(proj)
    assert "factions" in ws


def test_sandbox_simulate_empty_chars():
    assert mod._sandbox_simulate([], {}) == []


def test_sandbox_simulate_returns_two_candidates():
    cands = mod._sandbox_simulate(["小王", "李雷", "韩梅"], {}, k=3, t=10)
    assert len(cands) == 2
    assert all(c.get("sandbox_origin") == "bottom_up" for c in cands)
    assert all("stake_score" in c for c in cands)


def test_sandbox_simulate_k_t_clamped():
    # 即使传超界 K/T 也应钳到 [3,5]/[8,12]
    cands = mod._sandbox_simulate(["A", "B", "C", "D", "E", "F"], {}, k=99, t=99)
    assert len(cands) == 2
    cands2 = mod._sandbox_simulate(["A", "B", "C"], {}, k=1, t=1)
    assert len(cands2) == 2


def test_emerge_with_sandbox_off_mode_no_sandbox():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project(characters=["A", "B", "C"])
        # mock emerge_next_cluster 防真跑(避免依赖)
        with mock.patch.object(mod, "emerge_with_sandbox") as _:
            pass
        # 直接调用
        import cluster_emergence_engine as cee
        with mock.patch.object(
                cee, "emerge_next_cluster", return_value={"ok": True, "candidates": []}):
            r = mod.emerge_with_sandbox(proj, "cluster_001")
            assert r["mode"] == "off"
            assert r["sandbox"] == []
    finally:
        _set_mode(bak)


def test_emerge_with_sandbox_shadow_mode_sandbox_not_merged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=["A", "B", "C"], factions=["势力甲"])
        import cluster_emergence_engine as cee
        with mock.patch.object(
                cee, "emerge_next_cluster",
                return_value={"ok": True, "candidates": [{"cluster_id_proposed": "C1"}]}):
            r = mod.emerge_with_sandbox(proj, "cluster_001")
            assert r["mode"] == "shadow"
            assert len(r["sandbox"]) == 2
            # shadow 不合并
            assert r["merged_candidates"] == []
    finally:
        _set_mode(bak)


def test_emerge_with_sandbox_active_mode_merges():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=["A", "B", "C"], factions=["势力甲"])
        import cluster_emergence_engine as cee
        with mock.patch.object(
                cee, "emerge_next_cluster",
                return_value={"ok": True, "candidates": [{"cluster_id_proposed": "C1"}]}):
            r = mod.emerge_with_sandbox(proj, "cluster_001")
            assert r["mode"] == "active"
            # active 才合并
            assert len(r["merged_candidates"]) == 3
            # 第一个是 top-down
            assert r["merged_candidates"][0]["cluster_id_proposed"] == "C1"
            # 后两个是 sandbox
            assert all(c.get("sandbox_origin") == "bottom_up"
                       for c in r["merged_candidates"][1:])
    finally:
        _set_mode(bak)


def test_emerge_with_sandbox_no_project():
    r = mod.emerge_with_sandbox(None, "cluster_001")
    assert "error" in r


def test_cli_runs():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")  # off 不会跑 sandbox·避免真 emerge 依赖
        proj = _mk_project(characters=["A", "B", "C"])
        r = subprocess.run(
            [sys.executable, str(_TARGET),
             "--project", str(proj), "--after", "cluster_001"],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"})
        # 退出 0(也允许 1 报错)
        assert r.returncode in (0, 1)
        assert "mode" in r.stdout
    finally:
        _set_mode(bak)


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("sandbox_emergence_candidates")
    assert entry is not None
    assert entry.get("_new") is True


def test_code_not_in_hard_gate():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "SANDBOX_EMERGENCE_ABNORMAL" not in set(data.get("hard_gate_codes", []))
