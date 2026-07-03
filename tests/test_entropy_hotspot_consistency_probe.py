# -*- coding: utf-8 -*-
"""entropy_hotspot_consistency_probe R20 W9 Batch-AA · P1 · 中段 entropy hotspot 探针
确定性·零依赖·零 LLM/零联网。**不产 issue 码** · 只写 probe 文件。
"""
import json
import os
import random
import string
import subprocess
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import entropy_hotspot_consistency_probe as mod  # noqa: E402

_TARGET = _SCRIPTS / "entropy_hotspot_consistency_probe.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ENTROPY_HOTSPOT_PROBE_MODE", None)
    else:
        os.environ["ENTROPY_HOTSPOT_PROBE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


# 构造中段高 entropy(随机) + 头尾低 entropy(单一字符) 的文本
def _make_middle_high_entropy(seed=42):
    random.seed(seed)
    low_repeat = "天" * 400
    pool = [chr(c) for c in range(0x4E00, 0x4E00 + 1000)]
    high_random = "".join(random.choices(pool, k=600))
    return low_repeat + high_random + low_repeat


def test_off_mode():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("off")
        out = mod.run(_write("text" * 1000), _mk_project())
        assert out["mode"] == "off"
        assert out["skipped"] == "mode=off"
    finally:
        _set_mode(bak)


def test_shadow_default_writes_probe():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        text = _make_middle_high_entropy()
        out = mod.run(_write(text), proj, "cluster_001")
        assert out["mode"] == "shadow"
        # probe 文件已写
        probe = proj / "_临时" / "probe" / "hotspot_cluster_001.json"
        assert probe.exists()
        data = json.loads(probe.read_text(encoding="utf-8"))
        assert data["probe"] == "entropy_hotspot_consistency"
        # 中段应捕到 hotspot
        assert len(data["hotspots"]) >= 1
    finally:
        _set_mode(bak)


def test_detect_hotspots_short_text_skipped():
    out = mod.detect_hotspots("天" * 100)
    assert out["hotspots"] == []
    assert "skipped" in out


def test_detect_hotspots_uniform_no_hotspot():
    # 全统一文字 → entropy 全相等 → std=0 → 无 hotspot
    text = "天" * 2000
    out = mod.detect_hotspots(text)
    assert out["hotspots"] == []
    assert out["blocks_total"] >= 4


def test_detect_hotspots_middle_high():
    text = _make_middle_high_entropy()
    out = mod.detect_hotspots(text)
    assert out["mean"] > 0
    assert out["std"] > 0
    # 中段位置 0.25-0.75 内应有至少 1 个 hotspot
    assert len(out["hotspots"]) >= 1
    for h in out["hotspots"]:
        assert 0.25 <= h["position"] <= 0.75
        assert h["z"] > 1.0


def test_block_entropy_basic():
    # 100% 相同字符 → entropy = 0
    assert mod._block_entropy("a" * 100) == 0.0
    # 两种字符均衡 → entropy ~= 1
    e = mod._block_entropy("ab" * 50)
    assert 0.99 <= e <= 1.01


def test_mean_std_basic():
    m, s = mod._mean_std([])
    assert m == 0.0 and s == 0.0
    m, s = mod._mean_std([2.0, 4.0])
    assert m == 3.0
    assert 0.99 <= s <= 1.01


def test_read_failure_returns_error():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        out = mod.run(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("error", "")
    finally:
        _set_mode(bak)


def test_no_issue_codes_emitted():
    """探针不产 issue 码·只调度 priority bump。"""
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        out = mod.run(_write(_make_middle_high_entropy()), _mk_project())
        # 不应有 violations / flags / code
        assert "violations" not in out
        assert "flags" not in out
        # placeholder 标记
        assert out["_placeholder"] is True
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_main_cli_returns_json():
    text = _make_middle_high_entropy()
    p = _write(text)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p), "--project", str(proj), "--cluster-id", "cluster_001"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ENTROPY_HOTSPOT_PROBE_MODE": "shadow",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["probe"] == "entropy_hotspot_consistency"


# ============ 🔴 2026-07-02 真模型(surprisal_gpt2) 接入回归 ============

def test_model_metric_used_when_enabled_and_placeholder_flips(monkeypatch):
    """RUOYU_NN_SURPRISAL=1 且 bridge 全 block 命中 → metric='model'·_placeholder 转 False。"""
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        # content-aware 假模型：surprisal 与 block 内唯一字符数挂钩(确定性·非常量)
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [
                {"mean_surprisal": len(set(t)) * 0.05, "source": "model"} for t in texts
            ]
        )
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        proj = _mk_project()
        text = _make_middle_high_entropy()
        out = mod.run(_write(text), proj, "cluster_model")
        assert out["metric"] == "model"
        assert out["_placeholder"] is False
        assert len(out["hotspots"]) >= 1
        for h in out["hotspots"]:
            assert 0.25 <= h["position"] <= 0.75
    finally:
        _set_mode(bak)


def test_model_unavailable_keeps_heuristic_unchanged(monkeypatch):
    """bridge enabled 但返回全 None → 整体回退字符熵·结果与不开模型时完全一致(零回归)。"""
    bak = os.environ.get("ENTROPY_HOTSPOT_PROBE_MODE")
    try:
        _set_mode("shadow")
        text = _make_middle_high_entropy()
        baseline = mod.run(_write(text), _mk_project(), "cluster_baseline")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        out = mod.run(_write(text), _mk_project(), "cluster_fallback")
        assert out["metric"] == "heuristic"
        assert out["_placeholder"] is True
        assert out["mean"] == baseline["mean"]
        assert out["std"] == baseline["std"]
        assert out["hotspots"] == baseline["hotspots"]
    finally:
        _set_mode(bak)


def test_block_metric_values_gate_off_falls_back_to_heuristic():
    """门控关(默认)·_block_metric_values 直接回退字符熵(不发起模型调用)。"""
    chunks = ["天" * 200, "天" * 200]
    values, metric = mod._block_metric_values(chunks)
    assert metric == "heuristic"
    assert values == [mod._block_entropy(c) for c in chunks]


def test_block_metric_values_partial_none_falls_back(monkeypatch):
    """bridge 部分命中/部分 None(混合) → 整体回退熵(避免部分 None 破坏 z-score 语义)。"""
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: (
            [{"mean_surprisal": 5.0, "source": "model"}] + [None] * (len(texts) - 1)))
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
    chunks = ["天" * 200, "地" * 200]
    values, metric = mod._block_metric_values(chunks)
    assert metric == "heuristic"
    assert values == [mod._block_entropy(c) for c in chunks]
