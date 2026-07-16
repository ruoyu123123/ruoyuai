"""knowledge_graph_update.py — 从 事件簇.json 的 cluster locked_facts 增量写 knowledge_graph.json

v29 单一真理源：writer 链不再自报 factual，locked_facts 由 novel-archivist → apply_archive
确定性落到 `事件簇.json → clusters[cid].locked_facts`（每条 = {fact, subject, source_cluster}）。
本脚本读该 canonical 状态，确定性提取 角色↔事实 图，零 LLM 调用。

输出（_数据库/knowledge_graph.json）：
- facts: list[{id, fact, subject, source_cluster}]  ← plot_structure_scanner.scan_knowledge_graph 读 len(facts)
- nodes: list[fact 节点 + subject(角色) 节点]        ← 角色↔事实 图视图
- edges: list[{from(subject) → to(fact), relation:"asserts"}]  ← 角色→事实 established 边

纪律：
- 确定性（不调 LLM）
- 幂等（同 (subject, fact) 不重复入）
- cluster 为单位（北极星①）
- append-only（只追加不删旧）

退出码: 0 成功 / 2 输入缺失、JSON 损坏或 schema 错误

用法:
  python core/scripts/knowledge_graph_update.py <project_root> --cluster <cluster_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402


def _load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{p} JSON 解析失败: {exc}") from exc


def _save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fact_text(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("fact", "") or "").strip()
    return str(entry).strip()


def _subject(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("subject", "") or "").strip()
    return ""


def _node_key(node: dict) -> str:
    """用 type+label 做去重 key。"""
    return f"{node.get('type', '')}::{node.get('label', '')}"


def _cluster_locked_facts(project_root: Path, canonical_cid: str):
    """从 事件簇.json 取指定 cluster 的 locked_facts（canonical 源）。

    Returns list | None（None = 事件簇.json 无此 cluster）。
    """
    ec = _load_json(project_root / "_数据库" / "事件簇.json", {}) or {}
    for c in ec.get("clusters", []):
        cid = cluster_lookup.normalize_cluster_id(c.get("cluster_id"))
        if cid == canonical_cid:
            lf = c.get("locked_facts", [])
            if not isinstance(lf, list):
                raise RuntimeError(f"{canonical_cid}.locked_facts 必须是数组")
            return lf
    return None


def update_from_locked_facts(
    project_root: Path,
    cluster_id: str,
) -> dict:
    """从 事件簇.json 的 cluster locked_facts 增量写 knowledge_graph.json。

    Returns:
        {"added_facts": N, "added_nodes": N, "added_edges": N, "skipped_dups": N}
    """
    canonical_cid = cluster_lookup.normalize_cluster_id(cluster_id)
    if not canonical_cid:
        raise ValueError(f"无法归一 cluster_id: {cluster_id}")

    db = project_root / "_数据库"
    kg_path = db / "knowledge_graph.json"
    kg = _load_json(kg_path, {}) or {}
    kg.setdefault("schema_version", "v29")
    for key in ("facts", "nodes", "edges"):
        kg.setdefault(key, [])
        if not isinstance(kg[key], list):
            raise RuntimeError(f"knowledge_graph.json 的 {key} 必须是列表")

    locked = _cluster_locked_facts(project_root, canonical_cid)
    if locked is None:
        raise FileNotFoundError(f"事件簇.json 找不到 cluster: {cluster_id}")

    existing_facts = {(_subject(f), _fact_text(f)) for f in kg["facts"]}
    existing_nodes = {_node_key(n) for n in kg["nodes"]}
    existing_edges = {
        (e.get("from"), e.get("to"), e.get("relation")) for e in kg["edges"]
    }

    added_facts = added_nodes = added_edges = skipped = 0

    for entry in locked:
        fact = _fact_text(entry)
        if not fact:
            continue
        subject = _subject(entry)
        fkey = (subject, fact)
        if fkey in existing_facts:
            skipped += 1
            continue
        existing_facts.add(fkey)

        fact_id = f"KF_{len(kg['facts']) + 1:04d}"
        kg["facts"].append({
            "id": fact_id,
            "fact": fact,
            "subject": subject,
            "source_cluster": canonical_cid,
        })
        added_facts += 1

        # fact 节点
        fnode = {
            "type": "fact",
            "label": fact,
            "id": fact_id,
            "subject": subject,
            "source_cluster": canonical_cid,
        }
        fk = _node_key(fnode)
        if fk not in existing_nodes:
            kg["nodes"].append(fnode)
            existing_nodes.add(fk)
            added_nodes += 1

        # subject(角色) 节点 + 角色→事实 asserts 边（角色↔事实图核心）
        if subject:
            snode = {"type": "character", "label": subject}
            sk = _node_key(snode)
            if sk not in existing_nodes:
                kg["nodes"].append(snode)
                existing_nodes.add(sk)
                added_nodes += 1
            edge = (subject, fact, "asserts")
            if edge not in existing_edges:
                kg["edges"].append({
                    "from": subject,
                    "to": fact,
                    "relation": "asserts",
                    "source_cluster": canonical_cid,
                })
                existing_edges.add(edge)
                added_edges += 1

    _save_json(kg_path, kg)
    return {
        "added_facts": added_facts,
        "added_nodes": added_nodes,
        "added_edges": added_edges,
        "skipped_dups": skipped,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 事件簇.json 的 cluster locked_facts 增量写 knowledge_graph.json (确定性·零 LLM)"
    )
    ap.add_argument("project_root", help="workspace/novels/<书名>/")
    ap.add_argument("--cluster", required=True, help="cluster_id (如 cluster_001 / 001)")
    args = ap.parse_args()

    result = update_from_locked_facts(Path(args.project_root), args.cluster)
    print(f"[knowledge_graph] +{result['added_facts']} facts, "
          f"+{result['added_nodes']} nodes, "
          f"+{result['added_edges']} edges, "
          f"{result['skipped_dups']} dups skipped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[knowledge_graph] FATAL: {exc}", file=sys.stderr)
        raise SystemExit(2)
