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
# 🔴 2026-07-02 embedding 语义补漏接线（真后端命中 + 门控关零回归）
# 参考范式：topic_drift_scanner._has_real_embedding_backend（本仓约定每文件自留一份）
# 沿用本仓既有测试惯例：手工 os.environ 存/复 + 直接换 embedding_store.compute_embedding
# 属性（不用 pytest monkeypatch fixture · 兼容本文件的 __main__ 自跑器）。
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
        assert m._has_real_embedding_backend() is False
    finally:
        _restore_embed_env(bak_eb, bak_gen)


def test_has_real_embedding_backend_true_when_set():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "fake-real"
        assert m._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_hit_catches_paraphrased_thread_name():
    """字面 substring 未命中（摘要用同义改写描述同一件事），真后端语义余弦应补上命中——
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

    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _content_aware_embed(text):
        if ("复仇" in text) or ("仇人" in text) or ("寻仇" in text) or ("执念" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.compute_embedding = _content_aware_embed
    try:
        r = m.update(d, "001")
        assert r["subplot_updated"] == 1
        sub = json.loads((d / "_数据库" / "subplot_threads.json").read_text(encoding="utf-8"))
        assert sub["threads"][0]["match_method"] == "embedding_cosine"
        assert sub["threads"][0]["last_cluster"] == "001"
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_hit_catches_paraphrased_throughline_str_schema():
    """四线脉络字符串列表 schema 下同样吃到语义补漏（举一反三·同函数同 bug 模式）。"""
    d = _mkproj(throughlines=["复仇之路"], threads=[])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "他终于向杀父仇人寻仇"}]},
            ensure_ascii=False), encoding="utf-8")

    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _content_aware_embed(text):
        if ("复仇" in text) or ("仇人" in text) or ("寻仇" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.compute_embedding = _content_aware_embed
    try:
        r = m.update(d, "001")
        assert r["throughline_updated"] == 1
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_semantic_below_threshold_no_false_hit():
    """真后端就绪但相似度 < 阈值 → 不误判命中（不是随便配了后端就无脑判中一切）。"""
    d = _mkproj(throughlines=[],
               threads=[{"id": "t1", "name": "复仇之路", "description": "执念"}])
    (d / "_数据库" / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "今天天气很好大家去郊游"}]},
            ensure_ascii=False), encoding="utf-8")

    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _orthogonal_embed(text):
        # 名字/描述 embedding 与摘要 embedding 正交 → 相似度 0 < 阈值
        if ("复仇" in text) or ("执念" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.compute_embedding = _orthogonal_embed
    try:
        r = m.update(d, "001")
        assert r["subplot_updated"] == 0
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_update_gate_off_matches_original_literal_logic():
    """🔴 零回归锁：门控关（无 EMBED_BACKEND / 无 GEN_EMBED__*）→ update() 判定结果与原字面
    substring 逻辑一致，且 embedding_store.compute_embedding 即便被换成任意值也绝不会被调用
    （_embed_corpus_once 在最前面短路返回 None）。"""
    bak_eb, bak_gen = _clear_embed_env()
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise AssertionError("门控关时绝不应调用 compute_embedding")

    embedding_store.compute_embedding = _boom
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
        embedding_store.compute_embedding = orig
        _restore_embed_env(bak_eb, bak_gen)


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
