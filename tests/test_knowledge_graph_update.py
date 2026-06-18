"""P0-S2 测试: knowledge_graph_update.py 确定性增量写入。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from knowledge_graph_update import update_from_changes  # noqa: E402


def _mk_project(root: Path, facts: list, foreshadowing: list = None, paid: list = None):
    """搭最小项目结构。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "knowledge_graph.json").write_text(
        json.dumps({"schema_version": "v1", "nodes": [], "edges": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    ch_dir = root / "章节" / "cluster_001_draft"
    ch_dir.mkdir(parents=True, exist_ok=True)
    changes = {
        "facts_locked": facts,
        "foreshadowing_planted": foreshadowing or [],
        "foreshadowing_paid": paid or [],
    }
    (ch_dir / "cluster_001_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False),
        encoding="utf-8",
    )


def test_adds_facts_as_nodes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, facts=["事实A", "事实B"])
        result = update_from_changes(root, "cluster_001")
        assert result["added_nodes"] == 2
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        labels = [n["label"] for n in kg["nodes"]]
        assert "事实A" in labels
        assert "事实B" in labels
        assert all(n["type"] == "fact" for n in kg["nodes"])


def test_adds_foreshadowing_nodes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, facts=[], foreshadowing=[
            {"id": "fs_001", "desc": "第一条伏笔描述"}
        ])
        result = update_from_changes(root, "cluster_001")
        assert result["added_nodes"] == 1
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert kg["nodes"][0]["type"] == "foreshadowing"
        assert kg["nodes"][0]["resolved"] is False


def test_idempotent():
    """重跑不重复入。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, facts=["事实X"])
        r1 = update_from_changes(root, "cluster_001")
        assert r1["added_nodes"] == 1
        r2 = update_from_changes(root, "cluster_001")
        assert r2["added_nodes"] == 0
        assert r2["skipped_dups"] == 1
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert len(kg["nodes"]) == 1


def test_marks_foreshadowing_resolved():
    """foreshadowing_paid 标记已有伏笔 resolved=True。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, facts=[], foreshadowing=[{"id": "fs_001", "desc": "伏笔"}])
        update_from_changes(root, "cluster_001")
        # 第二个 cluster 回收伏笔
        ch2 = root / "章节" / "cluster_002_draft"
        ch2.mkdir(parents=True, exist_ok=True)
        (ch2 / "cluster_002_changes.json").write_text(
            json.dumps({
                "facts_locked": [],
                "foreshadowing_planted": [],
                "foreshadowing_paid": [{"id": "fs_001"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        update_from_changes(root, "cluster_002")
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        fs_node = [n for n in kg["nodes"] if n["type"] == "foreshadowing"][0]
        assert fs_node["resolved"] is True
        assert fs_node["resolved_cluster"] == "cluster_002"


def test_co_established_edges():
    """同 cluster 内 facts 产 co_established edges。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, facts=["A", "B", "C"])
        update_from_changes(root, "cluster_001")
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert len(kg["edges"]) == 3  # C(3,2) = 3


def test_missing_changes_returns_error():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "_数据库").mkdir(parents=True)
        (root / "_数据库" / "knowledge_graph.json").write_text(
            '{"nodes":[],"edges":[]}', encoding="utf-8"
        )
        result = update_from_changes(root, "cluster_999")
        assert result["added_nodes"] == 0
        assert "error" in result


def test_real_data_凿窍纪():
    """真数据: 凿窍纪 cluster_001 应产 7 nodes。"""
    real = REPO / "workspace" / "novels" / "凿窍纪"
    if not real.exists():
        return
    # 不修改真文件——读已有的 kg 验证
    kg = json.loads(
        (real / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8")
    )
    assert len(kg["nodes"]) >= 7
    fact_nodes = [n for n in kg["nodes"] if n["type"] == "fact"]
    assert len(fact_nodes) >= 5
