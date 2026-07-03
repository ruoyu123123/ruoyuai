# -*- coding: utf-8 -*-
"""build_manifest embedding_store 真接线回归测试（2026-07-02）。

覆盖两处改动：
  ① _collect_relevant_heuristics：真后端时 context 关键词重叠打分换成 embedding 余弦；
     无真后端时逐字节保持原关键词重叠计分（零回归）。
  ② _collect_selective_history bug fix：此前无门控裸用 compute_embedding/cosine_similarity——
     默认 hash 假嵌入会静默冒充语义检索。补门控后：无真后端时整段语义检索跳过（且绝不
     调用 compute_embedding），诚实返回空；真后端时语义检索照常工作。

全程确定性内容感知假 embedding（字符频率向量，同 test_topic_drift_scanner 手法），
不打 LLM、不联网。
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

import build_manifest as bm  # noqa: E402


def _char_freq_embedding(text: str, dim: int = 64) -> list:
    """确定性、内容感知的假 embedding（字符频率向量）——只看字符分布不看顺序。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _clear_embed_env(monkeypatch):
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in [k for k in os.environ if k.startswith("GEN_EMBED__")]:
        monkeypatch.delenv(k, raising=False)


def _rm(td):
    import shutil
    shutil.rmtree(td, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
# _has_real_embedding_backend 门控（跟 topic_drift_scanner 同款逻辑）
# ════════════════════════════════════════════════════════════════════

def test_has_real_embedding_backend_gate(monkeypatch):
    _clear_embed_env(monkeypatch)
    assert bm._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    assert bm._has_real_embedding_backend() is False
    monkeypatch.setenv("EMBED_BACKEND", "mstyle")
    assert bm._has_real_embedding_backend() is True
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.setenv("GEN_EMBED__test__API_KEY", "fake")
    assert bm._has_real_embedding_backend() is True


# ════════════════════════════════════════════════════════════════════
# ① _collect_relevant_heuristics：真后端 embedding 余弦 vs 无真后端关键词重叠
# ════════════════════════════════════════════════════════════════════

def _mk_heuristics_project(success_patterns: list) -> tuple:
    """临时项目 + 写作经验.json + 进度.json（本章 context：chars=["东方不败"]，
    turning="移花接玉"）。返回 (tmp_dir, project_root)。"""
    td = tempfile.mkdtemp()
    root = Path(td) / "测试书"
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    (root / "_数据库" / "写作经验.json").write_text(
        json.dumps({"success_patterns": success_patterns, "failure_patterns": []},
                   ensure_ascii=False), encoding="utf-8")
    (root / "_数据库" / "进度.json").write_text(json.dumps({
        "cluster_blueprint": {
            "cluster_001": {"scene_storyboard": [
                {"ch": 2, "characters": ["东方不败"], "turning_point": "移花接玉"},
            ]}
        }
    }, ensure_ascii=False), encoding="utf-8")
    return td, root


def test_relevant_heuristics_gate_off_uses_keyword_overlap(monkeypatch):
    """零回归证明：无真后端（默认环境）→ match_method=keyword，且 kw 重叠计分逻辑不变。"""
    _clear_embed_env(monkeypatch)
    td, root = _mk_heuristics_project([
        {"id": "hit1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
        {"id": "miss1", "name": "无关技法", "description": "完全不相关的另一套写法",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        assert res["mode"] == "on"
        assert res["match_method"] == "keyword"
        # "移花接玉" 关键词命中 desc → 应排第一（关键词重叠计分未变）
        assert res["retrieved"][0]["id"] == "hit1"
    finally:
        _rm(td)


def test_relevant_heuristics_gate_off_never_calls_compute_embedding(monkeypatch):
    """无真后端时 _collect_relevant_heuristics 不应调用 compute_embedding（零调用）。"""
    _clear_embed_env(monkeypatch)
    td, root = _mk_heuristics_project([
        {"id": "hit1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        import embedding_store
        calls = {"n": 0}

        def _counting(text):
            calls["n"] += 1
            return [0.0]

        monkeypatch.setattr(embedding_store, "compute_embedding", _counting)
        scanner = bm.DatabaseScanner(root, 2)
        bm._collect_relevant_heuristics(scanner, 2)
        assert calls["n"] == 0, "无真后端不应调用 compute_embedding"
    finally:
        _rm(td)


def test_relevant_heuristics_real_backend_uses_embedding_cosine(monkeypatch):
    """真后端命中：embedding 余弦驱动排序——制造「关键词路径打平手、embedding 路径能翻盘」
    的场景，证明真的走了语义路径而非仅仅贴标签。

    context = {"东方不败", "移花接玉"} → _ctx_text 排序拼接。
    - "near1"：desc 是 _ctx_text 的反转字符串（字符集合完全相同 → 假 embedding 余弦≈1.0；
      但因反转，不含任何原始 2-4 字 token 子串 → 关键词路径 kw_hits=0）。
    - "far1"：desc 是完全不相关字符（水果名）→ 假 embedding 余弦≈0；关键词路径 kw_hits=0 同样。
    两条 confidence/usage_count 全同 → 关键词路径下（kw_hits 均 0）打平手，稳定排序保留
    输入顺序（far1 在前）；embedding 路径下 near1 应翻盘到第一。
    """
    td, root = _mk_heuristics_project([])  # 先建项目骨架，稍后手写经验文件（控制顺序）
    try:
        scanner0 = bm.DatabaseScanner(root, 2)
        ch_plan = scanner0.load("进度", {})["cluster_blueprint"]["cluster_001"]["scene_storyboard"][0]
        ctx_kws = sorted({ch_plan["characters"][0], ch_plan["turning_point"]})
        ctx_text = " ".join(ctx_kws)

        far_desc = "苹果香蕉葡萄西瓜哈密瓜草莓橙子柚子桃李杏梅兰竹菊松柏"
        near_desc = ctx_text[::-1]
        assert far_desc not in ctx_text and near_desc not in ctx_text  # 均不含原始子串（kw_hits=0 前提）

        (root / "_数据库" / "写作经验.json").write_text(json.dumps({
            "success_patterns": [
                {"id": "far1", "name": "", "description": far_desc,
                 "confidence": 0.5, "usage_count": 0},
                {"id": "near1", "name": "", "description": near_desc,
                 "confidence": 0.5, "usage_count": 0},
            ],
            "failure_patterns": [],
        }, ensure_ascii=False), encoding="utf-8")

        # 先钉死关键词路径下确实打平手、far1 因稳定排序保留原序排第一（对照组）
        scanner_kw = bm.DatabaseScanner(root, 2)
        res_kw = bm._collect_relevant_heuristics(scanner_kw, 2)
        assert res_kw["match_method"] == "keyword"
        assert res_kw["retrieved"][0]["id"] == "far1"

        # 真后端 + 内容感知假 embedding → near1 应翻盘到第一
        monkeypatch.setenv("EMBED_BACKEND", "fake-real")
        import embedding_store
        monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
        scanner_emb = bm.DatabaseScanner(root, 2)
        res_emb = bm._collect_relevant_heuristics(scanner_emb, 2)
        assert res_emb["match_method"] == "embedding"
        assert res_emb["retrieved"][0]["id"] == "near1", (
            f"embedding 路径应因字符集合相同(反转串)而把 near1 排第一，实得 {res_emb['retrieved']}")
    finally:
        _rm(td)


def test_relevant_heuristics_batches_prefetch_once(monkeypatch):
    """🔴 2026-07-03 Wave-4：score() 对每条经验 desc 逐条 compute_embedding 之前，应先对
    全部 all_patterns 的 desc 触发一次批量 prefetch_embeddings（而非各自撞真后端 N 次）。
    query embed（_ctx_text）不在这次批量文本集合内——它已在循环外单独算过。"""
    td, root = _mk_heuristics_project([
        {"id": "p1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
        {"id": "p2", "name": "无关技法", "description": "完全不相关的另一套写法",
         "confidence": 0.6, "usage_count": 0},
        {"id": "p3", "name": "第三条", "description": "又一条独立的写作经验描述",
         "confidence": 0.5, "usage_count": 0},
    ])
    try:
        monkeypatch.setenv("EMBED_BACKEND", "fake-real")
        import embedding_store
        monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
        prefetch_calls = []

        def _recording_prefetch(texts):
            prefetch_calls.append(list(texts))
            return {"total": len(texts), "unique": len(set(texts)),
                    "cache_hits": 0, "computed": len(set(texts))}

        monkeypatch.setattr(embedding_store, "prefetch_embeddings", _recording_prefetch)
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        assert len(prefetch_calls) == 1, "应只触发一次批量 prefetch"
        assert len(prefetch_calls[0]) == 3   # 3 条 pattern desc（不含 query 文本）
        assert res["match_method"] == "embedding"
    finally:
        _rm(td)


def test_relevant_heuristics_gate_off_never_calls_prefetch(monkeypatch):
    """无真后端（_query_emb 恒 None）时批量 prefetch 分支也不应触发（与 compute_embedding 同款零调用）。"""
    _clear_embed_env(monkeypatch)
    td, root = _mk_heuristics_project([
        {"id": "hit1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        import embedding_store
        calls = {"n": 0}

        def _counting_prefetch(texts):
            calls["n"] += 1
            return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

        monkeypatch.setattr(embedding_store, "prefetch_embeddings", _counting_prefetch)
        scanner = bm.DatabaseScanner(root, 2)
        bm._collect_relevant_heuristics(scanner, 2)
        assert calls["n"] == 0, "无真后端不应调用 prefetch_embeddings"
    finally:
        _rm(td)


def test_relevant_heuristics_real_backend_failure_falls_back_to_keyword(monkeypatch):
    """真后端配置但 embedding_store 计算异常 → 静默回退关键词路径（不崩·不阻断 manifest）。"""
    td, root = _mk_heuristics_project([
        {"id": "hit1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        monkeypatch.setenv("EMBED_BACKEND", "fake-real")
        import embedding_store

        def _boom(text):
            raise RuntimeError("模拟真后端故障")

        monkeypatch.setattr(embedding_store, "compute_embedding", _boom)
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        assert res["mode"] == "on"
        assert res["match_method"] == "keyword"   # 编码失败 → _query_emb 仍是 None
        assert res["retrieved"][0]["id"] == "hit1"
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ② _collect_selective_history bug fix：无门控裸调 compute_embedding
# ════════════════════════════════════════════════════════════════════

def _mk_history_project() -> tuple:
    """临时项目：进度.json 含 ch=2 的 query 信号 + .embeddings/chapter_001.json 两条候选
    （一条语义相关、一条无关）。返回 (tmp_dir, project_root, relevant_text, irrelevant_text)。"""
    td = tempfile.mkdtemp()
    root = Path(td) / "测试书"
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    (root / "_数据库" / "进度.json").write_text(json.dumps({
        "cluster_blueprint": {
            "cluster_001": {"scene_storyboard": [
                {"ch": 2, "turning_point": "青冥剑现世", "goal": "夺回信物",
                 "threads_advance": ["江湖恩怨"]},
            ]}
        }
    }, ensure_ascii=False), encoding="utf-8")
    emb_dir = root / "_数据库" / ".embeddings"
    emb_dir.mkdir(parents=True, exist_ok=True)
    relevant_text = "青冥剑现世的那一夜江湖恩怨骤起风云突变"
    irrelevant_text = "厨房里炖着汤水柴米油盐岁月静好安然入睡"
    (emb_dir / "chapter_001.json").write_text(json.dumps({
        "chunks": [
            {"idx": 0, "text_preview": relevant_text,
             "embedding": _char_freq_embedding(relevant_text)},
            {"idx": 1, "text_preview": irrelevant_text,
             "embedding": _char_freq_embedding(irrelevant_text)},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    return td, root, relevant_text, irrelevant_text


def test_selective_history_gate_off_never_calls_compute_embedding(monkeypatch):
    """🔴 bug fix 核心断言：无真后端（默认环境）→ compute_embedding 零调用，诚实返回空。"""
    _clear_embed_env(monkeypatch)
    td, root, _rel, _irr = _mk_history_project()
    try:
        import embedding_store
        calls = {"n": 0}

        def _counting(text):
            calls["n"] += 1
            return [0.0]

        monkeypatch.setattr(embedding_store, "compute_embedding", _counting)
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_selective_history(scanner, 2, top_k=2)
        assert calls["n"] == 0, "无真后端时 _collect_selective_history 不应调用 compute_embedding"
        assert res["retrieved"] == []
        assert "reason" in res and res["reason"]
    finally:
        _rm(td)


def test_selective_history_gate_off_skips_even_with_embeddings_present(monkeypatch):
    """无真后端时即使 .embeddings 索引存在、query 信号齐全，也不做语义检索（不冒充语义）。"""
    _clear_embed_env(monkeypatch)
    td, root, _rel, _irr = _mk_history_project()
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_selective_history(scanner, 2, top_k=2)
        assert res == {"retrieved": [], "reason": (
            "无真 embedding 后端（EMBED_BACKEND 未设/=hash）·hash 假嵌入不可当语义检索用·跳过")}
    finally:
        _rm(td)


def test_selective_history_real_backend_semantic_ranking(monkeypatch):
    """真后端命中：语义相关的历史 chunk 应排第一（真调用 compute_embedding 编码 query）。"""
    td, root, relevant_text, irrelevant_text = _mk_history_project()
    try:
        monkeypatch.setenv("EMBED_BACKEND", "fake-real")
        import embedding_store
        calls = {"n": 0}

        def _counting_char_freq(text):
            calls["n"] += 1
            return _char_freq_embedding(text)

        monkeypatch.setattr(embedding_store, "compute_embedding", _counting_char_freq)
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_selective_history(scanner, 2, top_k=2)
        assert calls["n"] > 0, "真后端应真调用 compute_embedding 编码 query"
        assert res["retrieved"], "应检出候选 chunk"
        assert res["retrieved"][0]["text_preview"] == relevant_text, (
            f"语义相关 chunk 应排第一，实得 {res['retrieved']}")
    finally:
        _rm(td)


def test_selective_history_first_chapter_unaffected_by_gate(monkeypatch):
    """chapter<=1 早退分支不受本次门控改动影响（无论真后端与否）。"""
    _clear_embed_env(monkeypatch)
    td, root, _rel, _irr = _mk_history_project()
    try:
        scanner = bm.DatabaseScanner(root, 1)
        res = bm._collect_selective_history(scanner, 1, top_k=2)
        assert res == {"retrieved": [], "reason": "首章无历史"}
    finally:
        _rm(td)
