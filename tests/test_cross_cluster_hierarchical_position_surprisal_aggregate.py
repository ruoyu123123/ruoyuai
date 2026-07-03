# -*- coding: utf-8 -*-
"""cross_cluster_hierarchical_position_surprisal_aggregate R18 W7 Batch-U·P2 SCH 回归。

确定性·零依赖。覆盖 off 退出/无章节早返/拟合 SCH 三类 Δ/层级颠倒报 advisory/
_tokenize/_surprisal_seq/_compute_para_deltas/_compute_scene_deltas/
_compute_cluster_end_delta/aggregate API/CLI mode shadow 0 退出·
registry 未污染 hard_gate_codes。
"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

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


def _mk_project(chapters=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "章节").mkdir(parents=True, exist_ok=True)
    chapters = chapters or {}
    for ch, body in chapters.items():
        d = proj / "章节" / f"第{ch:03d}章"
        d.mkdir(exist_ok=True)
        (d / "body.txt").write_text(body, encoding="utf-8")
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


def test_no_chapters_returns_none():
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


def test_aggregate_one_chapter():
    proj = _mk_project({1: _BODY})
    s, f = mod.aggregate(proj, last_n=10)
    assert s is not None
    assert "avg_delta_para" in s
    assert "avg_delta_cluster_end" in s
    # findings 可能 0 或 1·都是 advisory
    for x in f:
        assert x["severity"] == "advisory"
        assert x["code"] == mod.ISSUE_CODE


def test_aggregate_multi_chapter():
    proj = _mk_project({1: _BODY, 2: _BODY})
    s, f = mod.aggregate(proj, last_n=10)
    assert s is not None
    assert len(s["chapters"]) == 2


def test_list_chapters_sorted():
    proj = _mk_project({3: "x", 1: "y", 2: "z"})
    chs = mod._list_chapters(proj)
    assert chs == [1, 2, 3]


def test_read_chapter_body_missing_returns_none():
    proj = _mk_project()
    assert mod._read_chapter_body(proj, 99) is None


def test_scan_cluster_compute():
    r = mod._scan_cluster(_BODY)
    assert r is not None
    assert "delta_para" in r
    assert "tokens" in r


# ============ 🔴 2026-07-01 真模型(surprisal_gpt2) 接入回归 ============

# _BODY 的场景切块(\n{3,} 分隔)全部 < 100 CJK(被 _compute_scene_deltas 的过滤条件挡掉)·
# 场景级模型路径需要真正过滤后仍 ≥2 个的"场景"·故用专用大文本(每场景 270 CJK)单独测 scene 维度。
_BIG_SCENE_BODY = (
    (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
    + ("\n" * 3)
    + (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
    + ("\n" * 3)
    + (("这是一段测试内容用来填充字数达到最低门槛不多不少刚刚好。\n\n") * 10)
)


def test_model_source_used_when_enabled(monkeypatch):
    """RUOYU_NN_SURPRISAL=1 且 bridge 命中 → 三类 ΔS 都标 source=model(用场景边界合规的大文本)。"""
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


def test_model_unavailable_keeps_heuristic_unchanged(monkeypatch):
    """bridge enabled 但返回全 None → 三类 ΔS 都回退 heuristic·数值与不开模型时逐位相等(零回归)。"""
    baseline = mod._scan_cluster(_BODY)  # 未碰模型 env 的现有频次代理基线
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: [None for _ in texts]
    )
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
    r = mod._scan_cluster(_BODY)
    assert r["source"]["delta_para"] == "heuristic"
    assert r["source"]["delta_scene"] == "heuristic"
    assert r["source"]["delta_cluster_end"] == "heuristic"
    assert r["delta_para"] == baseline["delta_para"]
    assert r["delta_scene"] == baseline["delta_scene"]
    assert r["delta_cluster_end"] == baseline["delta_cluster_end"]
    assert r["tokens"] == baseline["tokens"]


def test_model_disabled_by_default_no_env(monkeypatch):
    """默认(env 不开) → _predict_surprisal_batch 直接全 None·不触碰任何 sys.modules 假桥。"""
    monkeypatch.delenv("RUOYU_NN_SURPRISAL", raising=False)
    assert mod._predict_surprisal_batch(["测试文本"]) == [None]


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_shadow_exit_zero():
    proj = _mk_project({1: _BODY, 2: _BODY})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), "--last-n", "5"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr


def test_main_cli_off_skip():
    proj = _mk_project({1: _BODY})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    assert "SKIP" in r.stdout
