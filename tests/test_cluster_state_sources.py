"""Canonical cluster 状态源读取测试。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import cluster_state_sources as sources  # noqa: E402


def test_completed_clusters_order_by_cluster_id_not_chapter_range(tmp_path):
    database = tmp_path / "_数据库"
    database.mkdir()
    (database / "故事块摘要.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_010", "chapter_range": [1, 2], "status": "done"},
        {"cluster_id": "cluster_002", "chapter_range": [90, 99], "status": "done"},
        {"cluster_id": "cluster_003", "chapter_range": [20, 30], "status": "candidate"},
    ]}, ensure_ascii=False), encoding="utf-8")
    assert [cluster_id for cluster_id, _ in sources.iter_completed_clusters(tmp_path)] == [
        "cluster_002", "cluster_010"
    ]


def test_completed_clusters_last_n_uses_cluster_order(tmp_path):
    database = tmp_path / "_数据库"
    database.mkdir()
    (database / "故事块摘要.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_011", "status": "done"},
        {"cluster_id": "cluster_001", "status": "done"},
        {"cluster_id": "cluster_007", "status": "done"},
    ]}), encoding="utf-8")
    assert [cluster_id for cluster_id, _ in sources.iter_completed_clusters(tmp_path, 2)] == [
        "cluster_007", "cluster_011"
    ]
