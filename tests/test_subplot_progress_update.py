#!/usr/bin/env python3
"""subplot_progress_update str/dict 走向线兼容测试（G3 e2e 修 · 2026-06-23）

G3 真 API e2e 抓出：subplot_threads.json / 四线脉络.json 的线条目可能是**字符串列表**
（走向线 schema 存成 str），脚本却假设全是 dict 列表 → `line.get("name")` 抛
`'str' object has no attribute 'get'`。advisory 性质虽不阻断主链，但脏报错污染日志。
修后必须对 str / dict 两种 schema 都跑通且不抛异常。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import subplot_progress_update as m   # noqa: E402


def _mkproj(throughlines, threads):
    d = Path(tempfile.mkdtemp())
    db = d / "_数据库"
    db.mkdir(parents=True)
    (db / "四线脉络.json").write_text(
        json.dumps({"throughlines": throughlines}, ensure_ascii=False), encoding="utf-8")
    (db / "subplot_threads.json").write_text(
        json.dumps({"threads": threads}, ensure_ascii=False), encoding="utf-8")
    (db / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "主线复仇推进，感情线起步"}]},
            ensure_ascii=False), encoding="utf-8")
    return d


def test_str_list_schema_no_crash():
    """字符串列表 schema：不抛 AttributeError，命中关键词计数正常。"""
    d = _mkproj(throughlines=["主线复仇", "感情线", "支线甲"],
                threads=["主线复仇", "配角线"])
    r = m.update(d, "001")
    # 四线：主线复仇 + 感情线 命中摘要 → 2；支线甲 不命中
    assert r["throughline_updated"] == 2
    # subplot：主线复仇 命中 → 1；配角线 不命中
    assert r["subplot_updated"] == 1
    # str schema 只读不回写：四线脉络仍是字符串列表（schema 未被破坏）
    tl = json.loads((d / "_数据库" / "四线脉络.json").read_text(encoding="utf-8"))
    assert tl["throughlines"] == ["主线复仇", "感情线", "支线甲"]


def test_dict_list_schema_writes_back():
    """dict 列表 schema：命中线条目回写 last_cluster / last_updated。"""
    d = _mkproj(
        throughlines=[{"name": "主线复仇"}, {"name": "无关线"}],
        threads=[{"id": "t1", "name": "主线复仇"}, {"id": "t2", "name": "另一条"}])
    r = m.update(d, "001")
    assert r["throughline_updated"] == 1   # 只有「主线复仇」命中
    assert r["subplot_updated"] == 1
    sub = json.loads((d / "_数据库" / "subplot_threads.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in sub["threads"]}
    assert by_id["t1"].get("last_cluster") == "001"
    assert "last_cluster" not in by_id["t2"]


def test_mixed_list_schema_no_crash():
    """混合 str + dict 列表也不崩（防御性兼容）。"""
    d = _mkproj(
        throughlines=["主线复仇", {"name": "感情线"}],
        threads=["主线复仇", {"id": "t9", "name": "另一条"}])
    r = m.update(d, "001")  # 不抛异常即通过
    assert r["throughline_updated"] == 2  # 两条都命中摘要
    assert r["subplot_updated"] == 1


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）
# mock 面：直接换 embedding_store.content_backend_available /
# compute_content_embedding(_batch) / prefetch_content_embeddings 属性（不用
# pytest monkeypatch fixture · 兼容本文件的 __main__ 自跑器）。content_backend_available
# 查真文件系统（venv/infer 脚本/模型目录）不像旧 EMBED_BACKEND 是环境变量，本机若已备好
# bge 模型会恒真——_mkproj() 里顺带把它重置为 False（每个测试的第一行都会调 _mkproj，
# 早于各测试自己的显式覆盖·防跨测试非确定性污染又不依赖 pytest fixture）。
# ════════════════════════════════════════════════════════════════════
def _reset_content_backend_gate():
    import embedding_store
    embedding_store.content_backend_available = lambda: False


_orig_mkproj = _mkproj


def _mkproj(throughlines, threads):
    _reset_content_backend_gate()
    return _orig_mkproj(throughlines, threads)


def test_content_backend_ready_false_by_default():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        assert m._content_backend_ready() is False
    finally:
        embedding_store.content_backend_available = orig


def test_content_backend_ready_true_when_available():
    import embedding_store
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: True
    try:
        assert m._content_backend_ready() is True
    finally:
        embedding_store.content_backend_available = orig


def test_semantic_hit_catches_paraphrased_thread_name():
    """字面 substring 未命中（摘要用同义改写描述同一件事），内容后端语义余弦应补上命中——
    不误标 dormant（这正是本次升级要根治的漏检）。"""
    d = _mkproj(throughlines=[],
               threads=[{"id": "t1", "name": "复仇之路", "description": "对仇人的执念"}])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "他终于向杀父仇人寻仇"}]},
            ensure_ascii=False), encoding="utf-8")
    assert "复仇之路" not in json.dumps(
        json.loads((d / "_数据库" / "故事块摘要.json").read_text(encoding="utf-8")),
        ensure_ascii=False), "前置条件：字面 substring 必须不命中"

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_batch = embedding_store.compute_content_embeddings_batch
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _content_aware_embed(text):
        if ("复仇" in text) or ("仇人" in text) or ("寻仇" in text) or ("执念" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embeddings_batch = lambda texts: [_content_aware_embed(t) for t in texts]
    embedding_store.compute_content_embedding = _content_aware_embed
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = m.update(d, "001")
        assert r["subplot_updated"] == 1
        sub = json.loads((d / "_数据库" / "subplot_threads.json").read_text(encoding="utf-8"))
        assert sub["threads"][0]["match_method"] == "embedding_cosine"
        assert sub["threads"][0]["last_cluster"] == "001"
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embeddings_batch = orig_batch
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_semantic_hit_catches_paraphrased_throughline_str_schema():
    """四线脉络字符串列表 schema 下同样吃到语义补漏（举一反三·同函数同 bug 模式）。"""
    d = _mkproj(throughlines=["复仇之路"], threads=[])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "他终于向杀父仇人寻仇"}]},
            ensure_ascii=False), encoding="utf-8")

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _content_aware_embed(text):
        if ("复仇" in text) or ("仇人" in text) or ("寻仇" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = _content_aware_embed
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = m.update(d, "001")
        assert r["throughline_updated"] == 1
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_semantic_below_threshold_no_false_hit():
    """内容后端就绪但相似度 < 阈值 → 不误判命中（不是随便配了后端就无脑判中一切）。"""
    d = _mkproj(throughlines=[],
               threads=[{"id": "t1", "name": "复仇之路", "description": "执念"}])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "今天天气很好大家去郊游"}]},
            ensure_ascii=False), encoding="utf-8")

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _orthogonal_embed(text):
        # 名字/描述 embedding 与摘要 embedding 正交 → 相似度 0 < 阈值
        if ("复仇" in text) or ("执念" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.content_backend_available = lambda: True
    embedding_store.compute_content_embedding = _orthogonal_embed
    embedding_store.prefetch_content_embeddings = lambda texts: {
        "total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0, "available": True}
    try:
        r = m.update(d, "001")
        assert r["subplot_updated"] == 0
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single
        embedding_store.prefetch_content_embeddings = orig_prefetch


def test_update_content_backend_off_matches_original_literal_logic():
    """🔴 零回归锁：内容后端不可用 → update() 判定结果与原字面 substring 逻辑一致，且
    embedding_store.compute_content_embedding 即便被换成任意值也绝不会被调用
    （_embed_corpus_once 在最前面短路返回 None）。"""
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_single = embedding_store.compute_content_embedding

    def _boom(text):
        raise AssertionError("内容后端不可用时绝不应调用 compute_content_embedding")

    embedding_store.content_backend_available = lambda: False
    embedding_store.compute_content_embedding = _boom
    try:
        d = _mkproj(
            throughlines=[{"name": "主线复仇"}, {"name": "无关线"}],
            threads=[{"id": "t1", "name": "主线复仇"}, {"id": "t2", "name": "另一条"}])
        r = m.update(d, "001")
        assert r["throughline_updated"] == 1   # 只有「主线复仇」命中
        assert r["subplot_updated"] == 1
        sub = json.loads((d / "_数据库" / "subplot_threads.json").read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in sub["threads"]}
        assert by_id["t1"].get("last_cluster") == "001"
        assert by_id["t1"].get("match_method") == "literal_substring"
        assert "last_cluster" not in by_id["t2"]
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.compute_content_embedding = orig_single


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4 性能层：update() 批量 prefetch（corpus + 所有 thread/
# throughline query 一次性预热，其后 _embed_corpus_once / _thread_appears 内的
# 逐条 compute_content_embedding 全部命中缓存·内容后端子进程按条调用极贵）
# ════════════════════════════════════════════════════════════════════
def test_update_prefetches_all_queries_once_when_content_backend_ready():
    d = _mkproj(
        throughlines=[{"name": "复仇之路", "description": "对仇人的执念"}, "支线甲"],
        threads=[{"id": "t1", "name": "主线复仇", "description": "复仇进度"}, "配角线"])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "他终于向杀父仇人寻仇"}]},
            ensure_ascii=False), encoding="utf-8")

    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings
    orig_single = embedding_store.compute_content_embedding
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

    embedding_store.content_backend_available = lambda: True
    embedding_store.prefetch_content_embeddings = _rec_prefetch
    embedding_store.compute_content_embedding = lambda t: [0.0, 0.0]
    try:
        m.update(d, "001")
        assert len(calls) == 1, f"应恰好一次批量 prefetch·实际 {len(calls)} 次"
        texts = calls[0]
        summary_doc = json.loads(
            (d / "_数据库" / "故事块摘要.json").read_text(encoding="utf-8"))
        cluster_summary_text = json.dumps(summary_doc["clusters"][0], ensure_ascii=False)
        # corpus 摘要 + thread（dict/str 两种 schema）+ throughline（dict/str 两种 schema）
        # 全部收进同一次 prefetch
        assert cluster_summary_text in texts
        assert "主线复仇 复仇进度" in texts
        assert "配角线" in texts
        assert "复仇之路 对仇人的执念" in texts
        assert "支线甲" in texts
        assert len(texts) == 5
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.prefetch_content_embeddings = orig_prefetch
        embedding_store.compute_content_embedding = orig_single


def test_update_content_backend_off_never_calls_prefetch():
    """🔴 零回归锁：内容后端不可用 → prefetch_content_embeddings 完全不被调用
    （与既有字面逻辑零回归锁互补）。"""
    import embedding_store
    orig_avail = embedding_store.content_backend_available
    orig_prefetch = embedding_store.prefetch_content_embeddings

    def _boom(texts):
        raise AssertionError("内容后端不可用时绝不应调用 prefetch_content_embeddings")

    embedding_store.content_backend_available = lambda: False
    embedding_store.prefetch_content_embeddings = _boom
    try:
        d = _mkproj(throughlines=[{"name": "主线复仇"}],
                   threads=[{"id": "t1", "name": "主线复仇"}])
        r = m.update(d, "001")
        assert "subplot_updated" in r
    finally:
        embedding_store.content_backend_available = orig_avail
        embedding_store.prefetch_content_embeddings = orig_prefetch


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
