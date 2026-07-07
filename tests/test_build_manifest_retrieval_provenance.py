# -*- coding: utf-8 -*-
"""P2 向量检索溯源 typed manifest 段回归锁（2026-07-07）。

借鉴 AI_NovelGenerator / PlotPilot（research/open_source_writing_systems.md
「Vector retrieval provenance」条目）：build_manifest 的两个检索类载荷
（selective_history_retrieval / relevant_heuristics）规范成 typed 契约段——

  段级：source_type / match_method / token_budget{budget_chars, actual_chars, truncated}
        / anti_copy="reference-not-copy"（advisory·防 writer 照抄近邻正文/条目原句）
  条级：source_type / source_id（确定性派生：selective_history=chNNN#idx·
        heuristics=category#id）/ similarity / match_method（+ selective_history 额外
        recency_distance）

纪律锁：
  · 「无真语义就不伪装语义」——selective_history 无真后端 skip 路径形态逐字节不变；
  · token_budget 只把既有截断行为显式化（heuristics name[:60]/desc[:120]），
    不新增截断（selective_history 本段无截断 → truncated 恒 false）；
  · 消费方 seam：gen_writer 把 manifest 文件原文注入 prompt（## manifest 段），
    typed 段必须 JSON 可序列化且 _load_manifest_once 正常加载。

全程确定性内容感知假 embedding（字符频率向量），不打 LLM、不联网。
"""
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import build_manifest as bm  # noqa: E402


def _char_freq_embedding(text: str, dim: int = 64) -> list:
    """确定性、内容感知的假 embedding（字符频率向量）。"""
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


def _mk_history_project() -> tuple:
    """临时项目：ch=2 有 query 信号 + .embeddings/chapter_001.json 两条候选。"""
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


def _run_history_real_backend(monkeypatch, chapter=2, top_k=2, backend="fake-real"):
    """真后端路径跑 _collect_selective_history，返回 (res, tmp_dir 清理句柄, previews)。"""
    td, root, rel, irr = _mk_history_project()
    monkeypatch.setenv("EMBED_BACKEND", backend)
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
    scanner = bm.DatabaseScanner(root, chapter)
    res = bm._collect_selective_history(scanner, chapter, top_k=top_k)
    return res, td, (rel, irr)


def _mk_heuristics_project(success_patterns: list) -> tuple:
    """临时项目 + 写作经验.json + 进度.json（ch=2 context：东方不败/移花接玉）。"""
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


# ════════════════════════════════════════════════════════════════════
# ① selective_history：typed 字段存在 + source_id/recency_distance 派生正确
# ════════════════════════════════════════════════════════════════════

def test_selective_history_typed_fields_and_source_id_derivation(monkeypatch):
    res, td, (rel, _irr) = _run_history_real_backend(monkeypatch)
    try:
        # 段级契约
        assert res["source_type"] == "selective_history"
        assert res["match_method"] == "embedding:fake-real"   # 记真实后端名
        assert res["anti_copy"] == "reference-not-copy"
        assert res["retrieved"], "真后端应检出候选"
        # 条级契约：source_id 由 ch+chunk_idx 确定性派生（chNNN#idx）
        for item in res["retrieved"]:
            assert item["source_type"] == "selective_history"
            assert re.fullmatch(r"ch\d{3}#\d+", item["source_id"]), item["source_id"]
            assert item["source_id"] == f"ch{item['ch']:03d}#{item['chunk_idx']}"
            assert item["match_method"] == res["match_method"]
            assert isinstance(item["similarity"], float)
            assert item["recency_distance"] == 2 - item["ch"]  # 当前章 2 − 来源章
        # 语义排序不受 typed 化影响：相关片段仍第一
        assert res["retrieved"][0]["text_preview"] == rel
        assert res["retrieved"][0]["source_id"] == "ch001#0"
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ② 「无真语义就不伪装语义」：无后端 skip 形态逐字节不变（不带 typed 字段）
# ════════════════════════════════════════════════════════════════════

def test_selective_history_no_backend_skip_shape_unchanged(monkeypatch):
    _clear_embed_env(monkeypatch)
    td, root, _rel, _irr = _mk_history_project()
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_selective_history(scanner, 2, top_k=2)
        assert res == {"retrieved": [], "reason": (
            "无真 embedding 后端（EMBED_BACKEND 未设/=hash）·hash 假嵌入不可当语义检索用·跳过")}
        # typed 字段绝不出现在 skip 形态（诚实 skip ≠ 空 typed 段）
        for key in ("source_type", "match_method", "token_budget", "anti_copy"):
            assert key not in res
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ③ token_budget 真实反映行为：本段无截断 → truncated 恒 false、actual=实际字符数
# ════════════════════════════════════════════════════════════════════

def test_selective_history_token_budget_reflects_actual_behavior(monkeypatch):
    res, td, _texts = _run_history_real_backend(monkeypatch)
    try:
        tb = res["token_budget"]
        assert tb["truncated"] is False                       # 本段无截断行为（不新增截断）
        assert tb["actual_chars"] == sum(len(i["text_preview"]) for i in res["retrieved"])
        assert tb["budget_chars"] == len(res["retrieved"]) * 80  # 索引期 text_preview 既有 80 字上限
        assert tb["actual_chars"] <= tb["budget_chars"]
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ④ manifest 快照（JSON 序列化形态）含 anti_copy 溯源字段
# ════════════════════════════════════════════════════════════════════

