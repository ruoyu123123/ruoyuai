"""knowledge_store + knowledge_collector 核心测试。

钉死：
  · add/query/stats 基本 CRUD
  · category 校验
  · query 按 quality 降序 + tags 过滤 + limit
  · backfill 从真实蒸馏产物提取到条目
  · collect_from_writing 成功/失败模式反馈
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))


def _with_temp_knowledge(fn):
    """临时知识库目录跑测试。"""
    import knowledge_store as ks
    saved = ks._KNOWLEDGE_ROOT
    with tempfile.TemporaryDirectory() as d:
        ks._KNOWLEDGE_ROOT = Path(d)
        try:
            return fn(ks)
        finally:
            ks._KNOWLEDGE_ROOT = saved


# ---------- knowledge_store ----------

def test_add_and_query():
    def _test(ks):
        ks.add("genre", "xuanhuan", "修仙境界体系参考", source="test", tags=["修仙"])
        ks.add("genre", "xuanhuan", "打脸节奏参考", source="test", tags=["打脸"], quality=0.9)
        items = ks.query("genre", "xuanhuan")
        assert len(items) == 2
        assert items[0]["quality"] == 0.9  # 按 quality 降序
    _with_temp_knowledge(_test)


def test_query_tags_filter():
    def _test(ks):
        ks.add("technique", "sensory", "视觉描写", tags=["视觉"])
        ks.add("technique", "sensory", "嗅觉描写", tags=["嗅觉"])
        items = ks.query("technique", "sensory", tags=["嗅觉"])
        assert len(items) == 1
        assert "嗅觉" in items[0]["content"]
    _with_temp_knowledge(_test)


def test_query_min_quality():
    def _test(ks):
        ks.add("genre", "xianxia", "低质量", quality=0.2)
        ks.add("genre", "xianxia", "高质量", quality=0.8)
        items = ks.query("genre", "xianxia", min_quality=0.5)
        assert len(items) == 1
        assert "高质量" in items[0]["content"]
    _with_temp_knowledge(_test)


def test_query_limit():
    def _test(ks):
        for i in range(20):
            ks.add("research", "findings", f"条目{i}", quality=i / 20)
        items = ks.query("research", "findings", limit=5)
        assert len(items) == 5
    _with_temp_knowledge(_test)


def test_stats():
    def _test(ks):
        ks.add("genre", "test", "a")
        ks.add("genre", "test", "b")
        ks.add("technique", "test", "c")
        s = ks.stats()
        assert s["genre"] == 2
        assert s["technique"] == 1
        assert s["total"] == 3
    _with_temp_knowledge(_test)


def test_invalid_category():
    def _test(ks):
        try:
            ks.add("invalid_category", "test", "content")
            assert False, "should raise"
        except ValueError:
            pass
    _with_temp_knowledge(_test)


def test_query_for_genre():
    def _test(ks):
        ks.add("genre", "xuanhuan", "玄幻知识", tags=["xuanhuan"])
        ks.add("research", "findings", "玄幻调研", tags=["xuanhuan"])
        items = ks.query_for_genre("xuanhuan")
        assert len(items) == 2
    _with_temp_knowledge(_test)


# ---------- knowledge_collector ----------

def test_collect_from_writing():
    def _test(ks):
        import knowledge_collector as kc
        kc.ks = ks  # 注入临时知识库
        scanner_results = {
            "premature_resolution": {
                "verdict": "FAIL_MINOR",
                "violations": [{"kind": "premature_resolution", "message": "冲突消解过快"}]
            },
            "reveal_show": {"verdict": "PASS"}
        }
        kc.collect_from_writing(Path("/tmp/test_project"), "cluster_001", scanner_results)
        items = ks.query("technique", "failure_patterns")
        assert len(items) == 1
        assert "premature_resolution" in items[0]["content"]
    _with_temp_knowledge(_test)


def test_backfill_produces_entries():
    """真实回填：从已有蒸馏产物中提取到至少 10 条知识。"""
    import knowledge_store as ks
    s = ks.stats()
    assert s["total"] >= 10, f"backfill should have produced >=10 entries, got {s['total']}"
