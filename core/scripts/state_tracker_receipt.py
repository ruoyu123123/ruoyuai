#!/usr/bin/env python3
"""为 novel-state-tracker 的 state delta 写入内容绑定回执。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup
import cluster_state_delta
import plan_tracker
from atomic_json import atomic_write_json


def _validate_plan_binding(project: Path, cluster_id: str, plan_id: str,
                           step: int, delta_path: Path) -> str:
    """校验 plan 绑定并拒绝早于本 plan 的 state delta。"""
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
    declared = next(
        (row for row in plan.get("steps", []) if str(row.get("n")) == str(step)),
        None,
    )
    if not isinstance(declared, dict) or not declared.get("required"):
        raise ValueError("PLAN_ID 未声明当前 required step")
    raw_created = str(plan.get("created_at") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", raw_created):
        raise ValueError("PLAN_ID 缺少带微秒的 created_at")
    created_at = datetime.fromisoformat(raw_created)
    if delta_path.stat().st_mtime < created_at.timestamp():
        raise ValueError("state delta 早于当前 plan，禁止复用遗留产物")
    return raw_created


def build_receipt(project: Path, cluster: str, plan_id: str, step: str) -> dict:
    """校验 canonical delta，并返回绑定其字节摘要的 Agent 回执。"""
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise ValueError(f"非法 cluster: {cluster}")
    if not plan_id.strip():
        raise ValueError("plan_id 不能为空")
    if not re.fullmatch(r"[1-9]\d*", str(step).strip()):
        raise ValueError(f"step 必须是正整数: {step}")
    step_number = int(step)
    relative = Path("_数据库") / ".wal" / f"{cluster_id}_state_delta.json"
    delta_path, delta = cluster_state_delta.load_delta(project, cluster_id)
    plan_created_at = _validate_plan_binding(
        project, cluster_id, plan_id, step_number, delta_path,
    )
    raw = delta_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("state delta 必须是 UTF-8 无 BOM")
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"state delta 不是有效 UTF-8 JSON: {exc}") from exc
    return {
        "schema_version": "novel-state-tracker.receipt.v1",
        "agent": "novel-state-tracker",
        "plan_id": plan_id,
        "step": step_number,
        "cluster_id": cluster_id,
        "plan_created_at": plan_created_at,
        "completed": True,
        "delta": {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }


def write_receipt(project: Path, receipt: dict) -> Path:
    """把回执写到当前 cluster 的 canonical WAL 路径。"""
    output = (
        project / "_数据库" / ".wal"
        / f"{receipt['cluster_id']}_state_tracker_receipt.json"
    )
    atomic_write_json(output, receipt)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写 state-tracker Agent 完成回执")
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
