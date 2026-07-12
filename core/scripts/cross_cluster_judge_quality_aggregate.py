"""汇总故事块级 Judge 分数、评级、报告与豁免趋势。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


_GRADE_TO_SCORE = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}
_PLATEAU_DELTA = 0.1
_VOLATILITY_STD = 0.45


def _finding(severity: str, code: str, **fields: object) -> dict:
    return {"severity": severity, "gate_level": "advisory", "code": code, **fields}


def scan_judge_scores(records: list[dict]) -> list[dict]:
    scores = [
        (str(record["cluster_id"]), float(record["judge_score"]))
        for record in records
        if isinstance(record.get("judge_score"), (int, float))
        and not isinstance(record.get("judge_score"), bool)
    ]
    if len(scores) < 3:
        return []
    findings: list[dict] = []
    decline_streak = 0
    for index in range(1, len(scores)):
        if scores[index][1] < scores[index - 1][1]:
            decline_streak += 1
            if decline_streak >= 3:
                findings.append(_finding(
                    "warning", "JUDGE_SCORE_DECLINE",
                    trail=scores[index - 3:index + 1],
                ))
                decline_streak = 0
        else:
            decline_streak = 0

    if len(scores) >= 5:
        recent = scores[-5:]
        values = [value for _, value in recent]
        minimum, maximum = min(values), max(values)
        if maximum - minimum < _PLATEAU_DELTA:
            findings.append(_finding(
                "advisory", "JUDGE_SCORE_PLATEAU",
                range=[minimum, maximum],
                trail_clusters=[cluster_id for cluster_id, _ in recent],
            ))
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        deviation = variance ** 0.5
        if deviation > _VOLATILITY_STD:
            findings.append(_finding(
                "advisory", "JUDGE_SCORE_VOLATILITY",
                std=round(deviation, 2), mean=round(mean, 2),
                trail_clusters=[cluster_id for cluster_id, _ in recent],
            ))
    return findings


def scan_waiver_accumulation(records: list[dict]) -> list[dict]:
    findings: list[dict] = []
    codes_by_cluster: list[tuple[str, set[str]]] = []
    for record in records:
        cluster_id = str(record["cluster_id"])
        waivers = record.get("waivers") or []
        if len(waivers) >= 5:
            findings.append(_finding(
                "warning", "WAIVER_RUNAWAY",
                cluster_id=cluster_id, waiver_count=len(waivers),
            ))
        codes_by_cluster.append((cluster_id, {
            str(waiver.get("code"))
            for waiver in waivers
            if isinstance(waiver, dict) and waiver.get("code")
        }))

    all_codes = sorted({code for _, codes in codes_by_cluster for code in codes})
    for waived_code in all_codes:
        streak: list[str] = []
        longest: list[str] = []
        for cluster_id, codes in codes_by_cluster:
            if waived_code in codes:
                streak.append(cluster_id)
                if len(streak) > len(longest):
                    longest = list(streak)
            else:
                streak = []
        if len(longest) >= 3:
            findings.append(_finding(
                "advisory", "WAIVER_PERSISTENT_CODE",
                waived_code=waived_code,
                consecutive_clusters=len(longest),
                cluster_range=[longest[0], longest[-1]],
            ))
    return findings


def scan_report_disagreement(records: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for record in records:
        scores = []
        for report in record.get("judge_reports") or []:
            if not isinstance(report, dict):
                continue
            grade = report.get("overall_grade")
            if isinstance(grade, str) and grade.upper() in _GRADE_TO_SCORE:
                scores.append(_GRADE_TO_SCORE[grade.upper()])
        if len(scores) >= 2 and max(scores) - min(scores) >= 2:
            findings.append(_finding(
                "advisory", "JUDGE_REPORT_DISAGREEMENT",
                cluster_id=str(record["cluster_id"]),
                score_range=[min(scores), max(scores)],
                report_count=len(scores),
            ))
    return findings


def _metrics(records: list[dict]) -> dict:
    grade_distribution = Counter(
        str(record["judge_grade"])
        for record in records
        if isinstance(record.get("judge_grade"), str) and record["judge_grade"]
    )
    return {
        "scored_clusters": sum(record.get("judge_score") is not None for record in records),
        "graded_clusters": sum(record.get("judge_grade") is not None for record in records),
        "judge_reports": sum(len(record.get("judge_reports") or []) for record in records),
        "waivers": sum(len(record.get("waivers") or []) for record in records),
        "grade_distribution": dict(sorted(grade_distribution.items())),
    }


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    records = csr.get_clusters(project_root, last_n=last_n)
    if not records:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    findings = scan_judge_scores(records)
    findings.extend(scan_waiver_accumulation(records))
    findings.extend(scan_report_disagreement(records))
    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "judge_quality",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": [str(record["cluster_id"]) for record in records],
        "metrics": _metrics(records),
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
        out_path = out_dir / f"judge_quality_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[judge_quality] warning={report['summary']['warning']} "
              f"advisory={report['summary']['advisory']}")
        print(f"报告: {out_path}")
        return 2 if report["summary"]["warning"] else 1 if report["summary"]["advisory"] else 0
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
