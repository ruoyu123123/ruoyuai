#!/usr/bin/env python3
"""运行 cluster-save-state 的确定性状态更新子模块。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import atomic_json
import cluster_lookup
import state_cli_guard
from frozen_util import child_python, scripts_dir


SCRIPT_DIR = scripts_dir()
SUB_MODULES = (
    ("offscreen", "offscreen_update.py"),
    ("character_arc", "character_arc_update.py"),
)


def run_one_module(name: str, script_name: str, project: Path,
                   cluster_id: str) -> dict:
    script = SCRIPT_DIR / script_name
    if not script.is_file():
        return {"name": name, "ok": False, "exit_code": None,
                "error": f"脚本不存在: {script}"}
    try:
        result = subprocess.run(
            [child_python(), str(script), str(project), "--cluster", cluster_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=state_cli_guard.internal_env(),
        )
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "exit_code": None, "error": "timeout"}
    except OSError as exc:
        return {"name": name, "ok": False, "exit_code": None, "error": str(exc)}
    return {
        "name": name,
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-500:],
        "stderr_tail": result.stderr[-500:],
    }


def run_updates(project: Path, cluster: str) -> dict:
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise ValueError(f"非法 cluster: {cluster}")
    if not (project / "_数据库").is_dir():
        raise ValueError(f"项目 _数据库 不存在: {project}")
    results = [run_one_module(name, script, project, cluster_id)
               for name, script in SUB_MODULES]
    failures = [item for item in results if not item["ok"]]
    if failures:
        details = "; ".join(
            f"{item['name']}: rc={item.get('exit_code')} "
            f"{item.get('error') or item.get('stderr_tail') or item.get('stdout_tail')}"
            for item in failures
        )
        raise RuntimeError(details)
    receipt = {
        "schema_version": "cluster-state-updates.receipt.v1",
        "cluster_id": cluster_id,
        "completed": True,
        "modules": results,
    }
    atomic_json.atomic_write_json(
        project / "_数据库" / ".wal" / f"{cluster_id}_state_updates_receipt.json",
        receipt,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="cluster 状态更新调度")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        receipt = run_updates(Path(args.project).resolve(), args.cluster)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

