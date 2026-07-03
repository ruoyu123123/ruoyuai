# -*- coding: utf-8 -*-
"""trope_tag_canonicalizer R23 W11 Batch-GG · P0"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import trope_tag_canonicalizer as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("TROPE_CANON_MODE", None)
    else:
        os.environ["TROPE_CANON_MODE"] = m


def _mk_project(clusters: list[dict] | None = None) -> Path:
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_canon_loaded_with_30_pairs():
    canon = mod.load_canon()
    m = canon.get("canonical_map") or {}
    assert len(m) >= 30, f"canonical_map size = {len(m)}"
    assert canon.get("_placeholder") is True


def test_canonicalize_tag_basic():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        assert mod.canonicalize_tag("重生归来") == "重生"
        assert mod.canonicalize_tag("再活一次") == "重生"
        assert mod.canonicalize_tag("魂穿") == "穿越"
        # 未命中 → 保留 surface
        assert mod.canonicalize_tag("某新概念") == "某新概念"
    finally:
        _set_mode(bak)


def test_canonicalize_tag_off_identity():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("off")
        # off 模式 → identity（不归并）
        assert mod.canonicalize_tag("重生归来") == "重生归来"
    finally:
        _set_mode(bak)


def test_canonicalize_tags_dedup_preserve_order():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        out = mod.canonicalize_tags(["重生", "重生归来", "再活一次", "穿越", "杀手"])
        # 三 surface 都 canonical 化到「重生」· 去重
        assert out.count("重生") == 1
        assert "穿越" in out
        assert "杀手" in out
        assert out[0] == "重生"
    finally:
        _set_mode(bak)


def test_canonicalize_tags_empty_input():
    assert mod.canonicalize_tags(None) == []
    assert mod.canonicalize_tags([]) == []
    assert mod.canonicalize_tags(["", "  "]) == []


def test_scan_off_returns_skeleton():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("off")
        out = mod.scan_for_promotions(_mk_project([{"trope_tags": ["重生"]}]))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "canonical_distribution" not in out
    finally:
        _set_mode(bak)


def test_scan_no_tags_skipped():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("active")
        out = mod.scan_for_promotions(_mk_project([{"cluster_id": "cluster_001"}]))
        assert out.get("note") == "无 trope 标签可扫"
    finally:
        _set_mode(bak)


def test_scan_active_promotes_new_surface():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("active")
        clusters = [
            {"trope_tags": ["新型 trope X", "重生", "穿越"]},
            {"trope_tags": ["新型 trope X", "金手指"]},
            {"trope_tags": ["新型 trope X", "杀手"]},
        ]
        proj = _mk_project(clusters)
        out = mod.scan_for_promotions(proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "TROPE_NEW_SURFACE_PROMOTION_CANDIDATE" in codes
        # promotion_queue 应被写入
        q = proj / "_数据库" / ".trope_promotion_queue.json"
        assert q.exists()
        obj = json.loads(q.read_text(encoding="utf-8"))
        assert any(c["surface"] == "新型 trope X" for c in obj.get("candidates", []))
    finally:
        _set_mode(bak)


def test_scan_shadow_no_promotion_write():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        clusters = [
            {"trope_tags": ["新型 trope X", "重生"]},
            {"trope_tags": ["新型 trope X"]},
            {"trope_tags": ["新型 trope X"]},
        ]
        proj = _mk_project(clusters)
        out = mod.scan_for_promotions(proj)
        # shadow 不上 violation
        assert out["violations"] == []
        # 不写 queue
        q = proj / "_数据库" / ".trope_promotion_queue.json"
        assert not q.exists()
    finally:
        _set_mode(bak)


def test_canonical_distribution_aggregates_synonyms():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("shadow")
        clusters = [
            {"trope_tags": ["重生归来"]},
            {"trope_tags": ["再活一次"]},
            {"trope_tags": ["重生"]},
        ]
        out = mod.scan_for_promotions(_mk_project(clusters))
        dist = out.get("canonical_distribution", {})
        # 三个 surface 全 canonical 化到「重生」
        assert dist.get("重生") == 3
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("TROPE_CANON_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("TROPE_NEW_SURFACE_PROMOTION_CANDIDATE", "TROPE_CANON_DICT_THIN"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("trope_tag_canonicalizer")
    assert s is not None
    assert s.get("_new") is True


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-02 embedding 最近邻 canonical 建议接线（真后端命中 + 门控关字段缺省）
# 参考范式：topic_drift_scanner._has_real_embedding_backend（本仓约定每文件自留一份）
# ════════════════════════════════════════════════════════════════════
def _clear_embed_env():
    bak_eb = os.environ.pop("EMBED_BACKEND", None)
    bak_gen = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("GEN_EMBED__")}
    return bak_eb, bak_gen


def _restore_embed_env(bak_eb, bak_gen):
    if bak_eb is not None:
        os.environ["EMBED_BACKEND"] = bak_eb
    for k, v in bak_gen.items():
        os.environ[k] = v


def test_has_real_embedding_backend_false_by_default():
    bak_eb, bak_gen = _clear_embed_env()
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        _restore_embed_env(bak_eb, bak_gen)


def test_has_real_embedding_backend_true_when_set():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "fake-real"
        assert mod._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_promotion_candidate_gets_embedding_suggestion_when_real_backend():
    """真后端命中：新 surface 语义上贴近某已有 canonical 值 → 加建议字段（仍要求人审·
    绝不自动改 trope_canon.json）。"""
    bak_mode = os.environ.get("TROPE_CANON_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "fake-real"
        import embedding_store
        orig = embedding_store.compute_embedding

        def _content_aware_embed(text):
            # "死而复生"语义上贴近 canonical「重生」；其余目标给正交向量
            if text in ("死而复生", "重生"):
                return [1.0, 0.0]
            return [0.0, 1.0]

        embedding_store.compute_embedding = _content_aware_embed
        try:
            clusters = [
                {"trope_tags": ["死而复生", "重生", "穿越"]},
                {"trope_tags": ["死而复生", "金手指"]},
                {"trope_tags": ["死而复生", "杀手"]},
            ]
            proj = _mk_project(clusters)
            out = mod.scan_for_promotions(proj)
            cands = {c["surface"]: c for c in out["promotion_candidates"]}
            assert "死而复生" in cands
            assert cands["死而复生"].get("embedding_nearest_canonical") == "重生"
            assert cands["死而复生"].get("embedding_similarity") == 1.0
        finally:
            embedding_store.compute_embedding = orig
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_promotion_candidate_no_suggestion_below_threshold():
    """真后端就绪但相似度 < 阈值 → 不加建议字段（不是随便配了后端就无脑建议）。"""
    bak_mode = os.environ.get("TROPE_CANON_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    try:
        _set_mode("active")
        os.environ["EMBED_BACKEND"] = "fake-real"
        import embedding_store
        orig = embedding_store.compute_embedding

        def _orthogonal_embed(text):
            return [1.0, 0.0] if text == "新型 trope X" else [0.0, 1.0]

        embedding_store.compute_embedding = _orthogonal_embed
        try:
            clusters = [
                {"trope_tags": ["新型 trope X", "重生", "穿越"]},
                {"trope_tags": ["新型 trope X", "金手指"]},
                {"trope_tags": ["新型 trope X", "杀手"]},
            ]
            proj = _mk_project(clusters)
            out = mod.scan_for_promotions(proj)
            cands = {c["surface"]: c for c in out["promotion_candidates"]}
            assert "新型 trope X" in cands
            assert "embedding_nearest_canonical" not in cands["新型 trope X"]
        finally:
            embedding_store.compute_embedding = orig
    finally:
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_promotion_candidate_field_absent_when_gate_off():
    """🔴 零回归锁：门控关（无 EMBED_BACKEND）→ promotion_candidates 不带 embedding 建议
    字段（字段缺省·不是给默认值），且 compute_embedding 换成任意值也绝不会被调用。"""
    bak_mode = os.environ.get("TROPE_CANON_MODE")
    bak_eb, bak_gen = _clear_embed_env()
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise AssertionError("门控关时绝不应调用 compute_embedding")

    embedding_store.compute_embedding = _boom
    try:
        _set_mode("active")
        clusters = [
            {"trope_tags": ["新型 trope X", "重生", "穿越"]},
            {"trope_tags": ["新型 trope X", "金手指"]},
            {"trope_tags": ["新型 trope X", "杀手"]},
        ]
        proj = _mk_project(clusters)
        out = mod.scan_for_promotions(proj)
        cands = {c["surface"]: c for c in out["promotion_candidates"]}
        assert "新型 trope X" in cands
        assert set(cands["新型 trope X"].keys()) == {"surface", "count"}   # 逐字段零回归
    finally:
        embedding_store.compute_embedding = orig
        _set_mode(bak_mode)
        _restore_embed_env(bak_eb, bak_gen)


def test_canonicalize_tag_never_touches_embedding():
    """canonicalize_tag 本体不动：真后端就绪时也绝不调用 compute_embedding（纯字典精确匹配·
    只有 scan_for_promotions 的晋升候选队列才叠加 embedding 建议）。"""
    bak_mode = os.environ.get("TROPE_CANON_MODE")
    bak_eb = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise AssertionError("canonicalize_tag 不该碰 embedding")

    embedding_store.compute_embedding = _boom
    try:
        _set_mode("shadow")
        assert mod.canonicalize_tag("重生归来") == "重生"
        assert mod.canonicalize_tag("某新概念") == "某新概念"
    finally:
        embedding_store.compute_embedding = orig
        _set_mode(bak_mode)
        if bak_eb is not None:
            os.environ["EMBED_BACKEND"] = bak_eb
        else:
            os.environ.pop("EMBED_BACKEND", None)
