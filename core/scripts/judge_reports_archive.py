"""聚合一个故事块的 JudgeReport 与 required 评估信号。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atomic_json
import cluster_lookup

GRADE_TO_SCORE = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}


class JudgeArchiveError(ValueError):
    """required judge 来源缺失或合同不合法。"""


def _canonical_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise JudgeArchiveError(f"非法 cluster_id: {value!r}")
    return cluster_id


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise JudgeArchiveError(f"必需来源不存在: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise JudgeArchiveError(f"必需来源必须是 UTF-8 无 BOM: {path}")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JudgeArchiveError(f"必需来源损坏: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise JudgeArchiveError(f"必需来源顶层必须是 object: {path}")
    return value


def _require_cluster(document: dict, cluster_id: str, label: str) -> None:
    actual = document.get("cluster_id")
    if actual is None:
        raise JudgeArchiveError(f"{label}.cluster_id 缺失")
    if _canonical_cluster_id(actual) != cluster_id:
        raise JudgeArchiveError(f"{label}.cluster_id 与目标 {cluster_id} 不一致")


def _audit_grade(audit: dict) -> str:
    summary = audit.get("summary")
    if not isinstance(summary, dict):
        raise JudgeArchiveError("audit.summary 必须是 object")
    fatal = summary.get("fatal", 0)
    error = summary.get("error", 0)
    if (
        not isinstance(fatal, int) or isinstance(fatal, bool)
        or not isinstance(error, int) or isinstance(error, bool)
        or fatal < 0 or error < 0
    ):
        raise JudgeArchiveError("audit.summary fatal/error 必须是非负整数")
    if fatal:
        return "D"
    if error > 2:
        return "C"
    if error:
        return "B"
    return "A"


def build_audit_report(audit: dict, cluster_id: str) -> dict:
    issues = audit.get("issues")
    issues = issues if isinstance(issues, list) else []
    quotes = []
    for issue in issues[:2]:
        if isinstance(issue, dict):
            text = issue.get("evidence") or issue.get("desc") or issue.get("message")
            if isinstance(text, str) and text:
                quotes.append({"code": issue.get("code"), "quote": text[:240]})
    return {
        "schema_version": "1.0.cluster",
        "judge_id": "audit-hub",
        "cluster_id": cluster_id,
        "overall_grade": _audit_grade(audit),
        "confidence": 1.0,
        "verdict": audit.get("verdict"),
        "specific_findings": {
            "summary": audit.get("summary"),
            "issue_codes": [
                issue.get("code") for issue in issues
                if isinstance(issue, dict) and issue.get("code")
            ],
        },
        "evidence_quotes": quotes,
        "uncertainty_flags": [],
        "waivers": [],
    }


def _report_summary(path: Path, report: dict, project: Path) -> dict:
    judge_id = report.get("judge_id")
    if not isinstance(judge_id, str) or not judge_id:
        raise JudgeArchiveError(f"{path.name}.judge_id 缺失")
    waivers = report.get("waivers")
    waivers = waivers if isinstance(waivers, list) else []
    return {
        "judge_id": judge_id,
        "source": path.relative_to(project).as_posix(),
        "overall_grade": report.get("overall_grade"),
        "confidence": report.get("confidence"),
        "verdict": report.get("verdict"),
        "waivers": waivers,
    }


def _signal_summaries(changes: dict, summary: dict, reflection: dict) -> list[dict]:
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise JudgeArchiveError("changes.self_eval 必须是 object")
    entries = reflection.get("entries")
    if not isinstance(entries, list):
        raise JudgeArchiveError("reflection.entries 必须是 array")
    emotion = summary.get("emotion")
    if not isinstance(emotion, dict):
        raise JudgeArchiveError("summary.emotion 必须是 object")
    applied = self_eval.get("applied_style")
    applied = applied if isinstance(applied, dict) else {}
    return [
        {
            "signal_id": "writer-self-eval",
            "waiver_count": len(self_eval.get("waivers") or []),
            "applied_rule_count": len(applied.get("applied_rules") or []),
            "ending_type": applied.get("ending_type"),
        },
        {
            "signal_id": "summarizer",
            "scene_summary_count": len(summary.get("scene_summaries") or []),
            "key_detail_count": len(summary.get("key_details") or []),
            "emotion_value": emotion.get("value"),
        },
        {
            "signal_id": "reflector",
            "success_count": sum(
                entry.get("category") == "success" for entry in entries
                if isinstance(entry, dict)
            ),
            "failure_count": sum(
                entry.get("category") == "failure" for entry in entries
                if isinstance(entry, dict)
            ),
            "note": str(reflection.get("note") or "")[:160],
        },
    ]


def _dedupe_waivers(groups) -> list[dict]:
    output = []
    seen = set()
    for group in groups:
        for waiver in group if isinstance(group, list) else []:
            if not isinstance(waiver, dict):
                continue
            key = (str(waiver.get("code", "")), str(waiver.get("reason", "")))
            if key in seen:
                continue
            seen.add(key)
            output.append(waiver)
    return output


def _judge_score(reports: list[dict]) -> tuple[float | None, str | None]:
    scores = []
    for report in reports:
        grade = report.get("overall_grade")
        if isinstance(grade, str) and grade.upper() in GRADE_TO_SCORE:
            scores.append(GRADE_TO_SCORE[grade.upper()])
    if not scores:
        return None, None
    score = round(sum(scores) / len(scores), 2)
    grade = "A" if score >= 3.5 else "B" if score >= 2.5 else "C" if score >= 1.5 else "D"
    return score, grade


def _judge_paths(project: Path, cluster_id: str) -> list[Path]:
    directory = project / "_数据库" / ".judge_reports"
    excluded = {
        f"{cluster_id}_consensus.json",
        f"{cluster_id}_consensus_decision.json",
    }
    return sorted(
        path for path in directory.glob(f"{cluster_id}_*.json")
        if path.name not in excluded
    )


def build_rollup(project_root, cluster) -> tuple[dict, dict, Path]:
    project = Path(project_root)
    cluster_id = _canonical_cluster_id(cluster)
    database = project / "_数据库"
    draft_dir = project / "章节" / f"{cluster_id}_draft"
    audit_path = database / ".audit" / f"{cluster_id}_audit.json"
    summary_path = database / ".wal" / f"{cluster_id}_summary.json"
    reflection_path = database / ".wal" / f"{cluster_id}_reflection.json"
    changes_path = draft_dir / f"{cluster_id}_changes.json"
    truth_path = database / ".judge_reports" / f"{cluster_id}_writer-truth-check.json"
    foreshadower_path = database / ".judge_reports" / f"{cluster_id}_foreshadower.json"

    audit = _read_json(audit_path)
    summary = _read_json(summary_path)
    reflection = _read_json(reflection_path)
    changes = _read_json(changes_path)
    truth = _read_json(truth_path)
    foreshadower = _read_json(foreshadower_path)
    for label, document in (
        ("audit", audit), ("summary", summary), ("reflection", reflection),
        ("truth", truth), ("foreshadower", foreshadower),
    ):
        _require_cluster(document, cluster_id, label)

    audit_report = build_audit_report(audit, cluster_id)
    audit_report_path = database / ".judge_reports" / f"{cluster_id}_audit-hub.json"
    reports_by_path = {audit_report_path: audit_report}
    for path in _judge_paths(project, cluster_id):
        report = _read_json(path)
        _require_cluster(report, cluster_id, path.name)
        reports_by_path[path] = report
    reports_by_path[truth_path] = truth
    reports_by_path[foreshadower_path] = foreshadower

    report_summaries = [
        _report_summary(path, report, project)
        for path, report in sorted(reports_by_path.items(), key=lambda item: item[0].name)
    ]
    score, grade = _judge_score(report_summaries)
    writer_waivers = (changes.get("self_eval") or {}).get("waivers")
    waivers = _dedupe_waivers(
        [writer_waivers] + [report.get("waivers") for report in reports_by_path.values()]
    )
    rollup = {
        "schema_version": "cluster-judge-rollup.v1",
        "cluster_id": cluster_id,
        "reports": report_summaries,
        "signals": _signal_summaries(changes, summary, reflection),
        "judge_score": score,
        "judge_grade": grade,
        "waivers": waivers,
    }
    output_path = database / ".wal" / f"{cluster_id}_judge_reports_rollup.json"
    return rollup, audit_report, output_path


def archive_cluster(project_root, cluster, *, dry_run: bool = False) -> dict:
    project = Path(project_root)
    rollup, audit_report, output_path = build_rollup(project, cluster)
    cluster_id = rollup["cluster_id"]
    if not dry_run:
        audit_path = (
            project / "_数据库" / ".judge_reports" / f"{cluster_id}_audit-hub.json"
        )
        atomic_json.atomic_write_json(audit_path, audit_report)
        atomic_json.atomic_write_json(output_path, rollup)
    return rollup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="归档 cluster JudgeReport")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        rollup = archive_cluster(args.project, args.cluster, dry_run=args.dry_run)
    except (OSError, JudgeArchiveError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    mode = "DRY" if args.dry_run else "OK"
    print(
        f"[{mode}] {rollup['cluster_id']} judge rollup · reports={len(rollup['reports'])} "
        f"score={rollup['judge_score']} grade={rollup['judge_grade']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
