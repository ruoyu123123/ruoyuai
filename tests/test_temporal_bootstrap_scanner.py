# -*- coding: utf-8 -*-
"""temporal_bootstrap_scanner R19 W8 Batch-Y·P2 Tarjan SCC bootstrap paradox 回归测试。

确定性·零依赖。覆盖 off/无 edges skip/DAG PASS/简单 2-cycle/自环/3-node 大环/
brief 注入 vs project 兜底/shadow vs active/CLI/hard_gate.
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
import temporal_bootstrap_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "temporal_bootstrap_scanner.py"
_ENV = "TEMPORAL_BOOTSTRAP_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_brief(edges):
    d = Path(tempfile.mkdtemp())
    p = d / "brief.json"
    p.write_text(json.dumps({"info_provenance": {"edges": edges}},
                            ensure_ascii=False), encoding="utf-8")
    return p


def _mk_project(edges):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "info_provenance.json").write_text(
        json.dumps({"edges": edges}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan()
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_edges_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan()
        assert "无 info_provenance" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_dag_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        edges = [
            {"info": "A", "from": "B"},
            {"info": "B", "from": "C"},
        ]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        assert r["verdict"] == "PASS"
        assert r["cycles"] == []
    finally:
        _set_mode(bak)


def test_two_cycle_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        edges = [
            {"info": "A", "from": "B"},
            {"info": "B", "from": "A"},  # A → B → A loop
        ]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        codes = [v["code"] for v in r["violations"]]
        assert "TEMPORAL_BOOTSTRAP_LOOP" in codes
        assert len(r["cycles"]) >= 1
    finally:
        _set_mode(bak)


def test_self_loop_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        edges = [{"info": "X", "from": "X"}]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        codes = [v["code"] for v in r["violations"]]
        assert "TEMPORAL_BOOTSTRAP_LOOP" in codes
    finally:
        _set_mode(bak)


def test_three_node_cycle_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        edges = [
            {"info": "A", "from": "B"},
            {"info": "B", "from": "C"},
            {"info": "C", "from": "A"},
        ]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        codes = [v["code"] for v in r["violations"]]
        assert "TEMPORAL_BOOTSTRAP_LOOP" in codes
        assert any(len(c) >= 3 for c in r["cycles"])
    finally:
        _set_mode(bak)


def test_project_fallback():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project([{"info": "X", "from": "Y"}, {"info": "Y", "from": "X"}])
        r = mod.scan(project_root=proj)
        codes = [v["code"] for v in r["violations"]]
        assert "TEMPORAL_BOOTSTRAP_LOOP" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        edges = [{"info": "A", "from": "B"}, {"info": "B", "from": "A"}]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_invalid_edges_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        edges = [{"info": None, "from": "X"}, {"foo": "bar"}]
        r = mod.scan(cluster_brief_path=_mk_brief(edges))
        # 全无效 → skip
        assert "skip" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_build_graph_basic():
    edges = [{"info": "A", "from": "B"}]
    g, nodes = mod._build_graph(edges)
    assert "A" in g
    assert "B" in g["A"]
    assert "B" in nodes


def test_tarjan_no_cycle():
    g = {"A": ["B"], "B": ["C"], "C": []}
    nodes = {"A", "B", "C"}
    cycles = mod.tarjan_scc(g, nodes)
    assert cycles == []


def test_tarjan_simple_cycle():
    g = {"A": ["B"], "B": ["A"]}
    nodes = {"A", "B"}
    cycles = mod.tarjan_scc(g, nodes)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"A", "B"}


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_runs_shadow():
    proj = _mk_project([])
    r = subprocess.run(
        [sys.executable, str(_TARGET), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "temporal_bootstrap"
