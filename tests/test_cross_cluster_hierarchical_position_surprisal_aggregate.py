# -*- coding: utf-8 -*-
"""故事块层级位置惊异度聚合器测试。"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_hierarchical_position_surprisal_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_hierarchical_position_surprisal_aggregate.py"
_ENV = "HIERARCHICAL_POSITION_SURPRISAL_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project(records=None, drafts=None):
    proj = Path(tempfile.mkdtemp())
    write_cluster_summary(proj, records or [])
    for cluster_id, body in (drafts or {}).items():
        key = cluster_id.removeprefix("cluster_")
        d = proj / "章节" / f"cluster_{key}_draft"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"cluster_{key}_draft.txt").write_text(body, encoding="utf-8")
    return proj


_BODY = (
    "第一段春风又绿江南岸明月何时照我还。\n\n"
    "第二段山色空蒙雨亦奇水光潋滟晴方好。\n\n"
    "第三段落霞与孤鹜齐飞秋水共长天一色。\n\n\n"
    "第四段烟笼寒水月笼沙夜泊秦淮近酒家。\n\n"
    "第五段千山鸟飞绝万径人踪灭。\n\n\n"
    "第六段最后高潮戏剧性大转折真相揭晓彻底翻盘。\n\n"
    "第七段所有人惊呆血雨腥风风云突变。"
) * 8


def test_mode_default_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_no_clusters_returns_none():
    proj = _mk_project()
    s, f = mod.aggregate(proj, last_n=10)
    assert s is None
    assert f == []


def test_tokenize_bichar_filters_non_cjk():
    toks = mod._tokenize("abc中国def人")
    assert "中国" in toks
    assert all(all("一" <= c <= "鿿" for c in t) for t in toks)


def test_surprisal_seq_basic():
    freqs = {"中国": 5, "人民": 2}
    surps = mod._surprisal_seq(["中国", "人民"], freqs, 7)
    assert len(surps) == 2
    assert surps[1] > surps[0]


def test_avg_around_handles_edges():
    surps = [1.0, 2.0, 3.0, 4.0, 5.0]
    # idx 0 → left 空·返回 0
    assert mod._avg_around(surps, 0, w=2) == 0.0


def test_compute_para_deltas_short_returns_zero():
    assert mod._compute_para_deltas("短", {}, 1) == 0.0


def test_aggregate_one_cluster():
    records = [cluster_record("cluster_001")]
    proj = _mk_project(records, {"cluster_001": _BODY})
    s, f = mod.aggregate(proj, last_n=10)
    assert s is not None
    assert s["clusters"] == ["cluster_001"]
    assert "avg_delta_para" in s
    assert "avg_delta_cluster_end" in s
    for x in f:
        assert x["severity"] == "advisory"
        assert x["code"] == mod.ISSUE_CODE


def test_aggregate_multi_cluster_honors_window():
    records = [cluster_record(f"cluster_{n:03d}") for n in range(1, 4)]
    drafts = {record["cluster_id"]: _BODY for record in records}
    proj = _mk_project(records, drafts)
    s, f = mod.aggregate(proj, last_n=10)
    assert s is not None
    assert s["clusters"] == ["cluster_001", "cluster_002", "cluster_003"]
    s, _ = mod.aggregate(proj, last_n=2)
    assert s["clusters"] == ["cluster_002", "cluster_003"]


def test_missing_cluster_draft_is_fatal():
    proj = _mk_project([cluster_record("cluster_001")])
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 2
    assert "cluster 终稿不存在" in r.stderr


def test_scan_cluster_compute():
    r = mod._scan_cluster(_BODY)
    assert r is not None
    assert "delta_para" in r
    assert "tokens" in r


# 场景模型路径需要至少两个超过长度阈值的场景。
_BIG_SCENE_BODY = (
    (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
    + ("\n" * 3)
    + (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
    + ("\n" * 3)
    + (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
)


def test_model_source_used_when_enabled(monkeypatch):
    """模型命中时三类指标均记录模型来源。"""
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: [
            {"mean_surprisal": 3.0 + i * 0.5, "source": "model"} for i, _ in enumerate(texts)
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
    r = mod._scan_cluster(_BIG_SCENE_BODY)
    assert r is not None
    assert r["source"]["delta_para"] == "model"
    assert r["source"]["delta_scene"] == "model"
    assert r["source"]["delta_cluster_end"] == "model"


def test_enabled_model_failure_is_fatal(monkeypatch):
    """显式启用模型后不允许静默切换为频次估算。"""
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: [None for _ in texts]
    )
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
    with pytest.raises(RuntimeError, match="返回不完整"):
        mod._scan_cluster(_BODY)


def test_model_disabled_by_default_no_env(monkeypatch):
    """未启用模型时不请求推理。"""
    monkeypatch.delenv("RUOYU_NN_SURPRISAL", raising=False)
    assert mod._predict_surprisal_batch(["测试文本"]) == [None]


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_shadow_exit_zero():
    records = [cluster_record("cluster_001"), cluster_record("cluster_002")]
    proj = _mk_project(records, {
        "cluster_001": _BODY,
        "cluster_002": _BODY,
    })
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), "--last-n", "5"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr


def test_main_cli_off_skip():
    proj = _mk_project([cluster_record("cluster_001")], {"cluster_001": _BODY})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    assert "SKIP" in r.stdout
