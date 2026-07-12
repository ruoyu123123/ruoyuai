"""生成当前故事块的大势软窗口漂移报告。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import atomic_json
import cluster_lookup
import fate_engine


def build_report(project_root: Path, cluster_id: str) -> dict:
    current_cluster = cluster_lookup.normalize_cluster_id(cluster_id)
    if not current_cluster:
        raise ValueError(f"非法 cluster_id: {cluster_id}")
    drift_result = fate_engine.drift(project_root, current_cluster)
    findings = []
    for overdue in drift_result["overdue_events"]:
        severity = "warning" if overdue["overdue_by_clusters"] >= 2 else "advisory"
        findings.append({
            "severity": severity,
            "gate_level": "advisory",
            "code": "FATE_EVENT_OVERDUE",
            "metric": overdue,
            "message": (
                f"大事件「{overdue['title']}」({overdue['event_id']}) "
                f"已超过软窗口 {overdue['overdue_by_clusters']} 个故事块"
            ),
            "suggestion": f"建议下一故事块优先考虑推进 {overdue['event_id']}：{overdue['title']}",
        })
    return {
        "scan_type": "fate_drift",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "cluster_id": current_cluster,
        "findings": findings,
        "summary": {
            "warning": sum(item["severity"] == "warning" for item in findings),
            "advisory": sum(item["severity"] == "advisory" for item in findings),
            "total": len(findings),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        project_root = Path(args.project)
        report = build_report(project_root, args.cluster)
        out_dir = project_root / "_数据库" / ".cross_cluster_scan"
        out_path = out_dir / f"fate_drift_{report['cluster_id']}.json"
        atomic_json.atomic_write_json(out_path, report)
        print(f"[fate_drift_scan] {report['cluster_id']}: {report['summary']['total']} 项漂移")
        print(f"报告: {out_path}")
        return 1 if report["summary"]["warning"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
