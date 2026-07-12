"""故事块摘要 reader 的严格 cluster 合同回归。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cluster_summary_reader as reader  # noqa: E402


def _record(cluster_id="cluster_001", word_count=100):
    return {
        "cluster_id": cluster_id,
        "title": "测试块",
        "summary": "完整故事块摘要",
        "scene_summaries": [],
        "key_details": [],
        "emotion": {},
        "anchor_delivery": {},
        "word_count": word_count,
        "text_keyword_set": [],
        "pattern_metrics": {},
        "idiom_hits": {},
        "characters": [],
        "char_mention_counts": {},
        "char_emotion_counts": {},
        "locations_mentioned": [],
        "ending_type": "",
        "ending_line": "",
        "time_transition_present": False,
        "structure": {},
        "throughline_progress": {},
        "stress": {},
        "moves_used": [],
        "position_effect_evals": [],
        "outcome": "neutral",
        "offscreen": {},
        "state_delta": {},
        "relationship_changes": [],
        "item_changes": [],
        "locked_facts": [],
        "audit": {},
        "truth_check": {},
        "judge_reports": [],
        "judge_score": None,
        "judge_grade": None,
        "waivers": [],
    }


def _write(root: Path, payload: dict):
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / reader.SUMMARY_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_missing_summary_is_hard_failure(tmp_path):
    with pytest.raises(reader.ClusterSummaryError, match="不存在"):
        reader.load_summary(tmp_path)


def test_corrupt_summary_is_hard_failure(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / reader.SUMMARY_FILENAME).write_text("{bad", encoding="utf-8")
    with pytest.raises(reader.ClusterSummaryError, match="损坏"):
        reader.load_summary(tmp_path)


def test_old_schema_and_unknown_fields_are_rejected(tmp_path):
    _write(tmp_path, {"schema_version": "v2", "clusters": [], "volume_summaries": []})
    with pytest.raises(reader.ClusterSummaryError, match="schema_version"):
        reader.load_summary(tmp_path)
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [], "volume_summaries": [],
        "chapters": [],
    })
    with pytest.raises(reader.ClusterSummaryError, match="未知字段"):
        reader.load_summary(tmp_path)


def test_cluster_record_requires_complete_native_shape(tmp_path):
    record = _record()
    record["chapters"] = {}
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [record], "volume_summaries": [],
    })
    with pytest.raises(reader.ClusterSummaryError, match="未知字段"):
        reader.load_summary(tmp_path)


def test_get_clusters_is_cluster_ordered_and_last_n_is_cluster_count(tmp_path):
    _write(tmp_path, {
        "schema_version": "v2.cluster",
        "clusters": [_record("cluster_002", 2), _record("cluster_001", 1)],
        "volume_summaries": [],
    })
    assert [row["cluster_id"] for row in reader.get_clusters(tmp_path)] == [
        "cluster_001", "cluster_002"
    ]
    assert [row["cluster_id"] for row in reader.get_clusters(tmp_path, last_n=1)] == [
        "cluster_002"
    ]
    with pytest.raises(reader.ClusterSummaryError):
        reader.get_clusters(tmp_path, last_n=0)


def test_volume_summary_contract_and_duplicate_detection(tmp_path):
    volume = {
        "volume": 1,
        "summary": "卷摘要",
        "source": ["cluster_001"],
        "generated_at_cluster": "cluster_001",
    }
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [], "volume_summaries": [volume],
    })
    assert reader.load_summary(tmp_path)["volume_summaries"][0]["volume"] == 1
    volume["legacy_chapters"] = [1]
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [], "volume_summaries": [volume],
    })
    with pytest.raises(reader.ClusterSummaryError, match="未知字段"):
        reader.load_summary(tmp_path)


def test_volume_summary_requires_nonempty_unique_cluster_sources(tmp_path):
    volume = {
        "volume": 1,
        "summary": "卷摘要",
        "source": [],
        "generated_at_cluster": "cluster_001",
    }
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [], "volume_summaries": [volume],
    })
    with pytest.raises(reader.ClusterSummaryError, match="source 不能为空"):
        reader.load_summary(tmp_path)
    volume["source"] = ["cluster_001", "cluster_001"]
    _write(tmp_path, {
        "schema_version": "v2.cluster", "clusters": [], "volume_summaries": [volume],
    })
    with pytest.raises(reader.ClusterSummaryError, match="不得重复"):
        reader.load_summary(tmp_path)


def test_bom_is_rejected(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / reader.SUMMARY_FILENAME).write_bytes(
        b"\xef\xbb\xbf" + json.dumps({
            "schema_version": "v2.cluster", "clusters": [], "volume_summaries": []
        }).encode("utf-8")
    )
    with pytest.raises(reader.ClusterSummaryError, match="BOM"):
        reader.load_summary(tmp_path)
