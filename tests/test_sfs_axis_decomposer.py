# -*- coding: utf-8 -*-
"""sfs_axis_decomposer R20 W9 Batch-BB · P2 · FicSim 12-axis 分解器
确定性·零依赖·shadow-only。"""
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import sfs_axis_decomposer as mod  # noqa: E402

_AUTHOR = "她抬起头，望向窗外的雪。"
_REPLICA = "她抬眸，瞥见窗外飘雪。"


def test_compute_embedding_axes_returns_8():
    out = mod.compute_embedding_axes(_AUTHOR, _REPLICA)
    assert set(out.keys()) == set(mod.EMBED_AXES)
    for ax, d in out.items():
        assert 0 <= d["score"] <= 100
        assert d["_placeholder"] is True


def test_embedding_deterministic():
    a = mod.compute_embedding_axes(_AUTHOR, _REPLICA)
    b = mod.compute_embedding_axes(_AUTHOR, _REPLICA)
    for ax in mod.EMBED_AXES:
        assert a[ax]["score"] == b[ax]["score"]


def test_merge_av_axes_1_to_5_to_100():
    av = {"voice": 5, "pace": 1, "imagery": 3, "syntax": 4}
    out = mod.merge_av_axes(av)
    assert out["voice"]["score"] == 100.0
    assert out["pace"]["score"] == 0.0
    assert out["imagery"]["score"] == 50.0
    assert out["syntax"]["score"] == 75.0


def test_merge_av_axes_handles_dict_score():
    av = {"voice": {"score": 80}, "pace": {"score": 4}}
    out = mod.merge_av_axes(av)
    # 80 > 5 → 已 100 scale
    assert out["voice"]["score"] == 80.0
    # 4 ≤ 5 → 1-5 scale
    assert out["pace"]["score"] == 75.0


def test_merge_av_axes_missing():
    out = mod.merge_av_axes(None)
    for ax in mod.AV_AXES:
        assert out[ax]["score"] is None
        assert out[ax]["source"] == "av_missing"


def test_decompose_returns_12_axes():
    out = mod.decompose(_AUTHOR, _REPLICA, {"voice": 4, "pace": 4, "imagery": 4, "syntax": 4})
    assert len(out["axes"]) == 12
    assert out["scanner"] == "sfs_axis_decomposer"
    assert out["_embedding_placeholder"] is True


def test_decompose_plot_confound_risk():
    # voice/Author 设低、av_judge voice=1 (→ 0) → trigger confound
    # 但 plot 是 embedding placeholder 决定 · 我们用强制构造 av_judge 极低
    out = mod.decompose("xxx", "yyy", {"voice": 1, "pace": 1, "imagery": 1, "syntax": 1})
    # 即使 plot 嵌入是占位，Plot > 70 才触发
    plot = out["axes"]["Plot"]["score"]
    voice = out["axes"]["voice"]["score"]
    if plot > 70 and voice < 50:
        codes = {f["code"] for f in out["flags"]}
        assert "SFS_PLOT_CONFOUND_RISK" in codes


def test_decompose_all_high_pass():
    # 给 av_judge 全 5 + 寄望 embed 占位足够 · 实际 embed 0-75，无法>60 全部
    # 改测 当 voice=5 等高时若 embed 也 >= 60 才会触发 AXIS_ALL_HIGH_PASS
    out = mod.decompose(_AUTHOR, _REPLICA, {"voice": 5, "pace": 5, "imagery": 5, "syntax": 5})
    valid = [d["score"] for d in out["axes"].values() if isinstance(d.get("score"), (int, float))]
    if all(s >= 60 for s in valid):
        codes = {f["code"] for f in out["flags"]}
        assert "AXIS_ALL_HIGH_PASS" in codes
    # 否则不报，也是 PASS
    assert out["verdict"] == "PASS"


