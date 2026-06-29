# 🔴 2026-06-29 可成长NN架构 · 特征仓库测试
"""test_feature_cache.py — FeatureStore 测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "ml" / "feature_store"))
from feature_cache import FeatureStore, _cache_key, enabled


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv("RUOYU_FEATURE_STORE", "1")
    FeatureStore.reset()
    import feature_cache as fc
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path / "cache")
    FeatureStore._instance = None
    yield
    FeatureStore._instance = None


def test_enabled_gate(monkeypatch):
    monkeypatch.delenv("RUOYU_FEATURE_STORE", raising=False)
    assert not enabled()
    monkeypatch.setenv("RUOYU_FEATURE_STORE", "1")
    assert enabled()


def test_singleton():
    a = FeatureStore.get()
    b = FeatureStore.get()
    assert a is b


def test_cache_key_deterministic():
    k1 = _cache_key("hello", "embedding")
    k2 = _cache_key("hello", "embedding")
    assert k1 == k2
    assert len(k1) == 16


def test_cache_key_varies_by_feature():
    k1 = _cache_key("hello", "embedding")
    k2 = _cache_key("hello", "surprisal")
    assert k1 != k2


def test_disk_cache_roundtrip(tmp_path):
    fs = FeatureStore.get()
    fs._cache_dir = tmp_path / "cache"
    fs._cache_dir.mkdir(parents=True, exist_ok=True)
    key = "test_key_12345"
    data = {"value": [1.0, 2.0, 3.0]}
    fs._put_cached(key, data)
    fs._mem_cache.clear()
    result = fs._get_cached(key)
    assert result == data


def test_mem_cache_hit(tmp_path):
    fs = FeatureStore.get()
    fs._cache_dir = tmp_path / "cache"
    fs._cache_dir.mkdir(parents=True, exist_ok=True)
    key = "mem_test"
    data = {"x": 42}
    fs._mem_cache[key] = data
    result = fs._get_cached(key)
    assert result == data
    assert fs._stats["hits"] >= 1


def test_cache_miss():
    fs = FeatureStore.get()
    result = fs._get_cached("nonexistent_key")
    assert result is None
    assert fs._stats["misses"] >= 1


def test_invalidate_all(tmp_path):
    fs = FeatureStore.get()
    fs._cache_dir = tmp_path / "cache"
    fs._cache_dir.mkdir(parents=True, exist_ok=True)
    fs._put_cached("k1", {"a": 1})
    fs._put_cached("k2", {"b": 2})
    evicted = fs.invalidate()
    assert evicted == 2
    assert len(fs._mem_cache) == 0


def test_get_stats():
    fs = FeatureStore.get()
    stats = fs.get_stats()
    assert "hits" in stats
    assert "misses" in stats
    assert "hit_rate" in stats
    assert "cache_files" in stats


def test_compute_embedding_empty():
    fs = FeatureStore.get()
    assert fs.compute_embedding("") is None
    assert fs.compute_embedding("   ") is None


def test_compute_surprisal_empty():
    fs = FeatureStore.get()
    assert fs.compute_surprisal("") is None


def test_compute_vad_empty():
    fs = FeatureStore.get()
    assert fs.compute_vad("") is None


def test_batch_compute_default_features():
    fs = FeatureStore.get()
    results = fs.batch_compute(["", ""], features=["vad"])
    assert len(results) == 2
    assert results[0]["vad"] is None


def test_enforce_size_limit(tmp_path):
    fs = FeatureStore.get()
    fs._cache_dir = tmp_path / "cache"
    fs._cache_dir.mkdir(parents=True, exist_ok=True)
    for i in range(10):
        (fs._cache_dir / f"test_{i}.json").write_text(
            json.dumps({"data": "x" * 1000}), encoding="utf-8"
        )
    import feature_cache as fc
    old_max = fc._MAX_CACHE_MB
    fc._MAX_CACHE_MB = 0
    try:
        evicted = fs.enforce_size_limit()
        assert evicted > 0
    finally:
        fc._MAX_CACHE_MB = old_max
