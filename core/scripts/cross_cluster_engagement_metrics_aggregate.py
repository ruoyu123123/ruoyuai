"""汇总故事块级钩子、开场验证与结尾类型趋势。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


_HOOK_ISSUE = "READER_EXP_HOOK_STRENGTH"
_HOOK_SCANNER = "hook_strength_scanner"
_CLIFF_TYPES = ("cliffhanger", "悬念", "钩子型", "悬念型", "悬念断章")
_CLIFF_RATIO_THRESHOLD = 0.25
_CLIFF_STREAK_THRESHOLD = 3


def _score(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in ("score", "overall", "value"):
            nested = value.get(key)
            if isinstance(nested, (int, float)) and not isinstance(nested, bool):
                return float(nested)
    return None


def _scanner_ran(audit: dict, scanner: str) -> bool:
    statuses = audit.get("scanner_status")
    if not isinstance(statuses, list):
        return False
    return any(
        isinstance(status, dict)
        and status.get("scanner") == scanner
        and status.get("ok") is True
        for status in statuses
    )


def collect_cluster_metrics(records: list[dict]) -> dict:
    metrics = {
        "hook": [],
        "golden_kindling": [],
        "golden_hook": [],
        "golden_turn": [],
        "truth_checks": 0,
        "truth_failures": 0,
        "ending_type_mismatches": 0,
        "ending_type_advisories": [],
    }
    for record in records:
        cluster_id = str(record["cluster_id"])
        audit = record.get("audit") or {}
        summary = audit.get("summary") if isinstance(audit, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        hook_value = _score(summary.get("hook_strength"))
        issues = audit.get("issues") if isinstance(audit, dict) else []
        issues = issues if isinstance(issues, list) else []
        hook_failed = any(
            isinstance(issue, dict) and issue.get("code") == _HOOK_ISSUE
            for issue in issues
        )
        if hook_value is not None:
            metrics["hook"].append((cluster_id, hook_value))
        elif hook_failed or _scanner_ran(audit, _HOOK_SCANNER):
            metrics["hook"].append((cluster_id, 0.0 if hook_failed else 1.0))

        golden = summary.get("golden_three")
        if isinstance(golden, dict):
            for key in ("kindling", "hook", "turn"):
                value = _score(golden.get(key))
                if value is not None:
                    metrics[f"golden_{key}"].append((cluster_id, value))

        truth = record.get("truth_check") or {}
        if isinstance(truth, dict) and truth:
            metrics["truth_checks"] += 1
            if str(truth.get("verdict") or "").lower() not in ("pass", "ok"):
                metrics["truth_failures"] += 1
            # ending_type_advisory 非空 = 作者标自由文学标签，detector 有限分类法无法同法比对
            # （writer_truth_check 已判定非事实谎）→ 不计 mismatch，单列 advisory 口径。
            advisory = truth.get("ending_type_advisory")
            if truth.get("ending_type_match") is False:
                if isinstance(advisory, dict) and advisory:
                    metrics["ending_type_advisories"].append({
                        "cluster_id": cluster_id,
                        "declared": advisory.get("declared"),
                        "detected": advisory.get("actual"),
                    })
                else:
                    metrics["ending_type_mismatches"] += 1
    return metrics


def scan_ending_type_advisories(advisories: list[dict]) -> list[dict]:
    """作者自由文学 ending 标签占比过半 → 提示 detector 分类法与作者标签体系脱节。

    advisory-only：标签体系分歧是作者用词自由（北极星⑤），不是质量缺陷，
    只在跨 cluster 视野上给一次口径提示，绝不逐 cluster 报 issue。
    """
    if len(advisories) < 3:
        return []
    return [{
        "severity": "advisory",
        "gate_level": "advisory",
        "code": "ENDING_TYPE_TAXONOMY_DRIFT",
        "metric": "ending_type_advisory_count",
        "count": len(advisories),
        "clusters": [item["cluster_id"] for item in advisories],
        "declared_labels": sorted({
            str(item.get("declared") or "") for item in advisories if item.get("declared")
        }),
    }]


def scan_hook_trend(scores: list[tuple[str, float]]) -> list[dict]:
    if len(scores) < 3:
        return []
    findings: list[dict] = []
    decline_streak = 0
    for index in range(1, len(scores)):
        if scores[index][1] < scores[index - 1][1]:
            decline_streak += 1
            if decline_streak >= 3:
                findings.append({
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "HOOK_STRENGTH_DECLINE",
                    "trail": scores[index - 3:index + 1],
                })
                decline_streak = 0
        else:
            decline_streak = 0
    low_clusters = [cluster_id for cluster_id, score in scores if score < 0.4]
    if len(low_clusters) >= 3:
        findings.append({
            "severity": "advisory",
            "gate_level": "advisory",
            "code": "HOOK_PERSISTENT_LOW",
            "low_clusters": low_clusters,
        })
    return findings


def scan_golden_trend(metrics: dict) -> list[dict]:
    findings: list[dict] = []
    for key in ("golden_kindling", "golden_hook", "golden_turn"):
        scores = metrics.get(key) or []
        if len(scores) < 3:
            continue
        decline_streak = 0
        for index in range(1, len(scores)):
            if scores[index][1] < scores[index - 1][1]:
                decline_streak += 1
                if decline_streak >= 3:
                    findings.append({
                        "severity": "advisory",
                        "gate_level": "advisory",
                        "code": "GOLDEN_DEGRADATION",
                        "metric": key,
                        "trail": scores[index - 3:index + 1],
                    })
                    decline_streak = 0
            else:
                decline_streak = 0
        if len(scores) >= 5:
            recent = [score for _, score in scores[-5:]]
            if max(recent) - min(recent) < 0.1:
                findings.append({
                    "severity": "advisory",
                    "gate_level": "advisory",
                    "code": "GOLDEN_FLAT",
                    "metric": key,
                    "range": [min(recent), max(recent)],
                })
    return findings


def _is_cliffhanger(ending_type: object) -> bool:
    if not isinstance(ending_type, str):
        return False
    normalized = ending_type.strip().lower()
    return any(tag.lower() in normalized for tag in _CLIFF_TYPES)


def collect_ending_types(records: list[dict]) -> list[tuple[str, str]]:
    return [
        (str(record["cluster_id"]), record["ending_type"].strip())
        for record in records
        if isinstance(record.get("ending_type"), str) and record["ending_type"].strip()
    ]


def scan_cliffhanger_quota(endings: list[tuple[str, str]]) -> list[dict]:
    if not endings:
        return []
    findings: list[dict] = []
    cliff_clusters = [cluster_id for cluster_id, ending in endings if _is_cliffhanger(ending)]
    ratio = len(cliff_clusters) / len(endings)
    if len(endings) >= 4 and ratio > _CLIFF_RATIO_THRESHOLD:
        findings.append({
            "severity": "advisory",
            "gate_level": "advisory",
            "code": "CLIFFHANGER_QUOTA_OVER",
            "metric": "cliffhanger_ratio",
            "ratio": round(ratio, 3),
            "threshold": _CLIFF_RATIO_THRESHOLD,
            "cliffhanger_clusters": cliff_clusters,
            "clusters_scanned": len(endings),
        })

    streak: list[str] = []
    longest: list[str] = []
    for cluster_id, ending in endings:
        if _is_cliffhanger(ending):
            streak.append(cluster_id)
            if len(streak) > len(longest):
                longest = list(streak)
        else:
            streak = []
    if len(longest) >= _CLIFF_STREAK_THRESHOLD:
        findings.append({
            "severity": "advisory",
            "gate_level": "advisory",
            "code": "CLIFFHANGER_QUOTA_OVER",
            "metric": "consecutive_cliffhanger_streak",
            "streak": len(longest),
            "threshold": _CLIFF_STREAK_THRESHOLD,
            "cluster_range": [longest[0], longest[-1]],
        })
    return findings


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    records = csr.get_clusters(project_root, last_n=last_n)
    if not records:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    metrics = collect_cluster_metrics(records)
    findings = scan_hook_trend(metrics["hook"])
    findings.extend(scan_golden_trend(metrics))
    findings.extend(scan_cliffhanger_quota(collect_ending_types(records)))
    findings.extend(scan_ending_type_advisories(metrics["ending_type_advisories"]))
    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "engagement_metrics",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": [str(record["cluster_id"]) for record in records],
        "metrics_collected": {
            "hook": len(metrics["hook"]),
            "golden_kindling": len(metrics["golden_kindling"]),
            "golden_hook": len(metrics["golden_hook"]),
            "golden_turn": len(metrics["golden_turn"]),
            "truth_checks": metrics["truth_checks"],
            "truth_failures": metrics["truth_failures"],
            "ending_type_mismatches": metrics["ending_type_mismatches"],
            "ending_type_advisories": len(metrics["ending_type_advisories"]),
        },
        "findings": findings,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=None)
    args = parser.parse_args()
    try:
        report = build_report(Path(args.project), last_n=args.last_n)
        out_dir = Path(args.project) / "_数据库" / ".cross_cluster_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"engagement_metrics_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[engagement_metrics] warning={report['summary']['warning']} "
              f"advisory={report['summary']['advisory']}")
        print(f"报告: {out_path}")
        return 2 if report["summary"]["warning"] else 1 if report["summary"]["advisory"] else 0
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
