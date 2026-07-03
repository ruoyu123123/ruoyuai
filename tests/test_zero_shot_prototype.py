# -*- coding: utf-8 -*-
"""zero_shot_prototype.py 专属回归测试(2026-07-03·W3 零样本原型分类工具)。

确定性 mock embedding(字符频率向量·同 test_topic_drift_scanner/
test_macguffin_entanglement_scanner 手法)·不依赖真模型/网络。
"""
import math
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import zero_shot_prototype as mod  # noqa: E402


def _char_freq_embedding(text: str, dim: int = 32) -> list:
    """确定性 mock embedding（字符频率向量·同 test_macguffin_entanglement_scanner 手法）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _char_freq_embedding_batch(texts, dim: int = 32) -> list:
    """_char_freq_embedding 的批量版（逐条同一算法·无跨文本交互，供 mock compute_embeddings_batch）。"""
    return [_char_freq_embedding(t, dim) for t in texts]


def _run_with_mock_embedding(fn, *args, **kwargs):
    """EMBED_BACKEND=mock + monkeypatch embedding_store.compute_embedding(_batch) 后跑 fn。

    🔴 2026-07-03 Wave-4：zero_shot_prototype 内部改走 compute_embeddings_batch，
    两个 mock 都要打（单条路径 classify() 也委托 classify_batch，实际只会用到 _batch 版，
    保留 compute_embedding mock 是为了兼容任何仍直接调用它的调用点/未来用途）。
    """
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig = embedding_store.compute_embedding
    orig_batch = embedding_store.compute_embeddings_batch
    embedding_store.compute_embedding = _char_freq_embedding
    embedding_store.compute_embeddings_batch = _char_freq_embedding_batch
    mod.clear_cache()
    try:
        return fn(*args, **kwargs)
    finally:
        embedding_store.compute_embedding = orig
        embedding_store.compute_embeddings_batch = orig_batch
        mod.clear_cache()
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


# 刻意选高辨识度、类间零字符重叠的例句，避免 32 维 hash bucket 偶然碰撞导致误判
_PROTOTYPES = {
    "laughter": ["哈哈哈哈太好笑了", "笑得停不下来真好笑", "扑哧一声笑出声"],
    "shock": ["震惊愕然说不出话", "难以置信呆住了", "震惊得瞳孔一缩"],
}


# ── 门控 ─────────────────────────────────────────────
def test_gate_off_returns_none_by_default():
    bak = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
        assert mod.classify("她笑了", _PROTOTYPES) is None
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        for k, v in saved.items():
            os.environ[k] = v


def test_gate_hash_explicit_still_false():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "hash"
        assert mod._has_real_embedding_backend() is False
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_gate_true_with_gen_embed_env():
    bak = os.environ.pop("EMBED_BACKEND", None)
    os.environ["GEN_EMBED__X__API_KEY"] = "k"
    try:
        assert mod._has_real_embedding_backend() is True
    finally:
        os.environ.pop("GEN_EMBED__X__API_KEY", None)
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak


def test_gate_true_with_non_hash_backend():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "mstyle"
        assert mod._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


# ── 分类正确性 + margin ──────────────────────────────
def test_classify_correct_label_and_margin():
    def _do():
        result = mod.classify("哈哈哈笑得停不下来", _PROTOTYPES, floor=0.0)
        assert result is not None
        assert result["label"] == "laughter"
        assert result["source"] == "zero_shot_embedding"
        assert -1.0 - 1e-9 <= result["score"] <= 1.0 + 1e-9
        assert result["margin"] >= 0.0
    _run_with_mock_embedding(_do)


def test_classify_other_label():
    def _do():
        result = mod.classify("震惊愕然呆住了", _PROTOTYPES, floor=0.0)
        assert result is not None
        assert result["label"] == "shock"
    _run_with_mock_embedding(_do)


# ── floor 拦截 ───────────────────────────────────────
def test_floor_blocks_low_confidence():
    def _do():
        # floor 设成余弦相似度理论上限之上 → 无论分类结果如何必被拦截
        result = mod.classify("哈哈哈笑得停不下来", _PROTOTYPES, floor=1.01)
        assert result is None
    _run_with_mock_embedding(_do)


def test_floor_default_allows_high_confidence_self_match():
    def _do():
        # 待分类文本与某类例句完全同字 → 相似度接近 1，默认 floor=0.5 应放行
        result = mod.classify("哈哈哈哈太好笑了", _PROTOTYPES)
        assert result is not None
        assert result["label"] == "laughter"
    _run_with_mock_embedding(_do)


# ── 质心缓存 ─────────────────────────────────────────
def test_centroid_cache_reused_for_same_prototypes():
    """🔴 2026-07-03 Wave-4：classify() 内部改走 compute_embeddings_batch，改为按批调用计数。"""
    calls = []

    def _counting_batch(texts):
        calls.append(list(texts))
        return _char_freq_embedding_batch(texts)

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _counting_batch
        mod.classify("哈哈哈真好笑", _PROTOTYPES, floor=0.0)
        prototype_example_count = sum(len(v) for v in _PROTOTYPES.values())
        # 首次：例句 + 1 条待分类文本合成一次批调用（Wave-4 冷启动只占一次后端往返）
        assert len(calls) == 1
        assert len(calls[0]) == prototype_example_count + 1
        mod.classify("笑死我了哈哈", _PROTOTYPES, floor=0.0)
        # 二次：质心命中缓存，只批量 embed 待分类文本（1 条）
        assert len(calls) == 2
        assert len(calls[1]) == 1

    _run_with_mock_embedding(_do)


def test_cache_key_differs_by_prototypes_content():
    def _do():
        r1 = mod.classify("哈哈哈真好笑", _PROTOTYPES, floor=0.0)
        assert r1 is not None
        other = {"objectsx": ["案上摆着一只旧匣子"], "parallelx": ["街角另一头有人说话"]}
        r2 = mod.classify("案上摆着一只旧匣子本身", other, floor=0.0)
        assert r2 is not None
        assert r2["label"] in ("objectsx", "parallelx")
    _run_with_mock_embedding(_do)


def test_clear_cache_forces_rebuild():
    """🔴 2026-07-03 Wave-4：改为按批调用累计 embed 文本数计数。"""
    calls = []

    def _counting_batch(texts):
        calls.append(list(texts))
        return _char_freq_embedding_batch(texts)

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _counting_batch
        mod.classify("哈哈哈真好笑", _PROTOTYPES, floor=0.0)
        n1 = sum(len(c) for c in calls)
        mod.clear_cache()
        mod.classify("哈哈哈真好笑", _PROTOTYPES, floor=0.0)
        n2 = sum(len(c) for c in calls)
        # clear_cache 后质心应重新 embed（不是只 embed 待分类文本那 1 次）
        assert n2 > n1 + 1

    _run_with_mock_embedding(_do)


# ── 异常/边界 ────────────────────────────────────────
def test_empty_text_returns_none():
    def _do():
        assert mod.classify("", _PROTOTYPES) is None
    _run_with_mock_embedding(_do)


def test_empty_prototypes_returns_none():
    def _do():
        assert mod.classify("文本", {}) is None
    _run_with_mock_embedding(_do)


def test_compute_embedding_exception_returns_none():
    """🔴 2026-07-03 Wave-4：classify() 内部改走 compute_embeddings_batch，异常源随之改。"""
    def _boom(texts):
        raise RuntimeError("embed fail")

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _boom
        assert mod.classify("文本", _PROTOTYPES) is None

    _run_with_mock_embedding(_do)


def test_single_label_prototypes_margin_equals_score():
    def _do():
        single = {"only": ["唯一类别的例句在这里"]}
        result = mod.classify("唯一类别的例句在这里本身", single, floor=0.0)
        assert result is not None
        assert result["margin"] == result["score"]
    _run_with_mock_embedding(_do)


def test_label_with_no_examples_skipped():
    def _do():
        protos = dict(_PROTOTYPES)
        protos["empty_label"] = []
        result = mod.classify("哈哈哈真好笑", protos, floor=0.0)
        assert result is not None
        assert result["label"] != "empty_label"
    _run_with_mock_embedding(_do)


# ── 🔴 2026-07-03 Wave-4 性能层：classify_batch 专属回归 ─────────────────────
def test_classify_batch_gate_off_returns_all_none():
    bak = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        results = mod.classify_batch(["她笑了", "他哭了", "真震惊"], _PROTOTYPES)
        assert results == [None, None, None]
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        for k, v in saved.items():
            os.environ[k] = v


def test_classify_batch_empty_texts_returns_empty_list():
    def _do():
        assert mod.classify_batch([], _PROTOTYPES) == []
    _run_with_mock_embedding(_do)


def test_classify_batch_empty_prototypes_returns_none_list():
    def _do():
        results = mod.classify_batch(["哈哈哈", "震惊"], {})
        assert results == [None, None]
    _run_with_mock_embedding(_do)


def test_classify_batch_returns_list_aligned_with_input():
    def _do():
        texts = ["哈哈哈笑得停不下来", "震惊愕然呆住了", "扑哧一声笑出声"]
        results = mod.classify_batch(texts, _PROTOTYPES, floor=0.0)
        assert len(results) == len(texts)
        assert [r["label"] for r in results] == ["laughter", "shock", "laughter"]
    _run_with_mock_embedding(_do)


def test_classify_batch_matches_classify_per_item():
    """batch 逐条结果须与单条 classify() 数学上逐字节一致（只是 embed 方式变了）。"""
    def _do():
        texts = ["哈哈哈笑得停不下来", "震惊愕然呆住了"]
        batch_results = mod.classify_batch(texts, _PROTOTYPES, floor=0.0)
        mod.clear_cache()
        single_results = [mod.classify(t, _PROTOTYPES, floor=0.0) for t in texts]
        assert batch_results == single_results
    _run_with_mock_embedding(_do)


def test_classify_batch_single_backend_call_for_cold_start():
    """🔴 Wave-4 核心契约：质心缓存冷启动时，例句+全部待分类文本合成一次批调用。"""
    calls = []

    def _counting_batch(texts):
        calls.append(list(texts))
        return _char_freq_embedding_batch(texts)

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _counting_batch
        texts = ["哈哈哈笑得停不下来", "震惊愕然呆住了", "扑哧一声笑出声"]
        results = mod.classify_batch(texts, _PROTOTYPES, floor=0.0)
        assert all(r is not None for r in results)
        prototype_example_count = sum(len(v) for v in _PROTOTYPES.values())
        # 全部候选文本一次 scan 只触发一次批调用（不是逐条 N 次）
        assert len(calls) == 1
        assert len(calls[0]) == prototype_example_count + len(texts)
        assert set(calls[0]) == set(sum(_PROTOTYPES.values(), []) + texts)

    _run_with_mock_embedding(_do)


def test_classify_batch_single_backend_call_when_centroid_warm():
    """质心已缓存（例如上一次 scan 已建过）→ 后续整批只需 1 次批调用嵌入待分类文本。"""
    calls = []

    def _counting_batch(texts):
        calls.append(list(texts))
        return _char_freq_embedding_batch(texts)

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _counting_batch
        mod.classify_batch(["先热身一条"], _PROTOTYPES, floor=0.0)  # 建质心
        calls.clear()
        texts = ["哈哈哈笑得停不下来", "震惊愕然呆住了", "扑哧一声笑出声", "还有一条"]
        results = mod.classify_batch(texts, _PROTOTYPES, floor=0.0)
        assert len(results) == len(texts)
        assert len(calls) == 1
        assert len(calls[0]) == len(texts)

    _run_with_mock_embedding(_do)


def test_classify_batch_exception_returns_all_none():
    def _boom(texts):
        raise RuntimeError("batch embed fail")

    def _do():
        import embedding_store
        embedding_store.compute_embeddings_batch = _boom
        results = mod.classify_batch(["文本1", "文本2"], _PROTOTYPES)
        assert results == [None, None]

    _run_with_mock_embedding(_do)


def test_classify_batch_floor_blocks_low_confidence():
    def _do():
        results = mod.classify_batch(["哈哈哈笑得停不下来"], _PROTOTYPES, floor=1.01)
        assert results == [None]
    _run_with_mock_embedding(_do)
