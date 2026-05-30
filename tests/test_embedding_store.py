"""embedding_store 回归测试 — 守护 2026-05-30 真语义 dispatcher：
默认 hash 零回归（即使环境装了 sentence-transformers）+ opt-in 升级 + 维度混用安全。
"""
import os
import sys
import tempfile
from pathlib import Path

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
