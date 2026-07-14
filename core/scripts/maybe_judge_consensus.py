"""按故事块条件合并多个 Judge 报告，并始终写执行决策回执。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_json import atomic_write_json  # noqa: E402
import cluster_lookup  # noqa: E402
from frozen_util import child_python, scripts_dir  # noqa: E402
from proc_utils import run_utf8  # noqa: E402


def _cluster_id(value: str) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise ValueError(f"非法 cluster_id: {value}")
    return cluster_id


def _judge_reports(project_root: Path, cluster_id: str) -> list[Path]:
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.is_dir():
        return []
    excluded = {
        f"{cluster_id}_consensus.json",
        f"{cluster_id}_consensus_decision.json",
    }
    return sorted(
        path for path in judge_dir.glob(f"{cluster_id}_*.json")
        if path.name not in excluded
    )


def run_cluster(project_root: Path, cluster_key: str, *, timeout: int = 30) -> int:
    """合并当前故事块的 Judge 报告；少于两份时记录 not_required。"""
    cluster_id = _cluster_id(cluster_key)
    judge_dir = project_root / "_数据库" / ".judge_reports"
    judge_dir.mkdir(parents=True, exist_ok=True)
    decision_path = judge_dir / f"{cluster_id}_consensus_decision.json"
    reports = _judge_reports(project_root, cluster_id)
    base_decision = {
        "schema_version": "judge_consensus_decision.v1",
        "cluster_id": cluster_id,
        "required_substep_executed": True,
        "input_reports": [str(path.resolve()) for path in reports],
        "input_count": len(reports),
    }

    if len(reports) < 2:
        decision = {
            **base_decision,
            "status": "not_required",
            "reason": "至少需要两份独立 Judge 报告",
        }
        atomic_write_json(decision_path, decision)
        print(f"[OK] {cluster_id} consensus not_required: reports={len(reports)}")
        return 0

    consensus_script = scripts_dir() / "judge_consensus.py"
    if not consensus_script.is_file():
        print(f"[FATAL] judge_consensus.py 不存在: {consensus_script}", file=sys.stderr)
        return 2
    command = [child_python(), str(consensus_script), "merge", *map(str, reports)]
    try:
        result = run_utf8(command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[FATAL] consensus 调度失败: {exc}", file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(
            f"[FATAL] consensus exit={result.returncode}: {(result.stderr or '')[-400:]}",
            file=sys.stderr,
        )
        return 2
    try:
        consensus = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"[FATAL] consensus 输出不是 JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(consensus, dict):
        print("[FATAL] consensus 输出顶层必须是 object", file=sys.stderr)
        return 2

    consensus_path = judge_dir / f"{cluster_id}_consensus.json"
    atomic_write_json(consensus_path, consensus)
    atomic_write_json(decision_path, {
        **base_decision,
        "status": "merged",
        "consensus_report": str(consensus_path.resolve()),
    })
    print(f"[OK] {cluster_id} consensus merged: reports={len(reports)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    if args.timeout < 1:
        print("[FATAL] --timeout 必须是正整数", file=sys.stderr)
        return 2
    try:
        return run_cluster(Path(args.project), args.cluster, timeout=args.timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
