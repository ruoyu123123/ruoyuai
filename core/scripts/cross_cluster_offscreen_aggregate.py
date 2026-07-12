"""Audit offscreen action execution at story-cluster granularity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402


def _merge_offscreen(cluster: dict) -> dict:
    """读取 cluster 级幕后行动汇总。"""
    direct = cluster.get("offscreen")
    return direct if isinstance(direct, dict) else {}


def _scan_from_ledger(project_root: Path, last_n_clusters: int = 5):
    """Return findings, per-cluster metrics, and scanned cluster IDs."""
    clusters = csr.get_clusters(project_root)
    if last_n_clusters > 0:
        clusters = clusters[-last_n_clusters:]
    last_cluster_id = str(clusters[-1].get("cluster_id") or "") if clusters else ""
    findings: list[dict] = []
    per_cluster: dict[str, dict] = {}
    scanned = []
    for cluster in clusters:
        cid = str(cluster.get("cluster_id") or "")
        if not cid:
            continue
        scanned.append(cid)
        offscreen = _merge_offscreen(cluster)
        expected = offscreen.get("expected") or []
        executed = offscreen.get("executed") or []
        backlog = offscreen.get("backlog_count", 0) or 0
        per_cluster[cid] = {
            "expected_count": len(expected),
            "executed_count": len(executed),
            "expected_characters": sorted({a.get("character") for a in expected if isinstance(a, dict) and a.get("character")}),
            "executed_characters": sorted({a.get("character") for a in executed if isinstance(a, dict) and a.get("character")}),
            "backlog_count": backlog,
        }
        if expected and not executed:
            findings.append({
                "severity": "warning",
                "code": "OFFSCREEN_ACTIONS_NOT_EXECUTED",
                "cluster_id": cid,
                "metric": {"expected": len(expected), "executed": 0},
                "expected_actions": expected,
                "suggestion": "当前 cluster 的预期幕后行动没有执行证据",
            })
        for action in executed:
            if not isinstance(action, dict):
                continue
            evidence = str(action.get("evidence") or "")
            if len(evidence) < 10:
                findings.append({
                    "severity": "advisory",
                    "code": "OFFSCREEN_EVIDENCE_THIN",
                    "cluster_id": cid,
                    "metric": {"character": action.get("character", ""), "evidence_len": len(evidence)},
                    "suggestion": "幕后行动证据需要具体到本 cluster 的正文落点",
                })
        if cid == last_cluster_id and backlog >= 1:
            findings.append({
                "severity": "advisory",
                "code": "OFFSCREEN_BACKLOG",
                "cluster_id": cid,
                "metric": {"backlog_count": backlog},
                "suggestion": "当前 cluster 仍有未完成的幕后行动",
            })
    return findings, per_cluster, scanned


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=5)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    findings, per_cluster, scanned = _scan_from_ledger(project_root, args.last_n)
    if not scanned:
        print("[SKIP] no completed cluster records")
        raise SystemExit(0)
    summary = {
        "warning": sum(f["severity"] == "warning" for f in findings),
        "advisory": sum(f["severity"] == "advisory" for f in findings),
        "total": len(findings),
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "offscreen",
        "scan_ts": ts,
        "clusters_scanned": scanned,
        "per_cluster": per_cluster,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"offscreen_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[offscreen] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
