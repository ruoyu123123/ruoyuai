# 🔴 2026-06-29 可成长NN架构 · 特征仓库
"""feature_cache.py — 统一特征计算 + LRU 磁盘缓存。

核心思想: embedding/surprisal/VAD 计算一次、多处消费，避免重复推理。
缓存粒度: 段落级（~50-500 字）
缓存策略: sha256(text[:2000]+model_version)[:16] → JSON 文件
env 门控: RUOYU_FEATURE_STORE=1（默认 off）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_MANIFEST = Path(__file__).resolve().parent / "feature_manifest.json"
_MAX_CACHE_MB = int(os.environ.get("RUOYU_FEATURE_CACHE_MB", "500"))


def enabled() -> bool:
    return os.environ.get("RUOYU_FEATURE_STORE") == "1"


def _cache_key(text: str, feature: str, version: str = "v1") -> str:
    raw = f"{text[:2000]}|{feature}|{version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class FeatureStore:
    """单例特征仓库：缓存 + 委托各 bridge 计算。"""

    _instance: FeatureStore | None = None

    def __init__(self):
        self._cache_dir = _CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem_cache: dict[str, dict] = {}
        self._stats = {"hits": 0, "misses": 0, "errors": 0}

    @classmethod
    def get(cls) -> FeatureStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _disk_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _get_cached(self, key: str) -> dict | None:
        if key in self._mem_cache:
            self._stats["hits"] += 1
            return self._mem_cache[key]
        p = self._disk_path(key)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self._mem_cache[key] = data
                self._stats["hits"] += 1
                return data
            except (json.JSONDecodeError, OSError):
                pass
        self._stats["misses"] += 1
        return None

    def _put_cached(self, key: str, value: dict) -> None:
        self._mem_cache[key] = value
        p = self._disk_path(key)
        try:
            p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def compute_embedding(self, text: str, model: str = "default") -> list | None:
        if not text.strip():
            return None
        key = _cache_key(text, f"embedding_{model}")
        cached = self._get_cached(key)
        if cached is not None:
            return cached.get("embedding")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from embedding_store import compute_embedding
            emb = compute_embedding(text)
            if emb is not None:
                self._put_cached(key, {"embedding": emb})
            return emb
        except Exception:
            self._stats["errors"] += 1
            return None

    def compute_surprisal(self, text: str) -> dict | None:
        if not text.strip():
            return None
        key = _cache_key(text, "surprisal")
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from nn_surprisal_bridge import predict_one
            result = predict_one(text)
            if result is not None:
                self._put_cached(key, result)
            return result
        except Exception:
            self._stats["errors"] += 1
            return None

    def compute_vad(self, text: str) -> dict | None:
        if not text.strip():
            return None
        key = _cache_key(text, "vad")
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from nn_vad_bridge import predict_one
            result = predict_one(text)
            if result is not None:
                self._put_cached(key, result)
            return result
        except Exception:
            self._stats["errors"] += 1
            return None

    def batch_compute(self, texts: list[str],
                      features: list[str] | None = None,
                      ) -> list[dict]:
        if features is None:
            features = ["embedding", "surprisal", "vad"]
        results: list[dict] = [{} for _ in texts]
        dispatch = {
            "embedding": self.compute_embedding,
            "surprisal": self.compute_surprisal,
            "vad": self.compute_vad,
        }
        for feat in features:
            fn = dispatch.get(feat)
            if fn is None:
                continue
            for i, text in enumerate(texts):
                results[i][feat] = fn(text)
        return results

    def invalidate(self, model: str | None = None) -> int:
        count = 0
        if model is None:
            for p in self._cache_dir.glob("*.json"):
                try:
                    p.unlink()
                    count += 1
                except OSError:
                    pass
            self._mem_cache.clear()
        else:
            keys_to_remove = [
                k for k in self._mem_cache
                if model in str(k)
            ]
            for k in keys_to_remove:
                self._mem_cache.pop(k, None)
                p = self._disk_path(k)
                if p.exists():
                    try:
                        p.unlink()
                        count += 1
                    except OSError:
                        pass
        return count

    def enforce_size_limit(self) -> int:
        max_bytes = _MAX_CACHE_MB * 1024 * 1024
        files = list(self._cache_dir.glob("*.json"))
        if not files:
            return 0
        total = sum(f.stat().st_size for f in files)
        if total <= max_bytes:
            return 0
        files.sort(key=lambda f: f.stat().st_atime)
        evicted = 0
        while total > max_bytes * 0.8 and files:
            f = files.pop(0)
            try:
                total -= f.stat().st_size
                f.unlink()
                evicted += 1
            except OSError:
                pass
        return evicted

    def get_stats(self) -> dict:
        total = self._stats["hits"] + self._stats["misses"]
        return {
            **self._stats,
            "hit_rate": round(self._stats["hits"] / max(1, total), 3),
            "cache_files": len(list(self._cache_dir.glob("*.json"))),
            "mem_cache_size": len(self._mem_cache),
        }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fs = FeatureStore.get()
    print(json.dumps(fs.get_stats(), indent=2))
    print("Feature store ready. Set RUOYU_FEATURE_STORE=1 to enable caching.")


if __name__ == "__main__":
    main()
