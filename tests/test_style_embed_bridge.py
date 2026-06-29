"""🔴 2026-06-29 NN风格声纹集成 — embedding_store ruoyu_style 桥 + style_embed_sfs 测试。

核心守护（北极星⑤ + 零回归铁律）：
  · 默认安全：venv/模型/torch 缺 / 桥失败 → 返回 None / available=False·**绝不崩**·调用方回退。
  · 零回归：EMBED_BACKEND 未设 → 仍 hash 384（系统 py3.14 无 torch 也跑）。
  · 真桥（RUOYU_RUN_REAL_MODEL=1 门控·默认 skip）：venv 在 → 验编码返 768 维 embedding。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "scripts"))
import embedding_store as es  # noqa: E402
import style_embed_sfs as ses  # noqa: E402


# ───────────────────────── 零回归：默认仍 hash ─────────────────────────
def test_default_backend_unchanged_no_torch():
    """EMBED_BACKEND 未设 → hash 384（即使 ruoyu_style 桥代码已加入·零回归）。"""
    es._BACKEND = None
    os.environ.pop("EMBED_BACKEND", None)
    try:
        assert es.embedding_method() == "hash"
        assert len(es.compute_embedding("一段测试文字而已")) == 384
    finally:
        es._BACKEND = None


# ───────────────────────── 默认安全：桥不可用回退 ─────────────────────────
def test_encode_batch_none_when_venv_missing(monkeypatch):
    """venv 缺 → ruoyu_style_encode_batch 返回 None（调用方回退·不崩）。"""
    monkeypatch.setattr(es, "_ruoyu_venv_python", lambda: None)
    assert es.ruoyu_style_encode_batch(["甲", "乙"], model="author") is None


def test_encode_batch_none_when_model_missing(monkeypatch):
    """模型目录缺 → 返回 None。"""
    monkeypatch.setattr(es, "_ruoyu_venv_python", lambda: Path(sys.executable))
    monkeypatch.setattr(es, "_ruoyu_model_path", lambda model="author": None)
    assert es.ruoyu_style_encode_batch(["甲"], model="author") is None


def test_encode_batch_empty_returns_empty():
    """空输入 → 空列表（不启 subprocess）。"""
    assert es.ruoyu_style_encode_batch([], model="author") == []


def test_ruoyu_style_backend_falls_back_to_hash_when_venv_missing(monkeypatch):
    """EMBED_BACKEND=ruoyu_style 但 venv 缺 → _detect_backend 降级 hash 384（dim 一致·零崩）。"""
    es._BACKEND = None
    monkeypatch.setenv("EMBED_BACKEND", "ruoyu_style")
    monkeypatch.setattr(es, "_ruoyu_venv_python", lambda: None)
    try:
        assert es.embedding_method() == "hash"
        assert len(es.compute_embedding("风格声纹测试文本")) == 384
    finally:
        es._BACKEND = None


def test_backend_fn_raises_then_compute_embedding_hash_fallback():
    """ruoyu_style 后端 fn 运行期抛（桥不可用）→ compute_embedding 兜底 hash 384（永不崩）。"""
    es._BACKEND = ("ruoyu_style:final", 768,
                   lambda t: (_ for _ in ()).throw(RuntimeError("bridge down")))
    try:
        v = es.compute_embedding("x")
        assert len(v) == 384  # 兜底 hash·非 768
    finally:
        es._BACKEND = None


# ───────────────────────── 元信息读取（纯 stdlib·无 torch）─────────────────────────
def test_ruoyu_style_dim_reads_meta_or_none():
    """ruoyu_meta.json 在 → 返回 768；模型缺 → None（不加载 torch）。"""
    es._RUOYU_DIM_CACHE.clear()
    dim = es.ruoyu_style_dim("author")
    # 仓里训练产物在 → 768；若产物被清 → None（都不许崩）。
    assert dim in (768, None)
    if dim is not None:
        assert es.ruoyu_style_dim("character") in (768, None)


def test_ruoyu_style_available_logic(monkeypatch):
    """三者俱在 → True；任一缺 → False。"""
    monkeypatch.setattr(es, "_ruoyu_venv_python", lambda: None)
    assert es.ruoyu_style_available("author") is False


# ───────────────────────── style_embed_sfs 默认安全 ─────────────────────────
def test_embedding_sfs_unavailable_graceful(monkeypatch):
    """桥不可用 → available=False·embedding_sfs=None·gate_level=advisory·不抛。"""
    monkeypatch.setattr(es, "ruoyu_style_available", lambda model="author": False)
    r = ses.compute_embedding_sfs("生成的一段文字" * 50, ref_texts=["原文一段" * 50])
    assert r["available"] is False
    assert r["embedding_sfs"] is None
    assert r["gate_level"] == "advisory"
    assert "reason" in r


def test_embedding_sfs_no_ref_graceful(monkeypatch):
    """有桥但没给 ref/author → available=False（不崩）。"""
    monkeypatch.setattr(es, "ruoyu_style_available", lambda model="author": True)
    r = ses.compute_embedding_sfs("文" * 600, ref_texts=None, author=None)
    assert r["available"] is False
    assert "reason" in r


def test_chunker_deterministic():
    """分块协议确定性 + 非空（复用 data_prep·与训练一致）。"""
    text = "。".join(["这是第%d段话用来测试分块协议的稳定性它需要足够长" % i for i in range(80)])
    a = ses._chunks(text)
    b = ses._chunks(text)
    assert a == b
    assert len(a) >= 1


def test_centroid_and_cosine():
    """归一化 centroid + cosine 基本性质。"""
    c = ses._centroid([[3.0, 0.0], [0.0, 4.0]])
    assert abs(sum(x * x for x in c) - 1.0) < 1e-6  # 已归一化
    assert abs(ses._cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(ses._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


# ───────────────────────── 真桥集成（env 门控·默认 skip）─────────────────────────
@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_MODEL") != "1",
                    reason="真模型桥测试·需 venv+torch·设 RUOYU_RUN_REAL_MODEL=1 开启")
def test_real_bridge_encode_author():
    """venv 在 → 真编码返回 N×768 embedding（确定性：同输入两次一致）。"""
    es._RUOYU_DIM_CACHE.clear()
    assert es.ruoyu_style_available("author"), "venv/模型/infer 任一缺·无法跑真桥"
    texts = ["他独自走在雨里，影子被路灯拉得很长。",
             "城市像一头沉睡的兽，霓虹在水洼里碎成一片片。"]
    e1 = es.ruoyu_style_encode_batch(texts, model="author")
    assert e1 is not None and len(e1) == 2 and len(e1[0]) == 768
    e2 = es.ruoyu_style_encode_batch(texts, model="author")
    assert e1 == e2  # 确定性（eval+normalize+固定 batch+round6）


@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_MODEL") != "1",
                    reason="真模型桥测试·需 venv+torch·设 RUOYU_RUN_REAL_MODEL=1 开启")
def test_real_bridge_embedding_sfs_in_range():
    """真桥 embedding-SFS ∈ [-1, 1]·available=True。"""
    gen = "他握紧手里的剑，知道这一战躲不过去了。" * 40
    ref = ["少年抬头看向远处的山，眼里有从未熄灭的光。" * 40]
    r = ses.compute_embedding_sfs(gen, ref_texts=ref)
    assert r["available"] is True
    assert -1.0 <= r["embedding_sfs"] <= 1.0
