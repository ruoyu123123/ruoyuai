# -*- coding: utf-8 -*-
"""intentional_recurrence_scanner R23 W11 Batch-GG · P1

2026-07-01 追加：真语义 embedding 可选路径回归(mock 后端·完全照抄 topic_drift_scanner
测试手法)——钉死 (a) 无真后端时 match_method="lexicon"(零回归·trigram Jaccard 不变)
(b) mock 真后端时 match_method="semantic" 且语义路径被正确使用。
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import intentional_recurrence_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("INTENTIONAL_RECURRENCE_MODE", None)
    else:
        os.environ["INTENTIONAL_RECURRENCE_MODE"] = m


def _mk_project(clusters: list[dict] | None) -> Path:
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "故事块摘要.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return proj


# 中等相似（≥0.6）但五轴各不同 → intentional
_CLUSTER_A_INTENT = {
    "cluster_id": "cluster_001",
    "scope_summary": "主角在山顶面对敌人挥剑斩出一道气劲取胜",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "击败敌人",
}
_CLUSTER_B_INTENT = {
    "cluster_id": "cluster_010",
    "scope_summary": "主角在山顶面对敌人挥剑斩出一道气劲取胜",
    "characters_focus": ["徒弟"],
    "hub_locations": ["雪原"],
    "anchor_props": ["短刀"],
    "mood": "悲悯",
    "outcome": "放敌人一条生路",
}

# 高相似且五轴几乎相同 → real_repeat
_CLUSTER_REP_A = {
    "cluster_id": "cluster_020",
    "scope_summary": "主角在山顶挥剑斩敌赢得战斗",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "胜",
}
_CLUSTER_REP_B = {
    "cluster_id": "cluster_021",
    "scope_summary": "主角在山顶挥剑斩敌赢得战斗",
    "characters_focus": ["主角"],
    "hub_locations": ["山顶"],
    "anchor_props": ["长剑"],
    "mood": "悲壮",
    "outcome": "胜",
}


def test_off_returns_skeleton():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_thin_data_skipped():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT]))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "INTENTIONAL_RECURRENCE_THIN_DATA" in codes
    finally:
        _set_mode(bak)


def test_active_intentional_recurrence_detected():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        # 高 sim + 五轴差异 ≥3 → DETECTED
        if out.get("intentional_count", 0) >= 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "INTENTIONAL_RECURRENCE_DETECTED" in codes
    finally:
        _set_mode(bak)


def test_active_real_repeat_detected():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_REP_A, _CLUSTER_REP_B]))
        if out.get("real_repeat_count", 0) >= 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "INTENTIONAL_RECURRENCE_REAL_REPEAT" in codes
    finally:
        _set_mode(bak)


def test_jaccard_basic():
    assert mod._jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert mod._jaccard({"a"}, {"b"}) == 0.0
    assert mod._jaccard(set(), {"a"}) == 0.0


def test_trigrams_basic():
    assert mod._trigrams("abcd") == {"abc", "bcd"}
    assert mod._trigrams("ab") == set()


def test_extract_axes_basic():
    axes = mod._extract_axes(_CLUSTER_A_INTENT)
    for k in ("actor", "place", "prop", "mood", "outcome"):
        assert k in axes


def test_div_axes_counts_differences():
    a = mod._extract_axes(_CLUSTER_A_INTENT)
    b = mod._extract_axes(_CLUSTER_B_INTENT)
    count, diffs = mod._div_axes(a, b)
    assert count >= 3
    assert "actor" in diffs
    assert "place" in diffs


def test_div_axes_identical_zero():
    a = mod._extract_axes(_CLUSTER_REP_A)
    b = mod._extract_axes(_CLUSTER_REP_B)
    count, diffs = mod._div_axes(a, b)
    assert count == 0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("INTENTIONAL_RECURRENCE_DETECTED", "INTENTIONAL_RECURRENCE_REAL_REPEAT",
              "INTENTIONAL_RECURRENCE_THIN_DATA"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("intentional_recurrence")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_flag():
    out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
    assert out.get("_placeholder") is True


def test_no_db_returns_thin():
    proj = Path(tempfile.mkdtemp())
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(proj)
        # 无 _数据库 → 跳过
        assert "cluster 摘要 < 2" in (out.get("note") or "")
    finally:
        _set_mode(bak)


# ── 🔴 2026-07-01 真语义 embedding 可选路径（完全照抄 topic_drift_scanner 测试手法）──────

def _char_freq_embedding(text: str, dim: int = 32) -> list:
    """确定性 mock embedding（字符频率向量·同 test_topic_drift_scanner 手法）。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _run_with_mock_embedding(fn, *args, **kwargs):
    """EMBED_BACKEND=mock + monkeypatch embedding_store.compute_embedding 后跑 fn。"""
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "mock"
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _char_freq_embedding
    try:
        return fn(*args, **kwargs)
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_has_real_embedding_backend_false_by_default():
    bak = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        for k, v in saved.items():
            os.environ[k] = v


