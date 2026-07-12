"""cluster 摘要账本的严格写入合同。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import cluster_summary_store as store  # noqa: E402
from cluster_summary_reader import ClusterSummaryError, load_summary  # noqa: E402
from cluster_summary_fixtures import cluster_record  # noqa: E402


def test_initialize_creates_only_runtime_fields(tmp_path):
    document = store.initialize_summary(tmp_path)
    assert document == {
        "schema_version": "v2.cluster",
        "clusters": [],
        "volume_summaries": [],
    }
    assert load_summary(tmp_path) == document
    with pytest.raises(ClusterSummaryError, match="已存在"):
        store.initialize_summary(tmp_path)


def test_upsert_requires_existing_ledger_and_complete_new_record(tmp_path):
    with pytest.raises(ClusterSummaryError, match="不存在"):
        store.upsert_cluster(tmp_path, "cluster_001", cluster_record())

    store.initialize_summary(tmp_path)
    with pytest.raises(ClusterSummaryError, match="一次提供完整记录"):
        store.upsert_cluster(tmp_path, "cluster_001", {"title": "不完整"})

    store.upsert_cluster(tmp_path, "cluster_001", cluster_record(summary="首块"))
    result = load_summary(tmp_path)
    assert result["clusters"][0]["summary"] == "首块"


def test_upsert_merges_known_fields_without_chapter_shape(tmp_path):
    store.initialize_summary(tmp_path)
    store.upsert_cluster(
        tmp_path,
        "cluster_006",
        cluster_record(
            "cluster_006",
            state_delta={"time": {"elapsed": "1d"}, "locations": ["harbor"]},
            word_count=100,
        ),
    )
    store.upsert_cluster(
        tmp_path,
        6,
        {"state_delta": {"time": {"period": "night"}}, "word_count": 120},
    )
    record = load_summary(tmp_path)["clusters"][0]
    assert record["cluster_id"] == "cluster_006"
    assert record["word_count"] == 120
    assert record["state_delta"] == {
        "time": {"elapsed": "1d", "period": "night"},
        "locations": ["harbor"],
    }

    with pytest.raises(ClusterSummaryError, match="未知字段"):
        store.upsert_cluster(tmp_path, "cluster_006", {"chapter_range": [1, 2]})
    with pytest.raises(ClusterSummaryError, match="不一致"):
        store.upsert_cluster(
            tmp_path,
            "cluster_006",
            {"cluster_id": "cluster_007"},
        )


def test_replace_cluster_is_full_and_removes_stale_values(tmp_path):
    store.initialize_summary(tmp_path)
    original = cluster_record(
        "cluster_001", key_details=["old"], state_delta={"old": True}
    )
    store.upsert_cluster(tmp_path, "cluster_001", original)

    replacement = cluster_record("cluster_001", key_details=["new"])
    store.replace_cluster(tmp_path, "001", replacement)
    record = load_summary(tmp_path)["clusters"][0]
    assert record["key_details"] == ["new"]
    assert record["state_delta"] == {}
    assert "chapters" not in record

    with pytest.raises(ClusterSummaryError, match="字段不完整"):
        store.replace_cluster(tmp_path, "cluster_001", {"title": "缺字段"})
    extra = cluster_record("cluster_001")
    extra["chapter_range"] = [1, 2]
    with pytest.raises(ClusterSummaryError, match="字段不完整"):
        store.replace_cluster(tmp_path, "cluster_001", extra)


def test_corrupt_or_bom_ledger_is_never_repaired(tmp_path):
    database = tmp_path / "_数据库"
    database.mkdir()
    path = database / "故事块摘要.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ClusterSummaryError, match="损坏"):
        store.upsert_cluster(tmp_path, "cluster_001", cluster_record())

    path.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({
            "schema_version": "v2.cluster",
            "clusters": [],
            "volume_summaries": [],
        }).encode("utf-8")
    )
    with pytest.raises(ClusterSummaryError, match="BOM"):
        load_summary(tmp_path)


def test_volume_summary_upsert_is_strict_and_replacing(tmp_path):
    store.initialize_summary(tmp_path)
    store.upsert_volume_summary(tmp_path, 2, {
        "volume": 2,
        "summary": "第二卷摘要",
        "source": ["cluster_003"],
        "generated_at_cluster": "cluster_003",
        "emotional_peak": "旧港封锁",
    })
    store.upsert_volume_summary(tmp_path, 1, {
        "volume": 1,
        "summary": "第一卷摘要",
        "source": ["cluster_001", "cluster_002"],
        "generated_at_cluster": "cluster_002",
    })
    assert [row["volume"] for row in load_summary(tmp_path)["volume_summaries"]] == [1, 2]

    store.upsert_volume_summary(tmp_path, 2, {
        "volume": 2,
        "summary": "第二卷修订摘要",
        "source": ["cluster_003"],
        "generated_at_cluster": "cluster_003",
    })
    second = load_summary(tmp_path)["volume_summaries"][1]
    assert second["summary"] == "第二卷修订摘要"
    assert "emotional_peak" not in second

    with pytest.raises(ClusterSummaryError, match="不一致"):
        store.upsert_volume_summary(tmp_path, 2, {
            "volume": 3,
            "summary": "错卷",
            "source": ["cluster_003"],
            "generated_at_cluster": "cluster_003",
        })
    with pytest.raises(ClusterSummaryError, match="source 不能为空"):
        store.upsert_volume_summary(tmp_path, 3, {
            "volume": 3,
            "summary": "空来源",
            "source": [],
            "generated_at_cluster": "cluster_003",
        })