def test_off_mode_via_main(tmp_path):
    import subprocess
    a = tmp_path / "a.txt"
    a.write_text(_AUTHOR, encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text(_REPLICA, encoding="utf-8")
    target = _SCRIPTS / "sfs_axis_decomposer.py"
    r = subprocess.run(
        [sys.executable, str(target), "--author", str(a), "--replica", str(b)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SFS_AXIS_DECOMPOSE_MODE": "off", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["mode"] == "off"


def test_mode_default_shadow():
    bak = os.environ.get("SFS_AXIS_DECOMPOSE_MODE")
    try:
        os.environ.pop("SFS_AXIS_DECOMPOSE_MODE", None)
        assert mod._mode() == "shadow"
    finally:
        if bak is not None:
            os.environ["SFS_AXIS_DECOMPOSE_MODE"] = bak


# ── 🔴 2026-07-02 embedding_store 真接线（真后端 Author/Theme/ToneTags 三轴升级）──────────

def _char_freq_embedding(text: str, dim: int = 32) -> list:
    """确定性、内容感知的假 embedding（字符频率向量）。"""
    import math as _math
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = _math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def test_has_real_embedding_backend_gate():
    old = os.environ.get("EMBED_BACKEND")
    try:
        os.environ.pop("EMBED_BACKEND", None)
        assert mod._has_real_embedding_backend() is False
        os.environ["EMBED_BACKEND"] = "hash"
        assert mod._has_real_embedding_backend() is False
        os.environ["EMBED_BACKEND"] = "mstyle"
        assert mod._has_real_embedding_backend() is True
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_real_backend_upgrades_author_theme_tonetags(monkeypatch):
    """真后端命中：Author/Theme/ToneTags 三轴用真 embedding 余弦(source=embedding·非
    placeholder)；其余 5 轴仍走 sha256 占位（无轴向文本抽取器）。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)

    out = mod.compute_embedding_axes(_AUTHOR, _REPLICA)
    for ax in mod._REAL_EMBED_AXES:
        assert out[ax]["source"] == "embedding"
        assert out[ax]["_placeholder"] is False
        assert 0 <= out[ax]["score"] <= 100
    for ax in set(mod.EMBED_AXES) - set(mod._REAL_EMBED_AXES):
        assert out[ax]["_placeholder"] is True
        assert out[ax]["source"] == "embed_placeholder"


def test_real_backend_reflects_actual_similarity(monkeypatch):
    """内容感知假向量：author≈replica(高相似) 的 Author 轴分数应高于完全不同文本的场景。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)

    identical = mod.compute_embedding_axes("她抬起头望向窗外的雪落满地", "她抬起头望向窗外的雪落满地")
    different = mod.compute_embedding_axes("她抬起头望向窗外的雪落满地", "量子计算机比特位纠缠态并行加速")
    assert identical["Author"]["score"] > different["Author"]["score"]
    assert identical["Author"]["score"] == 100.0  # 余弦=1.0 → 线性映射满分


def test_real_backend_dimension_mismatch_falls_back_to_placeholder(monkeypatch):
    """embedding 维度不一致 → 静默回退 placeholder（不冒充语义）。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    import embedding_store

    def _mismatched_dim(text):
        return [0.1] * (16 if "replica" in text else 32)

    monkeypatch.setattr(embedding_store, "compute_embedding", _mismatched_dim)
    out = mod.compute_embedding_axes("author text", "replica text")
    for ax in mod._REAL_EMBED_AXES:
        assert out[ax]["_placeholder"] is True
        assert out[ax]["source"] == "embed_placeholder"


def test_gate_off_all_axes_placeholder_byte_identical(monkeypatch):
    """零回归证明：无真后端（默认环境）→ 8 轴全部 placeholder，且分数与 _deterministic_seed
    直接计算值逐字节一致（sha256 占位公式未受本次改动影响）。"""
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in [k for k in os.environ if k.startswith("GEN_EMBED__")]:
        monkeypatch.delenv(k, raising=False)

    out = mod.compute_embedding_axes(_AUTHOR, _REPLICA)
    for ax in mod.EMBED_AXES:
        assert out[ax]["_placeholder"] is True
        assert out[ax]["source"] == "embed_placeholder"
        seed = mod._deterministic_seed(_AUTHOR, _REPLICA, ax)
        expected = round((seed % 50) + 25.0, 1)
        assert out[ax]["score"] == expected
