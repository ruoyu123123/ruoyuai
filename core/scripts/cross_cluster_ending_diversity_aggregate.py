"""Audit ending-type variety across story clusters.

Only the cluster summary ledger is authoritative. Nested chapter values are
used as precomputed evidence when a cluster-level ending type is absent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402


def _rec_ending_type(record: dict) -> str:
    if not isinstance(record, dict):
        return ""
    value = record.get("ending_type")
    if isinstance(value, str) and value:
        return value
    self_eval = record.get("self_eval")
    if isinstance(self_eval, dict):
        applied = self_eval.get("applied_style")
        if isinstance(applied, dict) and isinstance(applied.get("ending_type"), str):
            return applied["ending_type"]
    applied = record.get("applied_style")
    return applied.get("ending_type", "") if isinstance(applied, dict) else ""


def _cluster_ending_type(cluster: dict) -> str:
    value = _rec_ending_type(cluster)
    if value:
        return value
    nested = cluster.get("chapters") or {}
    values = [_rec_ending_type(record) for record in nested.values() if isinstance(record, dict)]
    values = [value for value in values if value]
    return Counter(values).most_common(1)[0][0] if values else ""


def _collect(project_root: Path, last_n: int) -> list[dict]:
    clusters = csr.get_clusters(project_root)
    if last_n > 0:
        clusters = clusters[-last_n:]
    return [{"cluster_id": str(c.get("cluster_id")), "ending_type": _cluster_ending_type(c)}
            for c in clusters if c.get("cluster_id")]


def _scan(observations: list[dict]) -> list[dict]:
    findings: list[dict] = []
    missing = [o["cluster_id"] for o in observations if not o["ending_type"]]
    if len(missing) >= 3:
        findings.append({
            "severity": "warning",
            "code": "ENDING_TYPE_MISSING",
            "missing_clusters": missing,
            "suggestion": "多个 cluster 没有 ending_type，无法进行跨块节奏审计",
        })
    valid = [o for o in observations if o["ending_type"]]
    if len(valid) < 3:
        return findings
    counts = Counter(o["ending_type"] for o in valid)
    total = len(valid)
    dominant, count = counts.most_common(1)[0]
    if count / total > 0.5:
        findings.append({
            "severity": "warning",
            "code": "ENDING_TYPE_MONOTONE",
            "dominant_ending_type": dominant,
            "pct": round(count / total, 2),
            "distribution": dict(counts),
            "suggestion": "ending_type 过度集中，检查 cluster 收束方式是否单一",
        })
    if total >= 5 and len(counts) <= 2:
        findings.append({
            "severity": "advisory",
            "code": "ENDING_TYPE_LOW_DIVERSITY",
            "unique_count": len(counts),
            "distribution": dict(counts),
            "suggestion": "当前 cluster 的 ending_type 种类过少",
        })
    current = valid[0]["ending_type"]
    run_ids = [valid[0]["cluster_id"]]
    for observation in valid[1:]:
        if observation["ending_type"] == current:
            run_ids.append(observation["cluster_id"])
            if len(run_ids) >= 4:
                findings.append({
                    "severity": "advisory",
                    "code": "ENDING_TYPE_RUN",
                    "ending_type": current,
                    "consecutive_clusters": run_ids[-4:],
                    "suggestion": "连续 cluster 使用同一种 ending_type，应增加收束变化",
                })
                run_ids = [observation["cluster_id"]]
        else:
            current = observation["ending_type"]
            run_ids = [observation["cluster_id"]]
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    observations = _collect(project_root, args.last_n)
    if not observations:
        print("[SKIP] no completed cluster records")
        raise SystemExit(0)
    findings = _scan(observations)
    summary = {
        "warning": sum(f["severity"] == "warning" for f in findings),
        "advisory": sum(f["severity"] == "advisory" for f in findings),
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    valid = [o for o in observations if o["ending_type"]]
    report = {
        "scan_type": "ending_diversity",
        "scan_ts": ts,
        "clusters_scanned": [o["cluster_id"] for o in observations],
        "cluster_observations": observations,
        "ending_distribution": dict(Counter(o["ending_type"] for o in valid)),
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"ending_diversity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ending_diversity] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
