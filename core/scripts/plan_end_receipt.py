#!/usr/bin/env python3
"""收尾 step 的 plan 最终校验 + 回执落盘（cluster-write / cluster-save-state）。

收尾 step（cluster-write step 7 / cluster-save-state step 14）的职责是「plan 最终校验」。
本脚本把这份校验变成可验证产物：逐条核对本 plan 前置 required step **真完成且有
verified_outputs 实体文件**，全绿才写回执；任何一步假完成（completed 但 verified_outputs
为空 = 未跑输出校验）或产物丢失 → [FATAL] exit 2，收尾 step 不得标完成。

用法：
    python core/scripts/plan_end_receipt.py <project_root> \
        --plan-id <PLAN_ID> --step <N> --command <cluster-write|cluster-save-state>

回执路径（cluster 绑定 · canonical）：
    _数据库/.wal/<cluster_id>_write_end.json          （cluster-write）
    _数据库/.wal/<cluster_id>_save_state_end.json     （cluster-save-state）
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_tracker
from atomic_json import atomic_write_json

# command → 回执文件名后缀（单一真理源：plan 模板 expected_outputs 必须与此对齐）
RECEIPT_SUFFIX = {
    "cluster-write": "write_end",
    "cluster-save-state": "save_state_end",
}
SCHEMA_VERSION = "plan-end.receipt.v1"


def _step_number(value) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"非法 step 号: {value!r}")


def _load_plan(project: Path, plan_id: str, command: str) -> dict:
    if plan_tracker.verify_plan(plan_id) != "ok":
        raise ValueError(f"PLAN_ID attestation 无效: {plan_id}")
    plan = plan_tracker.get_plan(plan_id)
    if not plan:
        raise ValueError(f"plan 不存在: {plan_id}")
    plan_project = plan_tracker.resolve_project_root(str(plan.get("project") or ""))
    if plan_project is None or plan_project.resolve() != project.resolve():
        raise ValueError("PLAN_ID 未绑定当前项目")
    if plan.get("command") != command:
        raise ValueError(f"PLAN_ID 不是 {command} plan（实际 {plan.get('command')!r}）")
    return plan


def _verified_paths(project: Path, step: dict) -> list[str]:
    """step.verified_outputs 的实体文件必须仍在磁盘上。"""
    verified = step.get("verified_outputs") or []
    if not verified:
        raise ValueError(
            f"step {step.get('n')} ({step.get('name')}) 假完成："
            f"verified_outputs 为空（该 step 从未跑过 expected_outputs 校验）")
    paths = []
    for raw in verified:
        p = Path(str(raw))
        if not p.is_absolute():
            p = project / str(raw)
        if not p.exists():
            raise ValueError(
                f"step {step.get('n')} 的 verified_output 已丢失: {raw}")
        paths.append(str(p).replace("\\", "/"))
    return paths


def build_receipt(project: Path, plan_id: str, step: str, command: str) -> dict:
    """核验前置 required step 全部真完成，构造收尾回执。"""
    if command not in RECEIPT_SUFFIX:
        raise ValueError(f"不支持的 command: {command}（仅 {sorted(RECEIPT_SUFFIX)}）")
    if not str(plan_id).strip():
        raise ValueError("plan_id 不能为空")
    current_n = _step_number(step)
    plan = _load_plan(project, plan_id, command)

    cluster_id = plan.get("cluster_id") or plan_tracker.cluster_id_from_key(plan.get("key"))
    if not cluster_id or not re.fullmatch(r"cluster_[0-9A-Za-z_]+", str(cluster_id)):
        raise ValueError(f"plan 未绑定 canonical cluster_id: {cluster_id!r}")

    rows = {_step_number(s.get("n")): s for s in plan.get("steps", [])
            if s.get("n") is not None}
    current = rows.get(current_n)
    if not isinstance(current, dict) or not current.get("required"):
        raise ValueError(f"plan 未声明 required step {step}")

    steps_report = []
    for n in sorted(rows):
        if n >= current_n:
            continue
        row = rows[n]
        if not row.get("required"):
            continue
        if row.get("status") != plan_tracker.STATUS_COMPLETED:
            raise ValueError(
                f"required step {row.get('n')} ({row.get('name')}) 未完成："
                f"status={row.get('status')!r} — 收尾 step 不得跳过前置")
        steps_report.append({
            "n": row.get("n"),
            "name": row.get("name"),
            "completed_at": row.get("completed_at"),
            "verified_outputs": _verified_paths(project, row),
        })
    if not steps_report:
        raise ValueError("plan 无前置 required step，收尾校验空转")

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "command": command,
        "step": current.get("n"),
        "cluster_id": str(cluster_id),
        "completed": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "verified_required_steps": len(steps_report),
        "steps": steps_report,
    }


def receipt_path(project: Path, cluster_id: str, command: str) -> Path:
    return (project / "_数据库" / ".wal"
            / f"{cluster_id}_{RECEIPT_SUFFIX[command]}.json")


def write_receipt(project: Path, receipt: dict) -> Path:
    output = receipt_path(project, receipt["cluster_id"], receipt["command"])
    atomic_write_json(output, receipt)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="plan 收尾 step 最终校验 + 回执")
    parser.add_argument("project")
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--command", required=True, choices=sorted(RECEIPT_SUFFIX))
    args = parser.parse_args(argv)
    try:
        project = Path(args.project).resolve()
        receipt = build_receipt(project, args.plan_id, args.step, args.command)
        output = write_receipt(project, receipt)
    except (OSError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
