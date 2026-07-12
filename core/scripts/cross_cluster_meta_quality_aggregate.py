"""检查故事块长度与摘要指纹的长期稳定性。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


def _finding(severity: str, code: str, **fields: object) -> dict:
    return {"severity": severity, "gate_level": "advisory", "code": code, **fields}


def scan_length_distribution(records: list[dict]) -> list[dict]:
    lengths = [(str(record["cluster_id"]), int(record["word_count"])) for record in records]
    if len(lengths) < 3:
        return []
    counts = [count for _, count in lengths]
    ordered = sorted(counts)
    median = ordered[len(ordered) // 2]
    mean = sum(counts) / len(counts)
    variance = sum((count - mean) ** 2 for count in counts) / len(counts)
    deviation = variance ** 0.5
    coefficient = deviation / mean if mean > 0 else 0
    findings: list[dict] = []

    for cluster_id, count in lengths:
        if median > 1000 and abs(count - median) > median * 0.5:
            findings.append(_finding(
                "advisory", "LENGTH_OUTLIER",
                cluster_id=cluster_id,
                word_count=count,
                median=median,
                diff_pct=round((count - median) / median, 2),
            ))

    drop_streak = 0
    for index in range(1, len(lengths)):
        if lengths[index][1] < lengths[index - 1][1]:
            drop_streak += 1
            if drop_streak >= 3:
                window = lengths[index - 3:index + 1]
                findings.append(_finding(
                    "advisory", "LENGTH_TREND_DROP",
                    consecutive_clusters=[cluster_id for cluster_id, _ in window],
                    trail=[count for _, count in window],
                ))
                drop_streak = 0
        else:
            drop_streak = 0

    if len(lengths) >= 5 and coefficient > 0.4:
        findings.append(_finding(
            "advisory", "LENGTH_VARIANCE_HIGH",
            cv=round(coefficient, 2), mean=round(mean), std=round(deviation),
        ))
    return findings


def scan_summary_consistency(records: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for record in records:
        cluster_id = str(record["cluster_id"])
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            continue
        summary_length = len(re.findall(r"[一-鿿]", summary))
        if summary_length < 50:
            findings.append(_finding(
                "advisory", "SUMMARY_TOO_SHORT",
                cluster_id=cluster_id, summary_len=summary_length,
            ))
            continue
        fingerprint = [
            str(keyword) for keyword in record.get("text_keyword_set") or []
            if isinstance(keyword, str) and keyword
        ]
        if len(fingerprint) < 5:
            continue
        missing = [keyword for keyword in fingerprint if keyword not in summary]
        miss_ratio = len(missing) / len(fingerprint)
        if miss_ratio >= 0.6:
            findings.append(_finding(
                "warning", "SUMMARY_KEYWORD_MISMATCH",
                cluster_id=cluster_id,
                missing_keywords=missing[:8],
                miss_ratio=round(miss_ratio, 2),
            ))
    return findings


def _metrics(records: list[dict]) -> dict:
    counts = [record["word_count"] for record in records]
    return {
        "cluster_count": len(records),
        "total_word_count": sum(counts),
        "mean_word_count": round(sum(counts) / len(counts), 2),
        "min_word_count": min(counts),
        "max_word_count": max(counts),
        "summaries_with_keyword_fingerprint": sum(
            bool(record.get("text_keyword_set")) for record in records
        ),
    }


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    records = csr.get_clusters(project_root, last_n=last_n)
    if not records:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    findings = scan_length_distribution(records)
    findings.extend(scan_summary_consistency(records))
    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "meta_quality",
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
        out_path = out_dir / f"meta_quality_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[meta_quality] warning={report['summary']['warning']} "
              f"advisory={report['summary']['advisory']}")
        print(f"报告: {out_path}")
        return 2 if report["summary"]["warning"] else 1 if report["summary"]["advisory"] else 0
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
