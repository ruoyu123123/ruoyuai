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
        if not enabled():
            self._stats["misses"] += 1
            return None
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
        if not enabled():
            return
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

    def compute_surprisal_batch(self, texts: list[str],
                                ids: list[str] | None = None) -> list[dict | None]:
        """批量计算 surprisal，未命中缓存的文本合并成一次 bridge 调用。"""
        n = len(texts)
        if n == 0:
            return []
        ids = ids if ids is not None and len(ids) == n else None
        out: list[dict | None] = [None] * n
        miss_i: list[int] = []
        miss_texts: list[str] = []
        miss_ids: list[str] = []
        miss_keys: list[str] = []
        version = os.environ.get(
            "RUOYU_SURPRISAL_MODEL", "uer/gpt2-chinese-cluecorpussmall")
        for i, text in enumerate(texts):
            if not str(text).strip():
                continue
            key = _cache_key(str(text), "surprisal", version)
            cached = self._get_cached(key)
            if cached is not None:
                val = dict(cached)
                if ids is not None:
                    val["id"] = ids[i]
                out[i] = val
                continue
            miss_i.append(i)
            miss_texts.append(str(text))
            miss_ids.append(ids[i] if ids is not None else f"para_{i:04d}")
            miss_keys.append(key)
        if not miss_texts:
            return out
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from nn_surprisal_bridge import predict_batch
            preds = predict_batch(miss_texts, ids=miss_ids)
            if len(preds) != len(miss_texts):
                self._stats["errors"] += len(miss_texts)
                return out
            for i, key, pred in zip(miss_i, miss_keys, preds):
                if pred is None:
                    continue
                cached = dict(pred)
                cached.pop("id", None)  # id 是本次请求属性，不进跨请求缓存
                self._put_cached(key, cached)
                val = dict(cached)
                if ids is not None:
                    val["id"] = ids[i]
                out[i] = val
            return out
        except Exception:
            self._stats["errors"] += len(miss_texts)
            return out

    def compute_surprisal(self, text: str) -> dict | None:
        if not text.strip():
            return None
        return self.compute_surprisal_batch([text])[0]

    def compute_vad_batch(self, texts: list[str]) -> list[dict | None]:
        """批量计算 VAD，未命中缓存的文本合并成一次 bridge 调用。"""
        n = len(texts)
        if n == 0:
            return []
        out: list[dict | None] = [None] * n
        miss_i: list[int] = []
        miss_texts: list[str] = []
        miss_keys: list[str] = []
        version = os.environ.get("RUOYU_VAD_CKPT", "va_base")
        for i, text in enumerate(texts):
            if not str(text).strip():
                continue
            key = _cache_key(str(text), "vad", version)
            cached = self._get_cached(key)
            if cached is not None:
                out[i] = dict(cached)
                continue
            miss_i.append(i)
            miss_texts.append(str(text))
            miss_keys.append(key)
        if not miss_texts:
            return out
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from nn_vad_bridge import predict_batch
            preds = predict_batch(miss_texts)
            if len(preds) != len(miss_texts):
                self._stats["errors"] += len(miss_texts)
                return out
            for i, key, pred in zip(miss_i, miss_keys, preds):
                if pred is None:
                    continue
                val = dict(pred)
                self._put_cached(key, val)
                out[i] = dict(val)
            return out
        except Exception:
            self._stats["errors"] += len(miss_texts)
            return out

    def compute_vad(self, text: str) -> dict | None:
        if not text.strip():
            return None
        return self.compute_vad_batch([text])[0]

    def compute_coherence_pairs(self, pairs: list[tuple[str, str]]) -> list[dict | None]:
        """批量计算相邻文本对连贯性，供 coherence_scanner 复用缓存。"""
        n = len(pairs)
        if n == 0:
            return []
        out: list[dict | None] = [None] * n
        miss_i: list[int] = []
        miss_pairs: list[tuple[str, str]] = []
        miss_keys: list[str] = []
        version = os.environ.get("RUOYU_COHERENCE_CKPT", "coherence_v1")
        for i, (a, b) in enumerate(pairs):
            a, b = str(a), str(b)
            if not a.strip() or not b.strip():
                continue
            key = _cache_key(f"{a}\n<<PAIR>>\n{b}", "coherence_pair", version)
            cached = self._get_cached(key)
            if cached is not None:
                out[i] = dict(cached)
                continue
            miss_i.append(i)
            miss_pairs.append((a, b))
            miss_keys.append(key)
        if not miss_pairs:
            return out
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from nn_coherence_bridge import predict_pairs
            preds = predict_pairs(miss_pairs)
            if len(preds) != len(miss_pairs):
                self._stats["errors"] += len(miss_pairs)
                return out
            for i, key, pred in zip(miss_i, miss_keys, preds):
                if pred is None:
                    continue
                val = dict(pred)
                self._put_cached(key, val)
                out[i] = dict(val)
            return out
        except Exception:
            self._stats["errors"] += len(miss_pairs)
            return out

    def batch_compute(self, texts: list[str],
                      features: list[str] | None = None,
                      ) -> list[dict]:
        if features is None:
            features = ["embedding", "surprisal", "vad"]
        results: list[dict] = [{} for _ in texts]
        if "embedding" in features:
            for i, text in enumerate(texts):
                results[i]["embedding"] = self.compute_embedding(text)
        if "surprisal" in features:
            vals = self.compute_surprisal_batch(texts)
            for i, val in enumerate(vals):
                results[i]["surprisal"] = val
        if "vad" in features:
            vals = self.compute_vad_batch(texts)
            for i, val in enumerate(vals):
                results[i]["vad"] = val
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
