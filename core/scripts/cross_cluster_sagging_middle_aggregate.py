"""检测故事块序列中段的反转、压力和推进力塌陷。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402


CODE_REVERSAL_VOID = "SAGGING_MIDDLE_REVERSAL_VOID"
CODE_STAKES_FLAT = "SAGGING_MIDDLE_STAKES_FLAT"
CODE_PURPOSE_VOID = "SAGGING_MIDDLE_PURPOSE_VOID"
CODE_NEEDS_BOMB = "SAGGING_MIDDLE_NEEDS_MIDPOINT_BOMB"

MIDDLE_LO = 0.40
MIDDLE_HI = 0.60
TURN_SCORE_FLOOR = 0.50
HOOK_SCORE_FLOOR = 0.45
PURPOSE_VOID_STREAK = 3

DRIVE_PURPOSE_KEYWORDS = {
    "reveal": ("reveal", "揭露", "揭晓", "真相浮出"),
    "reversal": ("reversal", "反转", "逆转", "局势突变"),
    "escalate": ("escalate", "升级", "恶化", "危机加深"),
    "decision": ("decision", "抉择", "决定", "作出选择"),
    "transformation": ("transformation", "转化", "蜕变", "身份改变"),
}


def _mode() -> str:
    mode = (os.environ.get("SAGGING_MIDDLE_MODE") or "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "shadow"


def _middle_slice(clusters: list[dict]) -> list[dict]:
    """返回所选 cluster 序列的 40%-60% 区段。"""
    count = len(clusters)
    if count < 5:
        return []
    start = int(count * MIDDLE_LO)
    stop = max(start + 1, int(count * MIDDLE_HI) + 1)
    return clusters[start:stop]


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _audit_scores(record: dict) -> tuple[float | None, float | None]:
    audit = record.get("audit")
    audit_summary = audit.get("summary") if isinstance(audit, dict) else None
    if not isinstance(audit_summary, dict):
        return None, None
    golden_three = audit_summary.get("golden_three")
    turn_score = (
        _number(golden_three.get("turn"))
        if isinstance(golden_three, dict)
        else None
    )
    hook_strength = audit_summary.get("hook_strength")
    hook_value = (
        _number(hook_strength.get("score"))
        if isinstance(hook_strength, dict)
        else None
    )
    return turn_score, hook_value


def _scene_summary_texts(record: dict) -> list[str]:
    values = record.get("scene_summaries")
    if not isinstance(values, list):
        return []
    return [
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def _cluster_purpose_tags(record: dict) -> set[str]:
    text = "\n".join(_scene_summary_texts(record)).casefold()
    return {
        purpose
        for purpose, keywords in DRIVE_PURPOSE_KEYWORDS.items()
        if any(keyword.casefold() in text for keyword in keywords)
    }


def _cluster_has_drive(record: dict) -> bool:
    return bool(_cluster_purpose_tags(record))


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def detect_reversal_void(middle: list[dict]) -> dict:
    """检测中段是否既无推进目的，也无足够反转或钩子评分。"""
    turn_scores: list[float] = []
    hook_values: list[float] = []
    drive_clusters: list[str] = []
    for record in middle:
        turn_score, hook_value = _audit_scores(record)
        if turn_score is not None:
            turn_scores.append(turn_score)
        if hook_value is not None:
            hook_values.append(hook_value)
        if _cluster_has_drive(record):
            drive_clusters.append(str(record["cluster_id"]))

    turn_average = _average(turn_scores)
    hook_average = _average(hook_values)
    weak_turn = turn_average is None or turn_average < TURN_SCORE_FLOOR
    weak_hook = hook_average is None or hook_average < HOOK_SCORE_FLOOR
    return {
        "hit": bool(middle) and not drive_clusters and weak_turn and weak_hook,
        "turn_average": round(turn_average, 3) if turn_average is not None else None,
        "hook_average": round(hook_average, 3) if hook_average is not None else None,
        "drive_clusters": drive_clusters,
        "turn_samples": len(turn_scores),
        "hook_samples": len(hook_values),
    }


def detect_stakes_flat(middle: list[dict]) -> dict:
    """检测压力不抬升且场景推进目的重复的中段。"""
    stress_points: list[tuple[str, float]] = []
    purpose_tags: set[str] = set()
    scene_summary_clusters = 0
    for record in middle:
        stress = record.get("stress")
        value = stress.get("new_total") if isinstance(stress, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stress_points.append((str(record["cluster_id"]), float(value)))
        tags = _cluster_purpose_tags(record)
        purpose_tags.update(tags)
        if _scene_summary_texts(record):
            scene_summary_clusters += 1

    stress_values = [value for _, value in stress_points]
    stress_range = max(stress_values) - min(stress_values) if stress_values else None
    stress_rising = bool(stress_values) and any(
        value > stress_values[0] + 1 for value in stress_values[1:]
    )
    enough_evidence = len(stress_values) >= 3 and scene_summary_clusters >= 3
    repetitive_purpose = len(purpose_tags) <= 2
    return {
        "hit": enough_evidence and not stress_rising and repetitive_purpose,
        "stress_points": stress_points,
        "stress_range": round(stress_range, 3) if stress_range is not None else None,
        "stress_rising": stress_rising,
        "purpose_tags": sorted(purpose_tags),
        "purpose_tag_count": len(purpose_tags),
        "scene_summary_clusters": scene_summary_clusters,
    }


def detect_purpose_void(middle: list[dict]) -> dict:
    """检测连续三个 cluster 没有推进型场景目的。"""
    streak = 0
    max_streak = 0
    void_clusters: list[str] = []
    for record in middle:
        if _cluster_has_drive(record):
            streak = 0
            continue
        streak += 1
        max_streak = max(max_streak, streak)
        if streak >= PURPOSE_VOID_STREAK:
            void_clusters.append(str(record["cluster_id"]))
    return {
        "hit": max_streak >= PURPOSE_VOID_STREAK,
        "max_void_streak": max_streak,
        "void_clusters": void_clusters,
    }


def _finding(code: str, metrics: dict, suggestion: str) -> dict:
    return {
        "severity": "advisory",
        "gate_level": "advisory",
        "code": code,
        "metrics": metrics,
        "suggestion": suggestion,
    }


def build_report(clusters: list[dict], *, mode: str) -> dict:
    if not clusters:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")

    middle = _middle_slice(clusters)
    reversal = detect_reversal_void(middle)
    stakes = detect_stakes_flat(middle)
    purpose = detect_purpose_void(middle)
    findings: list[dict] = []
    if reversal["hit"]:
        findings.append(_finding(
            CODE_REVERSAL_VOID,
            reversal,
            "中段缺少推进目的，反转与钩子评分也未达到阈值",
        ))
    if stakes["hit"]:
        findings.append(_finding(
            CODE_STAKES_FLAT,
            stakes,
            "中段压力未抬升且场景推进目的重复，建议引入质变升级",
        ))
    if purpose["hit"]:
        findings.append(_finding(
            CODE_PURPOSE_VOID,
            purpose,
            "连续三个以上 cluster 未出现揭露、反转、升级、抉择或转化",
        ))

    signal_count = len(findings)
    if signal_count >= 2:
        findings.append({
            "severity": "warning",
            "gate_level": "advisory",
            "code": CODE_NEEDS_BOMB,
            "hit_signals": signal_count,
            "suggestion": "中段同时命中多项塌陷信号，建议下一 cluster 安排重大转折",
        })

    return {
        "scan_type": "sagging_middle",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "clusters_scanned": [str(record["cluster_id"]) for record in clusters],
        "middle_cluster_ids": [str(record["cluster_id"]) for record in middle],
        "middle_formed": bool(middle),
        "reversal": reversal,
        "stakes": stakes,
        "purpose": purpose,
        "needs_midpoint_bomb": signal_count > 0,
        "findings": findings,
        "summary": {
            "advisory": sum(item["severity"] == "advisory" for item in findings),
            "warning": sum(item["severity"] == "warning" for item in findings),
            "total": len(findings),
        },
    }


def _write_outputs(project_root: Path, report: dict) -> Path:
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = out_dir / f"sagging_middle_{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    snapshot = {
        "needs_midpoint_bomb": report["needs_midpoint_bomb"],
        "hit_signals": report["summary"]["advisory"],
        "middle_cluster_ids": report["middle_cluster_ids"],
        "advisory_codes": [
            item["code"] for item in report["findings"]
            if item["severity"] == "advisory"
        ],
    }
    (out_dir / "sagging_middle_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="检测 cluster 序列中段塌陷")
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=None)
    args = parser.parse_args()

    mode = _mode()
    if mode == "off":
        print("[OFF] SAGGING_MIDDLE_MODE=off")
        return 0

    project_root = Path(args.project).resolve()
    try:
        report = build_report(
            csr.get_clusters(project_root, last_n=args.last_n), mode=mode
        )
        report_path = _write_outputs(project_root, report)
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    summary = report["summary"]
    print(
        f"[sagging_middle] middle={len(report['middle_cluster_ids'])} cluster "
        f"advisory={summary['advisory']} warning={summary['warning']}"
    )
    print(f"报告: {report_path}")
    if mode == "shadow":
        return 0
    if summary["warning"]:
        return 2
    return 1 if summary["advisory"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
