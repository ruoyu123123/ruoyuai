"""按 cluster archive 检查关系变化覆盖与当前数值边界。

archive 记录每个 cluster 客观发生的关系建立/改变；关系.json 保存当前投影。
本顾问只判断这两类权威数据能够证明的事项。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_state_sources as css


DIMENSIONS = ("affinity", "trust", "fear", "respect")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def collect_recent_changes(project_root: Path, last_n: int) -> tuple[list[str], set[tuple[str, str]]]:
    cluster_ids = []
    changed_pairs = set()
    for cluster_id, _record in css.iter_completed_clusters(project_root, last_n):
        cluster_ids.append(cluster_id)
        archive = css.load_archive(project_root, cluster_id)
        for relationship in archive.get("relationships", []) or []:
            if not isinstance(relationship, dict):
                continue
            source, target = relationship.get("from"), relationship.get("to")
            if source and target:
                changed_pairs.add((source, target))
    return cluster_ids, changed_pairs


def scan(project_root: Path, last_n: int) -> dict:
    relationships = (load_json(project_root / "_数据库" / "关系.json", {}) or {}).get("relationships", []) or []
    cluster_ids, changed_pairs = collect_recent_changes(project_root, last_n)
    findings = []

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        source, target = relationship.get("from"), relationship.get("to")
        if not source or not target:
            continue
        for dimension in DIMENSIONS:
            value = relationship.get(dimension)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not -10 <= value <= 10:
                findings.append({
                    "severity": "warning",
                    "code": "RELATIONSHIP_OUT_OF_BOUND",
                    "from": source,
                    "to": target,
                    "dimension": dimension,
                    "value": value,
                    "suggestion": "将关系投影归一到 [-10, 10]",
                })

    if len(cluster_ids) >= 8:
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            pair = (relationship.get("from"), relationship.get("to"))
            if all(pair) and pair not in changed_pairs:
                findings.append({
                    "severity": "advisory",
                    "code": "RELATIONSHIP_FROZEN",
                    "from": pair[0],
                    "to": pair[1],
                    "window_clusters": cluster_ids,
                    "suggestion": f"近 {len(cluster_ids)} 个 cluster 无关系变化；结合正文判断是否符合人物走向",
                })

    return {"clusters_scanned": cluster_ids, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=15)
    args = parser.parse_args()
    project_root = Path(args.project)
    result = scan(project_root, args.last_n)
    findings = result["findings"]
    report = {
        "scan_type": "relationship_trend",
        "scan_ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
        **result,
        "summary": {
            "warning": sum(item["severity"] == "warning" for item in findings),
            "advisory": sum(item["severity"] == "advisory" for item in findings),
        },
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"relationship_trend_{report['scan_ts']}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[relationship_trend] clusters={len(result['clusters_scanned'])} findings={len(findings)}")
    if report["summary"]["warning"]:
        return 2
    return 1 if report["summary"]["advisory"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