def test_selective_history_manifest_snapshot_contains_anti_copy(monkeypatch):
    res, td, _texts = _run_history_real_backend(monkeypatch)
    try:
        snapshot = json.dumps({"selective_history_retrieval": res}, ensure_ascii=False)
        assert "reference-not-copy" in snapshot
        assert '"source_id"' in snapshot and '"match_method"' in snapshot
        # 往返可解析（写盘 manifest 的形态）
        assert json.loads(snapshot)["selective_history_retrieval"]["anti_copy"] == "reference-not-copy"
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ⑤ relevant_heuristics：typed 条级字段 + source_id=category#id + similarity 降序
# ════════════════════════════════════════════════════════════════════

def test_relevant_heuristics_typed_items_and_source_id(monkeypatch):
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    td, root = _mk_heuristics_project([
        {"id": "hit1", "name": "移花接玉技法", "description": "移花接玉挪移武功精髓",
         "confidence": 0.6, "usage_count": 0},
        {"id": "miss1", "name": "无关技法", "description": "完全不相关的另一套写法",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        assert res["source_type"] == "relevant_heuristics"
        assert res["match_method"] == "keyword"               # 既有段级值不变（回归）
        assert res["anti_copy"] == "reference-not-copy"
        assert res["retrieved"][0]["id"] == "hit1"            # 既有排序行为不变（回归）
        for item in res["retrieved"]:
            assert item["source_type"] == "relevant_heuristics"
            assert item["source_id"] == f"{item['category']}#{item['id']}"
            assert item["match_method"] == "keyword"
            assert isinstance(item["similarity"], float)
            assert "_orig" not in item and "_retrieved_at_ch" not in item  # 内部字段不泄漏
        sims = [i["similarity"] for i in res["retrieved"]]
        assert sims == sorted(sims, reverse=True), "similarity 应与检索排序一致（降序）"
        assert res["retrieved"][0]["source_id"] == "success_patterns#hit1"
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ⑥ relevant_heuristics token_budget：既有 name[:60]/desc[:120] 截断显式化
# ════════════════════════════════════════════════════════════════════

def test_relevant_heuristics_token_budget_truncation_explicit(monkeypatch):
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    long_desc = "移花接玉" * 40                                # 160 字 > 120 上限 → 必截
    td, root = _mk_heuristics_project([
        {"id": "long1", "name": "移花接玉技法", "description": long_desc,
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        tb = res["token_budget"]
        assert tb["truncated"] is True                        # 命中既有截断 → 显式记 true
        assert tb["budget_chars"] == len(res["retrieved"]) * (60 + 120)
        assert tb["actual_chars"] == sum(
            len(i["name"]) + len(i["description"]) for i in res["retrieved"])
        assert len(res["retrieved"][0]["description"]) == 120  # 截断行为本身不变（非新增）
    finally:
        _rm(td)


def test_relevant_heuristics_no_truncation_flag_false(monkeypatch):
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    td, root = _mk_heuristics_project([
        {"id": "short1", "name": "短名", "description": "短描述在上限内",
         "confidence": 0.6, "usage_count": 0},
    ])
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        tb = res["token_budget"]
        assert tb["truncated"] is False
        assert tb["actual_chars"] == len("短名") + len("短描述在上限内")
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ⑦ relevant_heuristics 零候选：typed 段级字段齐全（消费方按契约读不崩）
# ════════════════════════════════════════════════════════════════════

def test_relevant_heuristics_empty_pool_typed_segment(monkeypatch):
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    td, root = _mk_heuristics_project([])
    try:
        scanner = bm.DatabaseScanner(root, 2)
        res = bm._collect_relevant_heuristics(scanner, 2)
        assert res["mode"] == "on" and res["retrieved"] == []
        assert res["source_type"] == "relevant_heuristics"
        assert res["match_method"] == "none"                  # 未发生检索 → 不冒充检索方式
        assert res["anti_copy"] == "reference-not-copy"
        assert res["token_budget"] == {"budget_chars": 0, "actual_chars": 0, "truncated": False}
    finally:
        _rm(td)


# ════════════════════════════════════════════════════════════════════
# ⑧ 消费方 seam：gen_writer 加载含 typed 段的 manifest（prompt 注入原文形态）
# ════════════════════════════════════════════════════════════════════

def test_gen_writer_consumer_loads_typed_manifest(monkeypatch, tmp_path):
    """gen_writer 把 manifest 文件**原文**注入 prompt（## manifest 段）并经
    _load_manifest_once 解析给各 _build_*_section。typed 检索段必须：
    ① 被 _load_manifest_once 正常加载；② 原文含溯源关键字（writer 可见 anti_copy 指令）；
    ③ 不干扰其他 section builder（style fingerprint 等照常空转不崩）。"""
    res, td, _texts = _run_history_real_backend(monkeypatch)
    try:
        import gen_writer as gw
        manifest_path = tmp_path / "ch_002.json"
        manifest_doc = {"chapter": 2, "selective_history_retrieval": res}
        manifest_text = json.dumps(manifest_doc, ensure_ascii=False, indent=2)
        manifest_path.write_text(manifest_text, encoding="utf-8")

        loaded = gw._load_manifest_once(manifest_path)
        assert isinstance(loaded, dict)
        seg = loaded["selective_history_retrieval"]
        assert seg["anti_copy"] == "reference-not-copy"
        assert seg["retrieved"][0]["source_id"].startswith("ch")

        # writer prompt 注入的是文件原文——溯源字段对 writer 真实可见
        assert "reference-not-copy" in manifest_text
        assert "source_id" in manifest_text

        # 其他 manifest 消费 section 不受 typed 段影响（无对应字段 → 空串，不异常）
        assert gw._build_style_fingerprint_section(manifest_path, loaded) == ""
        assert gw._build_rolling_anchor_section(manifest_path, loaded) == ""
    finally:
        _rm(td)
