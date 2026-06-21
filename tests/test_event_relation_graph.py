# -*- coding: utf-8 -*-
"""event_relation_graph builder+validator R19 W8 Batch-W·P1 回归测试。

零依赖·确定性。覆盖 builder shadow/build、空 input、majors/clusters/foreshadowings
聚合、character throughline 聚合、validator 4 issue (orphan/cycle/payoff/broken)、
mode 切换、CLI、hard_gate 守卫。
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
import event_relation_graph_builder as builder  # noqa: E402
import event_relation_graph_validator as validator  # noqa: E402

_B_TARGET = _SCRIPTS / "event_relation_graph_builder.py"
_V_TARGET = _SCRIPTS / "event_relation_graph_validator.py"
_B_ENV = "EVENT_RELATION_GRAPH_MODE"
_V_ENV = "EVENT_RELATION_GRAPH_VALIDATE_MODE"


def _set_env(name, m):
    if m is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = m


def _mk_project(da_shi_ka=None, shi_jian_cu=None, fu_bi=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if da_shi_ka is not None:
        (db / "大势卡.json").write_text(json.dumps(da_shi_ka, ensure_ascii=False), encoding="utf-8")
    if shi_jian_cu is not None:
        (db / "事件簇.json").write_text(json.dumps(shi_jian_cu, ensure_ascii=False), encoding="utf-8")
    if fu_bi is not None:
        (db / "伏笔表.json").write_text(json.dumps(fu_bi, ensure_ascii=False), encoding="utf-8")
    return proj


def test_builder_empty_inputs_returns_empty():
    proj = _mk_project()
    g = builder.build(proj)
    assert g["schema_version"] == 1
    assert g["nodes"] == []
    assert g["edges"] == []
    assert g["character_throughlines"] == {}


def test_builder_collects_majors():
    da_shi_ka = {
        "major_events": [
            {"id": "ME-V1-1", "title": "凿一窍", "volume": 1, "cluster": "cluster_001",
             "prerequisites": []},
            {"id": "ME-V1-2", "title": "再凿", "volume": 1, "cluster": "cluster_002",
             "prerequisites": ["ME-V1-1"]},
        ]
    }
    proj = _mk_project(da_shi_ka=da_shi_ka)
    g = builder.build(proj)
    ids = {n["id"] for n in g["nodes"]}
    assert "ME-V1-1" in ids
    assert "ME-V1-2" in ids
    edges = [(e["src"], e["dst"], e["kind"]) for e in g["edges"]]
    assert ("ME-V1-1", "ME-V1-2", "causal_prerequisite") in edges


def test_builder_collects_clusters_and_payoff():
    shi_jian_cu = {
        "clusters": [
            {"id": "cluster_001", "title": "开端", "volume": 1,
             "scope_summary": "起手", "payoff_cluster": "cluster_005"},
            {"id": "cluster_002", "title": "中段", "volume": 1,
             "prerequisites": ["cluster_001"]},
        ]
    }
    proj = _mk_project(shi_jian_cu=shi_jian_cu)
    g = builder.build(proj)
    ids = {n["id"] for n in g["nodes"]}
    assert "cluster_001" in ids and "cluster_002" in ids
    edges = [(e["src"], e["dst"], e["kind"]) for e in g["edges"]]
    assert ("cluster_001", "cluster_005", "cluster_payoff") in edges
    assert ("cluster_001", "cluster_002", "cluster_prerequisite") in edges


def test_builder_collects_foreshadowings_three_link():
    fu_bi = {
        "promises": [
            {"id": "F-001", "description": "断裂的剑",
             "setup_cluster": "cluster_001", "payoff_cluster": "cluster_005",
             "related_chars": ["江条款"]},
        ]
    }
    proj = _mk_project(fu_bi=fu_bi)
    g = builder.build(proj)
    edges = [(e["src"], e["dst"], e["kind"]) for e in g["edges"]]
    assert ("cluster_001", "F-001", "foreshadowing_setup") in edges
    assert ("F-001", "cluster_005", "foreshadowing_payoff") in edges


def test_builder_character_throughlines():
    shi_jian_cu = {
        "clusters": [
            {"id": "cluster_001", "focal_characters": ["江条款", "审校"]},
            {"id": "cluster_002", "focal_characters": ["江条款"]},
            {"id": "cluster_003", "focal_characters": ["江条款", "审校"]},
        ]
    }
    proj = _mk_project(shi_jian_cu=shi_jian_cu)
    g = builder.build(proj)
    tl = g["character_throughlines"]
    assert "char:江条款" in tl
    assert tl["char:江条款"] == ["cluster_001", "cluster_002", "cluster_003"]


def test_validator_orphan_node():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "active")
        graph = {
            "nodes": [{"id": "ME-X", "kind": "major_event"},
                      {"id": "cluster_001", "kind": "cluster"}],
            "edges": [],
            "character_throughlines": {},
        }
        r = validator.validate(graph)
        codes = [v["code"] for v in r["violations"]]
        assert "NODE_ORPHAN" in codes
    finally:
        _set_env(_V_ENV, bak)


def test_validator_edge_cycle():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "active")
        graph = {
            "nodes": [{"id": "A", "kind": "cluster"},
                      {"id": "B", "kind": "cluster"},
                      {"id": "C", "kind": "cluster"}],
            "edges": [{"src": "A", "dst": "B", "kind": "x"},
                      {"src": "B", "dst": "C", "kind": "x"},
                      {"src": "C", "dst": "A", "kind": "x"}],
            "character_throughlines": {},
        }
        r = validator.validate(graph)
        codes = [v["code"] for v in r["violations"]]
        assert "EDGE_CYCLE" in codes
    finally:
        _set_env(_V_ENV, bak)


def test_validator_payoff_unreachable():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "active")
        # F-001 没 foreshadowing_payoff 边
        graph = {
            "nodes": [
                {"id": "cluster_001", "kind": "cluster"},
                {"id": "F-001", "kind": "foreshadowing"},
            ],
            "edges": [{"src": "cluster_001", "dst": "F-001", "kind": "foreshadowing_setup"}],
            "character_throughlines": {},
        }
        r = validator.validate(graph)
        codes = [v["code"] for v in r["violations"]]
        assert "PAYOFF_UNREACHABLE" in codes
    finally:
        _set_env(_V_ENV, bak)


def test_validator_character_throughline_broken():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "active")
        graph = {
            "nodes": [
                {"id": "cluster_001", "kind": "cluster"},
                {"id": "cluster_002", "kind": "cluster"},
                {"id": "cluster_003", "kind": "cluster"},
                {"id": "cluster_005", "kind": "cluster"},
                {"id": "char:江条款", "kind": "character"},
            ],
            "edges": [],
            "character_throughlines": {"char:江条款": ["cluster_001", "cluster_005"]},
        }
        r = validator.validate(graph)
        codes = [v["code"] for v in r["violations"]]
        assert "CHARACTER_THROUGH_LINE_BROKEN" in codes
    finally:
        _set_env(_V_ENV, bak)


def test_validator_off_mode():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "off")
        r = validator.validate({"nodes": [], "edges": [], "character_throughlines": {}})
        assert r["mode"] == "off"
    finally:
        _set_env(_V_ENV, bak)


def test_validator_shadow_no_violations():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "shadow")
        graph = {
            "nodes": [{"id": "ME-X", "kind": "major_event"}],
            "edges": [],
            "character_throughlines": {},
        }
        r = validator.validate(graph)
        assert r["violations"] == []
    finally:
        _set_env(_V_ENV, bak)


def test_validator_clean_graph_passes():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "active")
        graph = {
            "nodes": [
                {"id": "cluster_001", "kind": "cluster"},
                {"id": "cluster_002", "kind": "cluster"},
                {"id": "F-001", "kind": "foreshadowing"},
            ],
            "edges": [
                {"src": "cluster_001", "dst": "cluster_002", "kind": "cluster_prerequisite"},
                {"src": "cluster_001", "dst": "F-001", "kind": "foreshadowing_setup"},
                {"src": "F-001", "dst": "cluster_002", "kind": "foreshadowing_payoff"},
            ],
            "character_throughlines": {},
        }
        r = validator.validate(graph)
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        _set_env(_V_ENV, bak)


def test_builder_mode_invalid_falls_back():
    bak = os.environ.get(_B_ENV)
    try:
        _set_env(_B_ENV, "bogus")
        assert builder._mode() == "shadow"
    finally:
        _set_env(_B_ENV, bak)


def test_validator_mode_invalid_falls_back():
    bak = os.environ.get(_V_ENV)
    try:
        _set_env(_V_ENV, "bogus")
        assert validator._mode() == "shadow"
    finally:
        _set_env(_V_ENV, bak)


def test_codes_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for code in validator.ISSUE_CODES:
        assert code not in hgs, f"{code} 误进 hard_gate"


def test_builder_cli_shadow():
    proj = _mk_project(shi_jian_cu={"clusters": [{"id": "cluster_001"}]})
    r = subprocess.run(
        [sys.executable, str(_B_TARGET), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _B_ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["mode"] == "shadow"
    assert rep["nodes"] >= 1


def test_builder_cli_build_writes_file():
    proj = _mk_project(shi_jian_cu={"clusters": [{"id": "cluster_001"}]})
    r = subprocess.run(
        [sys.executable, str(_B_TARGET), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _B_ENV: "build", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    out_file = proj / "_数据库" / "event_relation_graph.json"
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_validator_cli():
    graph = {
        "nodes": [{"id": "cluster_001", "kind": "cluster"}],
        "edges": [],
        "character_throughlines": {},
    }
    d = Path(tempfile.mkdtemp())
    gp = d / "g.json"
    gp.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_V_TARGET), str(gp)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _V_ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["validator"] == "event_relation_graph"
