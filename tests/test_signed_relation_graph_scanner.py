# -*- coding: utf-8 -*-
"""signed_relation_graph_scanner R11 W6 STRONG 回归测试(确定性·零依赖)"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import signed_relation_graph_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "signed_relation_graph_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("SIGNED_RELATION_GRAPH_MODE", None)
    else:
        os.environ["SIGNED_RELATION_GRAPH_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(names, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps({"known_names": names}, ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"signed_graph_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


_COZY_DRAFT = ("张三笑着拥抱李四，他们彼此信任互敬互爱。\n张三和王五一起喝茶，王五赞了张三几句关怀十分。\n"
               "李四和王五互相帮扶，温柔地理解彼此心意。\n张三和李四又一同笑着，相互支持感激默契。\n") * 16
_HOSTILE_DRAFT = ("张三冷笑着斥责李四，他们彼此仇视杀意盎然。\n王五怒骂张三，张三反过来嘲讽鄙夷他几句。\n"
                  "李四和王五翻脸，背叛了对方阴险地威胁。\n张三和李四杀气腾腾互相威胁逼问敌视对方。\n") * 16


def test_off_returns_skeleton():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(["张三", "李四", "王五"])
        out = mod.scan(_write(_COZY_DRAFT), proj)
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "metrics" not in out
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_COZY_DRAFT), None)
        assert "无人物卡" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_active_overly_cozy_flags():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("active")
        baseline = {"mean_positivity_ratio": 0.4, "sd": 0.05}
        proj = _mk_project(["张三", "李四", "王五"], baseline=baseline)
        out = mod.scan(_write(_COZY_DRAFT), proj)
        assert "metrics" in out
        assert out["z_positivity"] > 1.5
        assert out["violations"]
        assert out["violations"][0]["code"] == "SIGNED_GRAPH_OVERLY_COZY"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_active_overly_hostile_flags():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("active")
        baseline = {"mean_positivity_ratio": 0.7, "sd": 0.05}
        proj = _mk_project(["张三", "李四", "王五"], baseline=baseline)
        out = mod.scan(_write(_HOSTILE_DRAFT), proj)
        assert out["z_positivity"] < -1.5
        assert out["violations"][0]["code"] == "SIGNED_GRAPH_OVERLY_HOSTILE"
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("shadow")
        baseline = {"mean_positivity_ratio": 0.4, "sd": 0.05}
        proj = _mk_project(["张三", "李四", "王五"], baseline=baseline)
        out = mod.scan(_write(_COZY_DRAFT), proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["甲", "乙"])
        out = mod.scan(_write("甲笑了笑。" * 3), proj)
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_failure_skip():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("SIGNED_RELATION_GRAPH_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_read_known_names_no_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_known_names(proj) == []
    assert mod._read_known_names(None) == []


def test_aggregate_edges_majority():
    edges = [("a", "b", 1), ("a", "b", 1), ("a", "b", -1)]
    agg = mod._aggregate_edges(edges)
    assert agg[("a", "b")] == 1


def test_aggregate_edges_tie():
    edges = [("a", "b", 1), ("a", "b", -1)]
    assert mod._aggregate_edges(edges)[("a", "b")] == 0


def test_graph_metrics_too_few_chars():
    assert mod._graph_metrics({("a", "b"): 1}, ["a", "b"]) is None


def test_cli_runs_clean():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps({"known_names": ["张三", "李四", "王五"]}, ensure_ascii=False),
        encoding="utf-8")
    p = _write(_COZY_DRAFT)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SIGNED_RELATION_GRAPH_MODE": "shadow",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"