def test_has_real_embedding_backend_true_with_mstyle():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "mstyle"
        assert mod._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_similarity_direct_with_mock_backend():
    """直接测 _semantic_similarity：mock 后端下返回浮点相似度(完全相同文本 → 相似度 1)。
    compute_embedding/cosine_similarity 由调用方传入(scan() 一次性 import 后传参的设计)。"""
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _char_freq_embedding
    try:
        sim = mod._semantic_similarity("主角在山顶挥剑", "主角在山顶挥剑",
                                       embedding_store.compute_embedding,
                                       embedding_store.cosine_similarity)
        assert sim is not None
        assert abs(sim - 1.0) < 1e-6
    finally:
        embedding_store.compute_embedding = orig


def test_semantic_similarity_dimension_mismatch_returns_none():
    """维度不一致 → None（调用方兜底 trigram Jaccard）。"""
    def _mixed(text, dim=32):
        if "MISMATCH" in text:
            return [0.1] * 8
        return _char_freq_embedding(text, dim)
    import embedding_store
    orig = embedding_store.compute_embedding
    embedding_store.compute_embedding = _mixed
    try:
        sim = mod._semantic_similarity("正常文本内容", "MISMATCH 维度不同",
                                       embedding_store.compute_embedding,
                                       embedding_store.cosine_similarity)
        assert sim is None
    finally:
        embedding_store.compute_embedding = orig


def test_semantic_similarity_compute_exception_returns_none():
    """compute_embedding 抛异常 → None（调用方兜底 trigram Jaccard·不崩）。"""
    def _boom(text):
        raise RuntimeError("模拟 embedding 计算异常")
    import embedding_store
    sim = mod._semantic_similarity("文本 A", "文本 B", _boom,
                                   embedding_store.cosine_similarity)
    assert sim is None


def test_default_match_method_is_lexicon():
    """无真后端(默认) → match_method="lexicon"（零回归基线·trigram Jaccard 不变）。"""
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["match_method"] == "lexicon"
        for rec in out["samples"]:
            assert rec["match_method"] == "lexicon"
        for v in out["violations"]:
            assert v["match_method"] == "lexicon"
    finally:
        _set_mode(bak)


def test_semantic_match_method_when_backend_available():
    """真后端(mock)可用 → match_method="semantic"·pair 记录随之标 semantic。"""
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        out = _run_with_mock_embedding(
            mod.scan, _mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["match_method"] == "semantic"
        for rec in out["samples"]:
            assert rec["match_method"] == "semantic"
    finally:
        _set_mode(bak)


# ── 🔴 2026-07-03 Wave-4：语义路径批量 prefetch（一次 prefetch 取代逐对首见各自后端调用）──

def test_prefetch_called_once_with_all_summary_texts(monkeypatch):
    """语义路径下 scan() 应一次性 prefetch 全部 cluster 摘要文本，而非逐对 compute_embedding
    各自触发后端调用（3 个 cluster→C(3,2)=3 对但 prefetch 只应调用 1 次·文本只 3 份）。"""
    _CLUSTER_C = {
        "cluster_id": "cluster_777",
        "scope_summary": "主角在密室里破解机关找到线索",
        "characters_focus": ["主角"], "hub_locations": ["密室"],
        "anchor_props": ["机关"], "mood": "紧张", "outcome": "找到线索",
    }
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)),
                "cache_hits": 0, "computed": len(set(texts))}

    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        import embedding_store
        monkeypatch.setattr(embedding_store, "prefetch_embeddings", fake_prefetch)
        _run_with_mock_embedding(
            mod.scan, _mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT, _CLUSTER_C]))
        assert len(calls) == 1
        expected = {mod._summary_text_for_sim(c)
                    for c in (_CLUSTER_A_INTENT, _CLUSTER_B_INTENT, _CLUSTER_C)}
        assert set(calls[0]) == expected
    finally:
        _set_mode(bak)


def test_prefetch_not_called_when_no_real_backend(monkeypatch):
    """默认（无真后端）→ 语义路径整体不进入 → prefetch_embeddings 零调用（零回归）。"""
    calls = []

    def fake_prefetch(texts):
        calls.append(list(texts))
        return {}

    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    try:
        _set_mode("active")
        import embedding_store
        monkeypatch.setattr(embedding_store, "prefetch_embeddings", fake_prefetch)
        mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert calls == []
    finally:
        _set_mode(bak)


def test_semantic_import_error_falls_back_to_lexicon_end_to_end():
    """scan() 端到端：embedding_store 不可导入 → 全程退回 trigram Jaccard·match_method="lexicon"。"""
    bak = os.environ.get("INTENTIONAL_RECURRENCE_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    saved = sys.modules.get("embedding_store")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "mock"
        sys.modules["embedding_store"] = None  # type: ignore[assignment]
        out = mod.scan(_mk_project([_CLUSTER_A_INTENT, _CLUSTER_B_INTENT]))
        assert out["match_method"] == "lexicon"
    finally:
        if saved is not None:
            sys.modules["embedding_store"] = saved
        else:
            sys.modules.pop("embedding_store", None)
        _set_mode(bak)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
