"""运行全部跨 cluster 顾问并持久化 required 执行回执。

顾问发现只影响报告，不改变 hard gate。调度故障、输入错误、超时和缺失脚本
会使本命令失败，避免 required 子步骤在未完整执行时被标记为完成。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from atomic_json import atomic_write_json
from frozen_util import child_python, scripts_dir
from proc_utils import run_utf8


SCANNERS = [
    "cross_cluster_continuity_aggregate",
    "cross_cluster_pattern_aggregate",
    "cross_cluster_fate_drift_aggregate",
    "cross_cluster_persona_drift_aggregate",
    "cross_cluster_data_consumption_aggregate",
    "cross_cluster_offscreen_aggregate",
    "cross_cluster_declarative_data_aggregate",
    "cross_cluster_arc_progression_aggregate",
    "cross_cluster_throughline_balance_aggregate",
    "cross_cluster_foreshadow_rhythm_aggregate",
    "cross_cluster_emotion_pattern_aggregate",
    "cross_cluster_character_dynamics_aggregate",
    "cross_cluster_world_dynamics_aggregate",
    "cross_cluster_judge_quality_aggregate",
    "cross_cluster_timeline_item_location_aggregate",
    "cross_cluster_meta_quality_aggregate",
    "cross_cluster_structure_compliance_aggregate",
    "cross_cluster_engagement_metrics_aggregate",
    "cross_cluster_ending_diversity_aggregate",
    "cross_cluster_scene_pov_diversity_aggregate",
    "cross_cluster_relationship_trend_aggregate",
    "cross_cluster_will_learn_aggregate",
    "volume_arc_drift_scanner",
    "cross_cluster_style_drift_scanner",
    "volume_transition_scanner",
    "cross_cluster_narrative_debt_ledger_aggregate",
    "cross_cluster_sagging_middle_aggregate",
    "cross_cluster_character_presence_balance_aggregate",
    "motif_recurrence_ledger",
    "cross_cluster_hierarchical_position_surprisal_aggregate",
    "cross_cluster_reader_retention_proxy_aggregate",
    "summary_cluster_alignment_distribution_scanner",
    "cross_cluster_entity_state_graph_aggregate",
    "cross_cluster_ousiometric_emd_scanner",
    "location_signature_consistency",
    "macguffin_entanglement_scanner",
]

SCANNERS_WITHOUT_WINDOW = {
    "cross_cluster_arc_progression_aggregate",
    "cross_cluster_world_dynamics_aggregate",
    "cross_cluster_foreshadow_rhythm_aggregate",
    "cross_cluster_will_learn_aggregate",
    "cross_cluster_structure_compliance_aggregate",
    "motif_recurrence_ledger",
    "cross_cluster_entity_state_graph_aggregate",
}

_CLUSTER_RE = re.compile(r"(?:cluster_)?(0*[1-9]\d*)\Z")
_FATAL_MARKERS = ("traceback", "[fatal]", "usage:", "unrecognized arguments")


def normalize_cluster_id(value: str) -> str:
    """校验并归一化显式 cluster 标识。"""
    match = _CLUSTER_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"无效 cluster 标识: {value!r}")
    return f"cluster_{int(match.group(1)):03d}"


def cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    """返回当前 cluster 的完整终稿路径。"""
    key = cluster_id.removeprefix("cluster_")
    return (
        project_root
        / "章节"
        / f"cluster_{key}_draft"
        / f"cluster_{key}_draft.txt"
    )


def build_scanner_command(
    scanner: str,
    script_path: Path,
    project_root: Path,
    cluster_id: str,
    last_n: int,
) -> list[str]:
    """按各顾问的 cluster CLI 构造命令。"""
    base = [child_python(), str(script_path)]
    if scanner == "cross_cluster_fate_drift_aggregate":
        return base + [str(project_root), "--cluster", cluster_id]
    if scanner == "cross_cluster_ousiometric_emd_scanner":
        return base + ["--project", str(project_root)]
    if scanner == "location_signature_consistency":
        return base + [
            "--project",
            str(project_root),
            "--scan-cluster",
            cluster_id,
            "--draft",
            str(cluster_draft_path(project_root, cluster_id)),
        ]
    if scanner in SCANNERS_WITHOUT_WINDOW:
        return base + [str(project_root)]
    return base + [str(project_root), "--last-n", str(last_n)]


def _is_execution_failure(returncode: int, stdout: str, stderr: str) -> bool:
    """区分顾问发现与调度/输入故障。"""
    combined = f"{stdout}\n{stderr}".lower()
    return (
        returncode < 0
        or returncode >= 3
        or any(marker in combined for marker in _FATAL_MARKERS)
    )


def _run_scanner(
    scanner: str,
    command: list[str],
    env: dict[str, str],
    timeout_seconds: int,
) -> dict:
    """执行单个顾问并返回结构化结果。"""
    result = run_utf8(
        command,
        timeout=timeout_seconds,
        env=env,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    failure = _is_execution_failure(result.returncode, stdout, stderr)
    if failure:
        status = "failed"
    elif result.returncode == 0:
        status = "ok"
    else:
        status = "advisory"
    return {
        "scanner": scanner,
        "status": status,
        "exit_code": result.returncode,
        "stdout_tail": stdout.strip()[-800:],
        "stderr_tail": stderr.strip()[-800:],
    }


def write_run_report(project_root: Path, report: dict) -> Path:
    """原子写入本次 required 执行回执。"""
    output_dir = project_root / "_数据库" / ".cross_cluster_scan"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "cross_cluster_wrapper_latest.json"
    atomic_write_json(output_path, report)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument(
        "--last-n",
        type=int,
        default=10,
        help="需要最近窗口的顾问读取的 cluster 数",
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    try:
        cluster_id = normalize_cluster_id(args.cluster)
    except ValueError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    if args.last_n < 1 or args.timeout < 1:
        print("[FATAL] --last-n 和 --timeout 必须为正整数", file=sys.stderr)
        return 2

    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        return 2

    script_root = scripts_dir()
    env = {**os.environ, "CLUSTER_MODE": "1", "CLUSTER_ID": cluster_id}
    tasks: list[dict] = []

    for scanner in SCANNERS:
        script_path = script_root / f"{scanner}.py"
        if not script_path.is_file():
            tasks.append(
                {
                    "scanner": scanner,
                    "status": "failed",
                    "error": f"脚本不存在: {script_path}",
                }
            )
            continue
        command = build_scanner_command(
            scanner,
            script_path,
            project_root,
            cluster_id,
            args.last_n,
        )
        try:
            tasks.append(
                _run_scanner(scanner, command, env, args.timeout)
            )
        except subprocess.TimeoutExpired:
            tasks.append(
                {
                    "scanner": scanner,
                    "status": "failed",
                    "error": f"超过 {args.timeout} 秒",
                }
            )
        except OSError as exc:
            tasks.append(
                {
                    "scanner": scanner,
                    "status": "failed",
                    "error": f"启动失败: {exc}",
                }
            )

    failed = [task for task in tasks if task["status"] == "failed"]
    advisory = [task for task in tasks if task["status"] == "advisory"]
    report = {
        "schema_version": "cross_cluster_run.v1",
        "cluster_id": cluster_id,
        "required": True,
        "expected_count": len(SCANNERS),
        "executed_count": len(tasks),
        "failed_count": len(failed),
        "advisory_count": len(advisory),
        "tasks": tasks,
    }

    try:
        report_path = write_run_report(project_root, report)
    except OSError as exc:
        print(f"[FATAL] 执行回执写入失败: {exc}", file=sys.stderr)
        return 2

    if failed:
        names = ", ".join(task["scanner"] for task in failed)
        print(f"[FATAL] {len(failed)} 个顾问未完成: {names}", file=sys.stderr)
        print(f"[REPORT] {report_path}")
        return 2

    print(
        f"[OK] {len(tasks)}/{len(SCANNERS)} 个跨 cluster 顾问已执行；"
        f"advisory={len(advisory)}"
    )
    print(f"[REPORT] {report_path}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
