"""knowledge_graph_update.py 测试（v29 · 从 事件簇.json 的 cluster locked_facts 读）。

回归锁：writer 链 v29 不自报 factual，changes.json 只有 self_eval；本脚本必须读
`事件簇.json → clusters[cid].locked_facts`（canonical 源，apply_archive 落库），
并写 plot_structure_scanner 真正读取的 `facts` 键（旧实现写 nodes/edges 但 scanner 读 facts →
全程 0 registered_facts）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from knowledge_graph_update import update_from_locked_facts  # noqa: E402


def _mk_project(root: Path, clusters: list, changes_self_eval_only: bool = True):
    """搭最小项目：事件簇.json(canonical locked_facts) + 空 knowledge_graph.json。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "knowledge_graph.json").write_text(
        json.dumps({"schema_version": "v29", "facts": [], "nodes": [], "edges": []},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )
    # v29 changes.json 只有 self_eval —— 证明脚本不再从 changes 读 factual
    if changes_self_eval_only:
        ch_dir = root / "章节" / "cluster_001_draft"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "cluster_001_changes.json").write_text(
            json.dumps({"self_eval": {"waivers": []},
                        "facts_locked": ["旧字段垃圾·必须被忽略"]}, ensure_ascii=False),
            encoding="utf-8",
        )


def test_populates_facts_key_from_locked_facts():
    """核心：写 plot_structure_scanner 真读的 facts 键。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, clusters=[{
            "cluster_id": "cluster_001",
            "locked_facts": [
                {"fact": "女娃是炎帝之女", "subject": "C_002"},
                {"fact": "东海吞人不还", "subject": "C_002"},
            ],
        }])
        result = update_from_locked_facts(root, "cluster_001")
        assert result["added_facts"] == 2
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert len(kg["facts"]) == 2  # ← scan_knowledge_graph registered_facts=2
        texts = [f["fact"] for f in kg["facts"]]
        assert "女娃是炎帝之女" in texts and "东海吞人不还" in texts
        assert all(f["source_cluster"] == "cluster_001" for f in kg["facts"])


def test_ignores_deprecated_changes_fields():
    """回归锁：不读 changes.json 的 v29-废弃 facts_locked 字段（否则 +0）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 事件簇 canonical 有 1 条；changes.json 里的 facts_locked 是垃圾，必须被忽略
        _mk_project(root, clusters=[{
            "cluster_id": "cluster_001",
            "locked_facts": [{"fact": "canonical 事实", "subject": "C_001"}],
        }])
        result = update_from_locked_facts(root, "cluster_001")
        assert result["added_facts"] == 1
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert [f["fact"] for f in kg["facts"]] == ["canonical 事实"]


def test_builds_character_fact_graph():
    """subject → fact asserts 边 + 角色/事实节点（角色↔事实图）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, clusters=[{
            "cluster_id": "cluster_001",
            "locked_facts": [
                {"fact": "事实A", "subject": "C_001"},
                {"fact": "事实B", "subject": "C_001"},
            ],
        }])
        update_from_locked_facts(root, "cluster_001")
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        char_nodes = [n for n in kg["nodes"] if n["type"] == "character"]
        fact_nodes = [n for n in kg["nodes"] if n["type"] == "fact"]
        assert len(char_nodes) == 1 and char_nodes[0]["label"] == "C_001"  # 角色去重
        assert len(fact_nodes) == 2
        asserts = [e for e in kg["edges"] if e["relation"] == "asserts"]
        assert len(asserts) == 2
        assert all(e["from"] == "C_001" for e in asserts)


def test_idempotent():
    """重跑不重复入（同 (subject, fact) 去重）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, clusters=[{
            "cluster_id": "cluster_001",
            "locked_facts": [{"fact": "事实X", "subject": "C_001"}],
        }])
        r1 = update_from_locked_facts(root, "cluster_001")
        assert r1["added_facts"] == 1
        r2 = update_from_locked_facts(root, "cluster_001")
        assert r2["added_facts"] == 0
        assert r2["skipped_dups"] == 1
        kg = json.loads((root / "_数据库" / "knowledge_graph.json").read_text(encoding="utf-8"))
        assert len(kg["facts"]) == 1


def test_accepts_short_key_form():
    """--cluster 006 与 cluster_006 都归一到 canonical。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, clusters=[{
            "cluster_id": "cluster_002",
            "locked_facts": [{"fact": "第二块事实", "subject": "C_003"}],
        }], changes_self_eval_only=False)
        result = update_from_locked_facts(root, "002")
        assert result["added_facts"] == 1


def test_missing_cluster_is_hard_error():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project(root, clusters=[{"cluster_id": "cluster_001", "locked_facts": []}],
                    changes_self_eval_only=False)
        try:
            update_from_locked_facts(root, "cluster_999")
            assert False, "缺少 cluster 应硬失败"
        except FileNotFoundError as exc:
            assert "cluster_999" in str(exc)
