"""wal_recovery.py — save-state 崩溃恢复检测（v19.2 升级版）

v19.2 设计修正：
- 原 WAL JSON 文件被 save-state step 1 创建后再没人更新（断层）
- 实际上 plan_tracker.py 已经追踪每步状态（plans/*.json with completed_steps）
- 所以 wal_recovery 直接读 plan_tracker 状态作为"WAL 真实视图"
- WAL JSON 文件保留为"传统兼容字段"但不再作为权威源

职责：
- 扫所有 plan_tracker 中 status != DONE/ABORT 的 plan（v26 后主力是 cluster-write /
  cluster-save-state；chapter mode 的 write-chapter/save-state 已废弃，仅历史 plan 仍兼容显示）
- 报告中断点（已完成 step 数 / 总 step 数）
- 给主代理"从 step N+1 续跑"的明确指令

用法：
    python wal_recovery.py <项目名|项目路径>              # 扫该项目所有未完成 plan
    python wal_recovery.py <项目名|项目路径> --ch <N>     # 查特定章（按 plan.chapter 过滤）
    python wal_recovery.py <项目名|项目路径> --cluster 001 # 查特定 cluster（v26 cluster mode）

退出码: 0 健康 / 1 有未完成 plan / 2 致命

2026-05-29 复审修复[M21]：补 --cluster <key> 入口（cluster-save-state.md 失败逃生舱
已引用但旧版未实现）。用 cluster_lookup 把 cluster_id 展开成章范围，再按
「plan.key 同 cluster_id」或「plan.chapter 落在该 cluster 章范围内」双判据过滤 plan。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 2026-05-29 复审修复[M21]：复用 cluster_lookup 做 cluster_id → 章范围展开
try:
    import cluster_lookup  # 同目录脚本
except Exception:  # pragma: no cover - 兜底（不阻塞章级路径）
    cluster_lookup = None

try:
    from frozen_util import child_python  # frozen-aware 子解释器（M4·dev=no-op）
except Exception:  # pragma: no cover
    def child_python():
        return sys.executable


# 传统兜底目录（历史遗留，绝大多数情况不存在 —— plan 实际落在
# <project>/_数据库/.plans/）。保留为最后兜底，不作权威源。
PLAN_DIR = Path(__file__).parent.parent / "claude-home" / "plans" / "runtime"


def _project_plan_dirs(project_arg: str) -> list[Path]:
    """定位 plan.json 真实存放目录。

    plan_tracker.runtime_plans_dir 把小说项目 plan 落在 <project>/_数据库/.plans/，
    风格库落在 <project>/.plans/，找不到项目则落 GLOBAL（core/claude-home/.plans/）。
    这里把所有可能目录都收集起来扫，确保 --cluster / 默认模式都能命中。
    """
    dirs: list[Path] = []
    p = Path(project_arg)
    if p.is_dir():
        for cand in (p / "_数据库" / ".plans", p / ".plans"):
            if cand.is_dir():
                dirs.append(cand)
    # GLOBAL 兜底（plan_tracker 找不到项目时落这）
    glob_dir = Path(__file__).parent.parent / "claude-home" / ".plans"
    if glob_dir.is_dir():
        dirs.append(glob_dir)
    # 传统 runtime 目录（历史兼容）
    if PLAN_DIR.is_dir():
        dirs.append(PLAN_DIR)
    return dirs


def list_plans_for_project(project_arg: str, project_name: str) -> list[dict]:
    """读 plan_tracker 真实 runtime 目录（<project>/_数据库/.plans/ 等）。

    project_arg：用户传入的项目名或路径（用于定位 .plans 目录）。
    project_name：归一化后的项目名（用于 plan.project 比对）。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for d in _project_plan_dirs(project_arg):
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            # GLOBAL/runtime 目录里混有别的项目 plan → 按 project 名过滤
            if data.get("project") not in (project_name, project_arg):
                continue
            pid = data.get("id") or str(f)
            if pid in seen:
                continue
            seen.add(pid)
            out.append(data)
    if out:
        return out
    # 兜底：plan_tracker list 文本解析
    try:
        result = subprocess.run(
            [child_python(), str(Path(__file__).parent / "plan_tracker.py"), "list"],
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        return _parse_plan_list_output(result.stdout, project_name)
    except Exception:
        return []


def _filter_plans_by_cluster(plans: list[dict], project_arg: str,
                             cluster_key: str) -> list[dict]:
    """按 cluster 过滤 plan（2026-05-29 复审修复[M21]）。

    双判据（任一命中即保留）：
      1. plan.key 归一化后 == 目标 cluster_id（cluster-save-state 创建时 --key 即 cluster_key）
      2. plan.chapter 落在该 cluster 的章范围内（cluster_lookup 展开；范围未回填则跳过此判据）
    SC-5 纪律：不机械拼 cluster_{ch}，章号→cluster 一律走 cluster_lookup。
    """
    target_cid = None
    rng = None
    if cluster_lookup is not None:
        target_cid = cluster_lookup.normalize_cluster_id(cluster_key)
        rng = cluster_lookup.cluster_id_to_range(project_arg, cluster_key)
    else:
        # cluster_lookup 不可用时退化为纯字符串比对（仍归一三位零填充）
        m = re.search(r"(\d+)", str(cluster_key))
        target_cid = f"cluster_{int(m.group(1)):03d}" if m else None

    def _match(p: dict) -> bool:
        # 判据 1：plan.key 同 cluster
        pkey = p.get("key")
        if pkey is not None and cluster_lookup is not None:
            if cluster_lookup.normalize_cluster_id(pkey) == target_cid:
                return True
        elif pkey is not None and target_cid is not None:
            m = re.search(r"(\d+)", str(pkey))
            if m and f"cluster_{int(m.group(1)):03d}" == target_cid:
                return True
        # 判据 2：plan.chapter 落在 cluster 章范围内
        ch = p.get("chapter")
        if isinstance(ch, int) and rng and rng[0] <= ch <= rng[1]:
            return True
        return False

    return [p for p in plans if _match(p)]


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
    # 2026-05-29 复审修复[M21]：cluster mode 续跑入口
    ap.add_argument("--cluster", type=str, default=None,
                    help="按 cluster 过滤未完成 plan（如 001 / cluster_001）")
    args = ap.parse_args()

    # 接受路径或项目名
    project_path = Path(args.project)
    if project_path.is_dir():
        project_name = project_path.name
    else:
        project_name = args.project

    plans = list_plans_for_project(args.project, project_name)
    if args.ch is not None:
        plans = [p for p in plans if p.get("chapter") == args.ch]
    if args.cluster is not None:
        plans = _filter_plans_by_cluster(plans, args.project, args.cluster)

    if not plans:
        scope = ""
        if args.ch is not None:
            scope = f"，ch{args.ch}"
        elif args.cluster is not None:
            scope = f"，cluster {args.cluster}"
        print(f"[OK] 无 plan 数据（项目 {project_name}{scope}）")
        sys.exit(0)

    # 2026-05-30 北极星复审：主路径直接读 plan JSON，顶层无 status 字段（plan_tracker 用
    # completed_at/aborted_at 时间戳表达完成/放弃，见 plan_tracker is_done/is_aborted）→ 原
    # p.get("status") 恒 None → 所有 plan 误判中断 exit1 + 错误续跑指令。读真实字段（兼容文本兜底的 status）。
    def _done(p):
        return bool(p.get("completed_at")) or p.get("status") in ("DONE", "done", "completed")
    def _aborted(p):
        return bool(p.get("aborted_at")) or p.get("status") in ("ABORT", "abort")
    incomplete = [p for p in plans if not (_done(p) or _aborted(p))]

    print(f"[wal_recovery] 项目 {project_name}: 总 plan {len(plans)} / 未完成 {len(incomplete)}")
    for p in plans:
        ch = p.get("chapter")
        cmd = p.get("command")
        status = "completed" if _done(p) else ("aborted" if _aborted(p) else (p.get("status") or "active"))
        steps_meta = p.get("steps", [])
        # 计算 verified/done 步数
        done_count = sum(1 for s in steps_meta if (s.get("status") in ("completed", "done") or s.get("verified")))
        total = len(steps_meta) if steps_meta else (p.get("total_steps") or 0)
        flag = "✅" if _done(p) else ("⏸" if _aborted(p) else "🔴 中断")
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
