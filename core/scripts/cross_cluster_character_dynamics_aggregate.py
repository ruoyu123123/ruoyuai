"""汇总故事块级压力与角色行动分布。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _cluster_id(record: dict) -> str:
    return str(record["cluster_id"])


def _finding(severity: str, code: str, **fields: object) -> dict:
    return {"severity": severity, "gate_level": "advisory", "code": code, **fields}


def scan_stress_trend(project_root: Path, clusters: list[dict]) -> list[dict]:
    """检查摘要中的压力序列；序列单位固定为 cluster。"""
    config = _read_json(project_root / "_数据库" / "主角压力档.json", {}) or {}
    threshold = config.get("stress_threshold_break", 10)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
        threshold = 10
    points: list[tuple[str, float, dict]] = []
    for record in clusters:
        stress = record.get("stress")
        if not isinstance(stress, dict):
            continue
        value = stress.get("new_total")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            points.append((_cluster_id(record), float(value), stress))
    if len(points) < 2:
        return []

    findings: list[dict] = []
    rising = 0
    for index in range(1, len(points)):
        if points[index][1] > points[index - 1][1]:
            rising += 1
            if rising >= 3:
                window = points[index - 3:index + 1]
                findings.append(_finding(
                    "advisory",
                    "STRESS_RUNAWAY",
                    consecutive_clusters=[item[0] for item in window],
                    stress_trail=[item[1] for item in window],
                ))
                rising = 0
        else:
            rising = 0

    high = [item for item in points if item[1] >= threshold * 0.75]
    breaks = [item for item in points if item[2].get("mental_break_card")]
    if len(high) >= 5 and not breaks:
        findings.append(_finding(
            "warning",
            "STRESS_PERMA_HIGH_NO_BREAK",
            high_stress_clusters=[item[0] for item in high],
        ))

    coping_defined = bool(
        (config.get("coping_mechanisms") or {}).get("high_stress_behaviors")
    )
    coping_observed = [
        item for item in high
        if "coping_hit" in item[2]
    ]
    if coping_defined and len(high) >= 3 and coping_observed and not any(
        bool(item[2].get("coping_hit")) for item in coping_observed
    ):
        findings.append(_finding(
            "advisory",
            "COPING_NEVER_TRIGGERED",
            high_stress_clusters_unaddressed=[item[0] for item in high],
        ))
    return findings


def scan_moves_usage(project_root: Path, clusters: list[dict]) -> list[dict]:
    """按 cluster 统计 moves 使用，不读取正文或物理章节。"""
    data = _read_json(project_root / "_数据库" / "角色行动表.json", {}) or {}
    characters = data.get("characters")
    if not isinstance(characters, dict) or not characters:
        return []

    per_cluster_moves = {
        _cluster_id(record): record.get("moves_used") or []
        for record in clusters
    }
    per_cluster_mentions = {
        _cluster_id(record): record.get("char_mention_counts") or {}
        for record in clusters
    }
    findings: list[dict] = []
    for character, character_data in characters.items():
        if not isinstance(character_data, dict):
            continue
        moves = character_data.get("moves") or []
        if not isinstance(moves, list) or not moves:
            continue
        totals: Counter[str] = Counter()
        by_cluster: dict[str, Counter[str]] = defaultdict(Counter)
        for cluster_id, usages in per_cluster_moves.items():
            if not isinstance(usages, list):
                continue
            for usage in usages:
                if not isinstance(usage, dict) or usage.get("character") != character:
                    continue
                move_id = usage.get("move_id")
                instances = usage.get("instances", 1)
                if not isinstance(move_id, str) or not isinstance(instances, int) or isinstance(instances, bool):
                    continue
                totals[move_id] += instances
                by_cluster[cluster_id][move_id] += instances

        for move in moves:
            if not isinstance(move, dict):
                continue
            move_id = move.get("move_id")
            limit = move.get("frequency_per_cluster", 99)
            if not isinstance(move_id, str) or not isinstance(limit, int) or isinstance(limit, bool):
                continue
            for cluster_id, counts in by_cluster.items():
                instances = counts.get(move_id, 0)
                if instances > limit:
                    findings.append(_finding(
                        "advisory",
                        "MOVE_OVERUSE",
                        character=character,
                        move_id=move_id,
                        cluster_id=cluster_id,
                        instances=instances,
                        limit=limit,
                    ))

        appeared = [
            cluster_id for cluster_id, mentions in per_cluster_mentions.items()
            if isinstance(mentions, dict) and isinstance(mentions.get(character), (int, float))
            and mentions.get(character, 0) > 0
        ]
        zero_moves = [
            move.get("move_id") for move in moves
            if isinstance(move, dict) and isinstance(move.get("move_id"), str)
            and totals[move["move_id"]] == 0
        ]
        if len(appeared) >= 3 and not totals:
            findings.append(_finding(
                "warning",
                "CHARACTER_VOICELESS",
                character=character,
                appearance_clusters=appeared,
                moves_count=len(moves),
            ))
        elif len(appeared) >= 5 and len(zero_moves) >= max(3, len(moves) // 2):
            findings.append(_finding(
                "advisory",
                "MOVES_UNDERUSED",
                character=character,
                never_used_moves=zero_moves[:5],
                appearance_cluster_count=len(appeared),
            ))
    return findings


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    clusters = csr.get_clusters(project_root, last_n=last_n)
    if not clusters:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    findings: list[dict] = []
    findings.extend(scan_stress_trend(project_root, clusters))
    findings.extend(scan_moves_usage(project_root, clusters))
    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "character_dynamics",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": [_cluster_id(record) for record in clusters],
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
        out_path = out_dir / f"character_dynamics_{stamp}.json"
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = report["summary"]
        print(
            f"[character_dynamics] {summary['warning']} warning / "
            f"{summary['advisory']} advisory"
        )
        print(f"报告: {out_path}")
        return 2 if summary["warning"] else 1 if summary["advisory"] else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
