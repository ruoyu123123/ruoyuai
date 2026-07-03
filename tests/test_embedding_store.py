"""embedding_store 回归测试 — 守护 2026-05-30 真语义 dispatcher：
默认 hash 零回归（即使环境装了 sentence-transformers）+ opt-in 升级 + 维度混用安全。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import embedding_store as es


def test_default_backend_is_hash():
    """默认（未 opt-in）必须 hash 384 —— 即使环境装了 sentence-transformers（零回归核心）。"""
    es._BACKEND = None
    os.environ.pop("EMBED_BACKEND", None)
    try:
        assert es.embedding_method() == "hash"
        assert len(es.compute_embedding("测试文本一段")) == 384
    finally:
        es._BACKEND = None


def test_compute_embedding_degrades_on_failure():
    """后端抛异常 → hash 兜底（永不崩）。"""
    es._BACKEND = ("api:x", 1024, lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        assert len(es.compute_embedding("x")) == 384
    finally:
        es._BACKEND = None


def test_load_embed_profile():
    """GEN_EMBED__* .env 解析（cwd/.env 优先）。"""
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".env").write_text(
            "GEN_EMBED__qwen__BASE_URL=https://x/v1\nGEN_EMBED__qwen__API_KEY=sk-x\n"
            "GEN_EMBED__qwen__MODEL=text-embedding-v4\nGEN_EMBED__qwen__DIM=1024\nGEN_EMBED_ACTIVE=qwen\n",
            encoding="utf-8")
        os.chdir(d)
        try:
            prof = es._load_embed_profile()
        finally:
            os.chdir(orig)
    assert prof and prof["model"] == "text-embedding-v4" and prof["dim"] == 1024


def test_cosine_dim_mismatch_returns_zero():
    """维度不等 → cosine 返回 0（维度混用安全兜底，不崩）。"""
    assert es.cosine_similarity([1.0] * 384, [1.0] * 512) == 0.0


# ── 🔴 2026-07-03 Wave-4 缓存 + 批量 API ────────────────────────────────────

@pytest.fixture()
def _isolated_embed_cache(monkeypatch, tmp_path):
    """每用例独立磁盘缓存目录 + 清空内存缓存 + 重置后端探测缓存。"""
    monkeypatch.setenv("RUOYU_EMBED_CACHE_DIR", str(tmp_path / "embcache"))
    es._EMBED_MEM_CACHE.clear()
    es._BACKEND = None
    yield
    es._EMBED_MEM_CACHE.clear()
    es._BACKEND = None


def _fake_real_backend(counter, dim=8):
    """content-aware 假真后端：调用计数 + 按文本内容出向量。"""
    def fn(t):
        counter["calls"] += 1
        base = float(len(t or "") % 7 + 1)
        return [base / (i + 1) for i in range(dim)]
    return fn


def test_compute_embedding_caches_real_backend(_isolated_embed_cache):
    counter = {"calls": 0}
    es._BACKEND = ("fake:real", 8, _fake_real_backend(counter))
    v1 = es.compute_embedding("同一段文本")
    v2 = es.compute_embedding("同一段文本")
    assert v1 == v2 and counter["calls"] == 1  # 第二次纯缓存命中


def test_compute_embedding_failure_not_cached(_isolated_embed_cache):
    calls = {"calls": 0}
    def boom(t):
        calls["calls"] += 1
        raise RuntimeError("backend down")
    es._BACKEND = ("fake:real", 8, boom)
    v1 = es.compute_embedding("文本")
    assert len(v1) == 384          # hash 兜底
    es.compute_embedding("文本")
    assert calls["calls"] == 2     # 失败不缓存 → 第二次仍尝试后端（不被兜底向量污染）


def test_hash_backend_never_cached(_isolated_embed_cache, tmp_path):
    es._BACKEND = ("hash", 384, lambda t: es._stable_hash_embedding(t, 384))
    es.compute_embedding("哈希路径文本")
    assert not es._EMBED_MEM_CACHE  # hash 后端零缓存
    assert not list((tmp_path / "embcache").rglob("*.json"))


def test_batch_dedupes_and_single_backend_call(_isolated_embed_cache, monkeypatch):
    batch_calls = {"n": 0, "sizes": []}
    def fake_batch(texts, model="author", timeout=600):
        batch_calls["n"] += 1
        batch_calls["sizes"].append(len(texts))
        return [[float(len(t))] * 4 for t in texts]
    monkeypatch.setattr(es, "ruoyu_style_encode_batch", fake_batch)
    es._BACKEND = ("ruoyu_style:final", 4, es._ruoyu_style_embed)
    out = es.compute_embeddings_batch(["甲甲", "乙乙乙", "甲甲", "丙"])
    assert len(out) == 4 and out[0] == out[2]           # 对齐 + 重复项同向量
    assert batch_calls == {"n": 1, "sizes": [3]}        # 单次子进程·只编 3 条 unique
    # 第二轮整批命中缓存 → 后端零调用
    es.compute_embeddings_batch(["甲甲", "乙乙乙", "丙"])
    assert batch_calls["n"] == 1


def test_prefetch_then_single_calls_hit_cache(_isolated_embed_cache):
    counter = {"calls": 0}
    es._BACKEND = ("fake:real", 8, _fake_real_backend(counter))
    stats = es.prefetch_embeddings(["a 段", "b 段", "a 段"])
    assert stats["unique"] == 2 and stats["computed"] == 2
    before = counter["calls"]
    es.compute_embedding("a 段")
    es.compute_embedding("b 段")
    assert counter["calls"] == before  # prefetch 后逐条调用零后端成本


def test_disk_cache_survives_mem_clear(_isolated_embed_cache):
    counter = {"calls": 0}
    es._BACKEND = ("fake:real", 8, _fake_real_backend(counter))
    v1 = es.compute_embedding("跨进程持久文本")
    es._EMBED_MEM_CACHE.clear()      # 模拟新进程（内存缓存丢失）
    v2 = es.compute_embedding("跨进程持久文本")
    assert v1 == v2 and counter["calls"] == 1  # 磁盘命中·后端仍只调过一次


def test_cache_gate_off_disables_all(_isolated_embed_cache, monkeypatch):
    monkeypatch.setenv("RUOYU_EMBED_CACHE", "0")
    counter = {"calls": 0}
    es._BACKEND = ("fake:real", 8, _fake_real_backend(counter))
    es.compute_embedding("文本")
    es.compute_embedding("文本")
    assert counter["calls"] == 2 and not es._EMBED_MEM_CACHE


def test_batch_backend_failure_falls_back_hash_uncached(_isolated_embed_cache, monkeypatch):
    monkeypatch.setattr(es, "ruoyu_style_encode_batch",
                        lambda texts, model="author", timeout=600: None)
    es._BACKEND = ("ruoyu_style:final", 4, es._ruoyu_style_embed)
    out = es.compute_embeddings_batch(["失败文本"])
    assert len(out) == 1 and len(out[0]) == 384   # hash 兜底
    assert not es._EMBED_MEM_CACHE                # 兜底不入缓存


def test_api_embed_l2_normalized(monkeypatch):
    """API 后端返回裸向量必须 L2 归一 —— cosine_similarity 是纯点积，未归一会越界（2026-07-02 修）。"""
    class _FakeResp:
        class _D:
            embedding = [3.0, 4.0]  # 模长 5，未归一
        data = [_D()]

    class _FakeClient:
        def __init__(self, **kw):
            self.embeddings = self

        def create(self, **kw):
            return _FakeResp()

    import types
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeClient))
    vec = es._api_embed({"api_key": "k", "base_url": "https://x/v1", "model": "m"}, "文本")
    assert abs(vec[0] - 0.6) < 1e-9 and abs(vec[1] - 0.8) < 1e-9
    # 自身点积 = 1（归一化后 cosine(v,v)==1 不越界）
    assert abs(es.cosine_similarity(vec, vec) - 1.0) < 1e-9
