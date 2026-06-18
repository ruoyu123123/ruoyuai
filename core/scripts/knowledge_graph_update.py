"""knowledge_graph_update.py — 从 cluster_changes 增量写 knowledge_graph.json

G3 调研发现: knowledge_graph.json 全程空→穿帮检测形同虚设。
本脚本从已有的 cluster_changes 确定性提取 nodes/edges,零新 LLM 调用。

数据源 (cluster_changes.json 已有字段):
- facts_locked: list[str] → 每条→1 node(type=fact)
- foreshadowing_planted: list[{id, desc}] → 每条→1 node(type=foreshadowing)
- foreshadowing_paid: list[{id, ...}] → 对已有 node 标 resolved=True

输出: 追加到 _数据库/knowledge_graph.json 的 nodes[] 和 edges[]

纪律:
- 确定性(不调 LLM)
- 幂等(同 fact 不重复入)
- cluster 为单位(北极星①)
- 只追加不删旧(append-only)

用法:
  python core/scripts/knowledge_graph_update.py <project_root> --cluster <cluster_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _node_key(node: dict) -> str:
    """用 type+label 做去重 key。"""
    return f"{node.get('type', '')}::{node.get('label', '')}"


def update_from_changes(
    project_root: Path,
    cluster_id: str,
) -> dict:
    """从 cluster_changes 增量写 knowledge_graph.json。

    Returns:
        {"added_nodes": N, "added_edges": N, "skipped_dups": N}
    """
    db = project_root / "_数据库"
    kg_path = db / "knowledge_graph.json"
    kg = _load_json(kg_path)

    if "nodes" not in kg:
        kg["nodes"] = []
    if "edges" not in kg:
        kg["edges"] = []
    if "schema_version" not in kg:
        kg["schema_version"] = "v1"

    # 已有 node key 集合(去重)
    existing_keys = {_node_key(n) for n in kg["nodes"]}

    # 找 cluster_changes
    changes_candidates = [
        project_root / "章节" / f"{cluster_id}_draft" / f"{cluster_id}_changes.json",
        project_root / "章节" / f"cluster_{cluster_id}_draft" / f"cluster_{cluster_id}_changes.json",
    ]
    changes_path = next((p for p in changes_candidates if p.exists()), None)
    if changes_path is None:
        return {"added_nodes": 0, "added_edges": 0, "skipped_dups": 0, "error": "changes not found"}

    changes = _load_json(changes_path)
    added_nodes = 0
    added_edges = 0
    skipped = 0
    ts = datetime.now().isoformat(timespec="seconds")

    # 1. facts_locked → nodes(type=fact)
    for fact in changes.get("facts_locked", []):
        label = fact if isinstance(fact, str) else str(fact)
        node = {
            "type": "fact",
            "label": label,
            "source_cluster": cluster_id,
            "locked_at": ts,
        }
        key = _node_key(node)
        if key in existing_keys:
            skipped += 1
            continue
        kg["nodes"].append(node)
        existing_keys.add(key)
        added_nodes += 1

    # 2. foreshadowing_planted → nodes(type=foreshadowing)
    for fs in changes.get("foreshadowing_planted", []):
        if isinstance(fs, dict):
            label = fs.get("id") or fs.get("desc", "")[:50]
            desc = fs.get("desc", "")
        else:
            label = str(fs)[:50]
            desc = str(fs)
        node = {
            "type": "foreshadowing",
            "label": label,
            "description": desc,
            "source_cluster": cluster_id,
            "planted_at": ts,
            "resolved": False,
        }
        key = _node_key(node)
        if key in existing_keys:
            skipped += 1
            continue
        kg["nodes"].append(node)
        existing_keys.add(key)
        added_nodes += 1

    # 3. foreshadowing_paid → 标记已有伏笔 node resolved=True
    for paid in changes.get("foreshadowing_paid", []):
        paid_id = paid.get("id") if isinstance(paid, dict) else str(paid)
        for n in kg["nodes"]:
            if n.get("type") == "foreshadowing" and n.get("label") == paid_id:
                if not n.get("resolved"):
                    n["resolved"] = True
                    n["resolved_at"] = ts
                    n["resolved_cluster"] = cluster_id

    # 4. facts 之间加 edges(同 cluster 的 facts 互相关联)
    cluster_facts = [
        n for n in kg["nodes"]
        if n.get("type") == "fact" and n.get("source_cluster") == cluster_id
    ]
    if len(cluster_facts) >= 2:
        # 同 cluster 内的 facts 两两关联(简单策略)
        existing_edges = {
            (e.get("from"), e.get("to")) for e in kg["edges"]
        }
        for i, f1 in enumerate(cluster_facts):
            for f2 in cluster_facts[i + 1:]:
                pair = (f1["label"], f2["label"])
                if pair not in existing_edges:
                    kg["edges"].append({
                        "from": f1["label"],
                        "to": f2["label"],
                        "relation": "co_established",
                        "source_cluster": cluster_id,
                    })
                    existing_edges.add(pair)
                    added_edges += 1

    _save_json(kg_path, kg)
    return {
        "added_nodes": added_nodes,
        "added_edges": added_edges,
        "skipped_dups": skipped,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 cluster_changes 增量写 knowledge_graph.json (确定性·零 LLM)"
    )
    ap.add_argument("project_root", help="workspace/novels/<书名>/")
    ap.add_argument("--cluster", required=True, help="cluster_id (如 cluster_001)")
    args = ap.parse_args()

    result = update_from_changes(Path(args.project_root), args.cluster)
    print(f"[knowledge_graph] +{result['added_nodes']} nodes, "
          f"+{result['added_edges']} edges, "
          f"{result['skipped_dups']} dups skipped")
    if result.get("error"):
        print(f"  ⚠️ {result['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
