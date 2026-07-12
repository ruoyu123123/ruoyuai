#!/usr/bin/env python3
"""用 cluster 钩子、节律、结尾与长度合成留存代理指标。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402
import cross_cluster_engagement_metrics_aggregate as engagement  # noqa: E402
import cross_cluster_sagging_middle_aggregate as sagging  # noqa: E402


ISSUE_CODE = "RETENTION_PROXY_LOW"
DEFAULT_WEIGHTS = {
    "hook": 0.35,
    "sagging": 0.25,
    "cliffhanger": 0.25,
    "length": 0.15,
}
RETENTION_LOW_FLOOR = 0.45
CLUSTER_CJK_RANGE = (10_000, 25_000)


def _mode() -> str:
    mode = (os.environ.get("READER_RETENTION_PROXY_MODE") or "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "shadow"


def _normalize_hook(score: float) -> float:
    value = score if score <= 1.0 else score / 10.0
    return max(0.0, min(1.0, value))


def _hook_score(records: list[dict]) -> float:
    scores = engagement.collect_cluster_metrics(records)["hook"]
    if not scores:
        raise csr.ClusterSummaryError("留存代理缺少 cluster hook_strength 遥测")
    return sum(_normalize_hook(score) for _, score in scores) / len(scores)


def _sagging_score(records: list[dict]) -> float:
    report = sagging.build_report(records, mode="shadow")
    weighted_hits = (
        report["summary"]["advisory"]
        + report["summary"]["warning"] * 2
    )
    return max(0.0, 1.0 - min(1.0, weighted_hits * 0.2))


def _cliffhanger_score(records: list[dict]) -> float:
    endings = engagement.collect_ending_types(records)
    if not endings:
        raise csr.ClusterSummaryError("留存代理缺少 cluster ending_type")
    cliff_count = sum(
        1 for _, ending_type in endings if engagement._is_cliffhanger(ending_type)
    )
    ratio_score = 1.0 - cliff_count / len(endings)
    findings = engagement.scan_cliffhanger_quota(endings)
    streak_penalty = 0.2 if any(
        finding.get("metric") == "consecutive_cliffhanger_streak"
        for finding in findings
    ) else 0.0
    return max(0.0, ratio_score - streak_penalty)


def _length_health(records: list[dict]) -> float:
    low, high = CLUSTER_CJK_RANGE
    healthy = sum(low <= record["word_count"] <= high for record in records)
    return healthy / len(records)


def aggregate(records: list[dict], weights: dict | None = None) -> tuple[dict, list[dict]]:
    if not records:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    selected_weights = weights or DEFAULT_WEIGHTS
    if set(selected_weights) != set(DEFAULT_WEIGHTS):
        raise ValueError(f"weights 必须完整包含 {sorted(DEFAULT_WEIGHTS)}")

    hook = _hook_score(records)
    sagging_inverse = _sagging_score(records)
    cliffhanger = _cliffhanger_score(records)
    length = _length_health(records)
    proxy = (
        selected_weights["hook"] * hook
        + selected_weights["sagging"] * sagging_inverse
        + selected_weights["cliffhanger"] * cliffhanger
        + selected_weights["length"] * length
    )
    summary = {
        "clusters_examined": [str(record["cluster_id"]) for record in records],
        "hook_norm": round(hook, 3),
        "sagging_inverse": round(sagging_inverse, 3),
        "cliffhanger": round(cliffhanger, 3),
        "length_health": round(length, 3),
        "retention_proxy": round(proxy, 3),
        "weights": selected_weights,
    }
    findings = []
    if proxy < RETENTION_LOW_FLOOR:
        findings.append({
            "severity": "advisory",
            "gate_level": "advisory",
            "code": ISSUE_CODE,
            "suggestion": (
                f"retention proxy {proxy:.3f} < {RETENTION_LOW_FLOOR}；"
                "优先改善得分最低的 cluster 维度"
            ),
            "metrics": summary,
        })
    return summary, findings


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    records = csr.get_clusters(project_root, last_n=last_n)
    summary, findings = aggregate(records)
    return {
        "scan_type": "reader_retention_proxy",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": summary["clusters_examined"],
        "gate_level": "advisory",
        "summary": summary,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="cluster 留存代理")
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=None)
    args = parser.parse_args()
    mode = _mode()
    if mode == "off":
        print("[SKIP] READER_RETENTION_PROXY_MODE=off")
        return 0

    project_root = Path(args.project).resolve()
    try:
        report = build_report(project_root, args.last_n)
        out_dir = project_root / "_数据库" / ".cross_cluster_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = out_dir / f"reader_retention_proxy_{stamp}.json"
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    print(f"[reader_retention_proxy] findings={len(report['findings'])} → {out_path}")
    if mode == "shadow":
        return 0
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
