#!/usr/bin/env python3
"""校验 cluster-save-state 后置子步骤并写 plan 绑定回执。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup
import plan_tracker
from atomic_json import atomic_write_json
from run_cross_cluster_aggregates import SCANNERS


def _read_object(path: Path) -> tuple[bytes, dict]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"必需产物不存在或不可读: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"必需产物必须是 UTF-8 无 BOM: {path}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"必需产物不是有效 UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"必需产物顶层必须是 object: {path}")
    return raw, value


def _artifact(project: Path, path: Path, role: str) -> dict:
    raw = path.read_bytes()
    try:
        relative = path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"产物不在项目目录内: {path}") from exc
    return {
        "role": role,
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _plan_created_at(project: Path, cluster_id: str, plan_id: str,
                     step: int) -> datetime:
    """校验当前 plan 绑定并返回带微秒的稳定创建时间。"""
    if plan_tracker.verify_plan(plan_id) != "ok":
        raise ValueError("PLAN_ID attestation 无效")
    plan = plan_tracker.get_plan(plan_id)
    plan_project = plan_tracker.resolve_project_root(str(plan.get("project") or ""))
    if plan_project is None or plan_project.resolve() != project.resolve():
        raise ValueError("PLAN_ID 未绑定当前项目")
    if plan.get("command") != "cluster-save-state":
        raise ValueError("PLAN_ID 不是 cluster-save-state plan")
    if plan.get("cluster_id") != cluster_id:
        raise ValueError("PLAN_ID 未绑定当前 cluster")
    current_step = next(
        (row for row in plan.get("steps", []) if str(row.get("n")) == str(step)),
        None,
    )
    if not isinstance(current_step, dict) or not current_step.get("required"):
        raise ValueError("PLAN_ID 未声明当前 required step")
    raw_created = str(plan.get("created_at") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", raw_created):
        raise ValueError("PLAN_ID 缺少带微秒的 created_at")
    return datetime.fromisoformat(raw_created)


def _require_fresh(paths: list[Path], created_at: datetime) -> None:
    """拒绝早于当前 plan 的同 cluster 遗留产物。"""
    threshold = created_at.timestamp()
    stale = [str(path) for path in paths if path.stat().st_mtime < threshold]
    if stale:
        raise ValueError(f"发现早于当前 plan 的遗留产物: {stale}")


def _validate_state_updates(path: Path, cluster_id: str) -> None:
    _raw, value = _read_object(path)
    if value.get("schema_version") != "cluster-state-updates.receipt.v1":
        raise ValueError("state updates receipt schema_version 不匹配")
    if value.get("cluster_id") != cluster_id or value.get("completed") is not True:
        raise ValueError("state updates receipt 未绑定当前 cluster 或未完成")
    modules = value.get("modules")
    if not isinstance(modules, list):
        raise ValueError("state updates receipt.modules 必须是 array")
    names = [row.get("name") for row in modules if isinstance(row, dict)]
    if names != ["offscreen", "character_arc"]:
        raise ValueError("state updates receipt 模块集合不完整")
    if not all(row.get("ok") is True and row.get("exit_code") == 0 for row in modules):
        raise ValueError("state updates receipt 含失败模块")


def _validate_state_evaluators(path: Path, cluster_id: str) -> None:
    _raw, value = _read_object(path)
    if value.get("schema_version") != "cluster-state-evaluators.receipt.v1":
        raise ValueError("state evaluators receipt schema_version 不匹配")
    if value.get("cluster_id") != cluster_id or value.get("completed") is not True:
        raise ValueError("state evaluators receipt 未绑定当前 cluster 或未完成")
    evaluators = value.get("evaluators")
    if not isinstance(evaluators, list):
        raise ValueError("state evaluators receipt.evaluators 必须是 array")
    names = [row.get("name") for row in evaluators if isinstance(row, dict)]
    if names != ["clock", "narrator", "stress", "relationship"]:
        raise ValueError("state evaluators receipt 评估器集合不完整")
    if not all(
        row.get("ok") is True and row.get("exit_code") in (0, 1)
        for row in evaluators
    ):
        raise ValueError("state evaluators receipt 含失败评估器")


def _validate_cross_cluster(path: Path, cluster_id: str) -> None:
    _raw, value = _read_object(path)
    tasks = value.get("tasks")
    if value.get("schema_version") != "cross_cluster_run.v1":
        raise ValueError("cross-cluster wrapper schema_version 不匹配")
    if value.get("cluster_id") != cluster_id or value.get("required") is not True:
        raise ValueError("cross-cluster wrapper 未绑定当前 cluster")
    if not isinstance(tasks, list):
        raise ValueError("cross-cluster wrapper.tasks 必须是 array")
    if value.get("expected_count") != len(SCANNERS):
        raise ValueError("cross-cluster wrapper.expected_count 不完整")
    if value.get("executed_count") != len(tasks) or len(tasks) != len(SCANNERS):
        raise ValueError("cross-cluster wrapper 未执行全部顾问")
    if value.get("failed_count") != 0:
        raise ValueError("cross-cluster wrapper 含执行失败")
    names = [row.get("scanner") for row in tasks if isinstance(row, dict)]
    if names != list(SCANNERS):
        raise ValueError("cross-cluster wrapper 顾问集合或顺序不匹配")
    if not all(row.get("status") in {"ok", "advisory"} for row in tasks):
        raise ValueError("cross-cluster wrapper 含非完成状态")


def _validate_world_apply(path: Path, cluster_id: str) -> None:
    _raw, value = _read_object(path)
    if value.get("cluster_id") != cluster_id:
        raise ValueError("world evolution apply 未绑定当前 cluster")
    required = {"world_state_consumption", "fate_events", "tick", "applied_at"}
    if not required <= set(value):
        raise ValueError("world evolution apply 缺少 required 字段")


def _validate_consensus(path: Path, project: Path, cluster_id: str) -> Path | None:
    _raw, value = _read_object(path)
    if value.get("schema_version") != "judge_consensus_decision.v1":
        raise ValueError("consensus decision schema_version 不匹配")
    if value.get("cluster_id") != cluster_id:
        raise ValueError("consensus decision 未绑定当前 cluster")
    if value.get("required_substep_executed") is not True:
        raise ValueError("consensus required 子步骤未执行")
    reports = value.get("input_reports")
    if not isinstance(reports, list) or value.get("input_count") != len(reports):
        raise ValueError("consensus decision 输入计数不一致")
    status = value.get("status")
    if status == "not_required":
        if len(reports) >= 2 or not value.get("reason"):
            raise ValueError("consensus not_required 决策与输入不一致")
        return None
    if status != "merged" or len(reports) < 2:
        raise ValueError("consensus decision 状态无效")
    expected = project / "_数据库" / ".judge_reports" / f"{cluster_id}_consensus.json"
    declared = Path(str(value.get("consensus_report") or ""))
    if not declared.is_absolute():
        declared = project / declared
    if declared.resolve() != expected.resolve():
        raise ValueError("consensus report 路径不是当前 cluster canonical 路径")
    _read_object(expected)
    return expected


def build_receipt(project: Path, cluster: str, plan_id: str, step: str) -> dict:
    """严格验证当前 cluster 的动态证明文件并构造聚合回执。"""
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise ValueError(f"非法 cluster: {cluster}")
    if not plan_id.strip():
        raise ValueError("plan_id 不能为空")
    if not re.fullmatch(r"[1-9]\d*", str(step).strip()):
        raise ValueError(f"step 必须是正整数: {step}")
    step_number = int(step)
    created_at = _plan_created_at(project, cluster_id, plan_id, step_number)
    db = project / "_数据库"
    paths = {
        "state_updates": db / ".wal" / f"{cluster_id}_state_updates_receipt.json",
        "state_evaluators": db / ".wal" / f"{cluster_id}_state_evaluators_receipt.json",
        "cross_cluster": db / ".cross_cluster_scan" / "cross_cluster_wrapper_latest.json",
        "world_evolution": db / ".world_evolution" / f"{cluster_id}_apply.json",
        "consensus_decision": db / ".judge_reports" / f"{cluster_id}_consensus_decision.json",
        "knowledge_graph": db / "knowledge_graph.json",
        "subplot_threads": db / "subplot_threads.json",
    }
    _validate_state_updates(paths["state_updates"], cluster_id)
    _validate_state_evaluators(paths["state_evaluators"], cluster_id)
    _validate_cross_cluster(paths["cross_cluster"], cluster_id)
    _validate_world_apply(paths["world_evolution"], cluster_id)
    consensus_report = _validate_consensus(
        paths["consensus_decision"], project, cluster_id,
    )
    _read_object(paths["knowledge_graph"])
    _read_object(paths["subplot_threads"])
    fresh_paths = list(paths.values())
    if consensus_report is not None:
        fresh_paths.append(consensus_report)
    _require_fresh(fresh_paths, created_at)
    artifacts = [
        _artifact(project, paths[role], role)
        for role in (
            "state_updates", "state_evaluators", "cross_cluster",
            "world_evolution", "consensus_decision", "knowledge_graph",
            "subplot_threads",
        )
    ]
    if consensus_report is not None:
        artifacts.append(_artifact(project, consensus_report, "consensus_report"))
    return {
        "schema_version": "cluster-post-state.receipt.v1",
        "plan_id": plan_id,
        "step": step_number,
        "cluster_id": cluster_id,
        "plan_created_at": created_at.isoformat(timespec="microseconds"),
        "completed": True,
        "artifacts": artifacts,
    }


def write_receipt(project: Path, receipt: dict) -> Path:
    """写入当前 cluster 的 canonical 后置回执。"""
    output = (
        project / "_数据库" / ".wal"
        / f"{receipt['cluster_id']}_post_state_receipt.json"
    )
    atomic_write_json(output, receipt)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写 cluster-save-state 后置执行回执")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--step", required=True)
    args = parser.parse_args(argv)
    try:
        project = Path(args.project).resolve()
        receipt = build_receipt(project, args.cluster, args.plan_id, args.step)
        output = write_receipt(project, receipt)
    except (OSError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
