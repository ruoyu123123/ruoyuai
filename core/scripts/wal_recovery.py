"""wal_recovery.py — save-state 崩溃恢复检测（v19.2 升级版）

v19.2 设计修正：
- 原 WAL JSON 文件被 save-state step 1 创建后再没人更新（断层）
- 实际上 plan_tracker.py 已经追踪每步状态（plans/*.json with completed_steps）
- 所以 wal_recovery 直接读 plan_tracker 状态作为"WAL 真实视图"
- WAL JSON 文件保留为"传统兼容字段"但不再作为权威源

职责：
- 扫所有 plan_tracker 中 command=save-state/write-chapter 且 status != DONE/ABORT 的 plan
- 报告中断点（已完成 step 数 / 总 step 数）
- 给主代理"从 step N+1 续跑"的明确指令

用法：
    python wal_recovery.py <项目名>            # 扫该项目所有未完成 plan
    python wal_recovery.py <项目名> --ch <N>   # 查特定章

退出码: 0 健康 / 1 有未完成 plan / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


PLAN_DIR = Path(__file__).parent.parent / "claude-home" / "plans" / "runtime"


def list_plans_for_project(project_name: str) -> list[dict]:
    """直接读 plan_tracker runtime 目录，比 subprocess 调用更稳。"""
    if not PLAN_DIR.is_dir():
        # 兜底：尝试 plan_tracker list
        try:
            result = subprocess.run(
                ["python", str(Path(__file__).parent / "plan_tracker.py"), "list"],
                capture_output=True, text=True, encoding="utf-8", timeout=10
            )
            return _parse_plan_list_output(result.stdout, project_name)
        except Exception:
            return []
    out = []
    for f in PLAN_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("project") != project_name:
            continue
        out.append(data)
    return out


def _parse_plan_list_output(text: str, project: str) -> list[dict]:
    """解析 plan_tracker list 文本输出（兜底）。"""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*\[(\w+)\s*\]\s+(\S+)\s+cmd=(\S+)\s+project=(\S+)\s+chapter=(\S+)", line)
        if m:
            status, pid, cmd, proj, ch = m.groups()
            if proj != project:
                continue
            out.append({
                "id": pid,
                "command": cmd,
                "project": proj,
                "chapter": None if ch == "None" else int(ch),
                "status": status,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    args = ap.parse_args()

    # 接受路径或项目名
    project_path = Path(args.project)
    if project_path.is_dir():
        project_name = project_path.name
    else:
        project_name = args.project

    plans = list_plans_for_project(project_name)
    if args.ch is not None:
        plans = [p for p in plans if p.get("chapter") == args.ch]

    if not plans:
        print(f"[OK] 无 plan 数据（项目 {project_name}{'，ch'+str(args.ch) if args.ch else ''}）")
        sys.exit(0)

    incomplete = [p for p in plans if p.get("status") not in ("DONE", "done", "ABORT", "abort", "completed")]

    print(f"[wal_recovery] 项目 {project_name}: 总 plan {len(plans)} / 未完成 {len(incomplete)}")
    for p in plans:
        ch = p.get("chapter")
        cmd = p.get("command")
        status = p.get("status")
        steps_meta = p.get("steps", [])
        # 计算 verified/done 步数
        done_count = sum(1 for s in steps_meta if (s.get("status") in ("completed", "done") or s.get("verified")))
        total = len(steps_meta) if steps_meta else (p.get("total_steps") or 0)
        flag = "✅" if status in ("DONE", "done", "completed") else ("⏸" if status == "ABORT" else "🔴 中断")
        ch_str = f"ch{ch}" if ch else "全书"
        print(f"  {flag} [{status}] {cmd}/{ch_str}: {done_count}/{total} 步 ({p['id']})")

    if incomplete:
        print("\n=== 未完成 plan 列表 ===")
        for p in incomplete:
            steps_meta = p.get("steps", [])
            done_count = sum(1 for s in steps_meta if (s.get("status") in ("completed", "done") or s.get("verified")))
            next_step = done_count + 1
            print(f"  {p['id']}:")
            print(f"    续跑: plan_tracker step {p['id']} --n {next_step} （或主代理重新进入 {p['command']} 流水线从 step {next_step} 续跑）")
            print(f"    放弃: plan_tracker abort {p['id']}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
