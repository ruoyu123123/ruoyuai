#!/usr/bin/env python3
"""git_snapshot.py — 项目 Git 自动快照（薄·安全·advisory）。

CLAUDE.md「Git 版本快照（自动）」承诺的程序驱动实现（轮次2 全旅程实测抓出：
distill-style/cluster-save-state plan 引用本脚本但文件不存在 → 每次空跑 WARN）。

Git 安全纪律（CLAUDE.md）：预检 git+.git·不 push/pull/force/reset·不改全局 config
（只设本地 user.name/email 兜底）·失败不中断流水线（调用行带 `?` advisory·本脚本
自身也尽量 exit 0）。

用法：
  python git_snapshot.py <project_root> --message "蒸馏作者风格"
退出码：0 成功或安全跳过 / 1 commit 失败（advisory 行不阻断）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _run(args: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    # 狩猎修：无 timeout 时用户机若配了 gpg 签名/钩子·git commit 交互等待 → 流水线挂死
    try:
        return subprocess.run(args, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "[git_snapshot] timeout")


def snapshot(project_root: Path, message: str) -> int:
    if shutil.which("git") is None:
        print("[git_snapshot] git 不在 PATH·跳过", file=sys.stderr)
        return 0
    if not (project_root / ".git").is_dir():
        # 项目还没 init git（init_project 会建·旧项目可能没有）→ 安静初始化
        r = _run(["git", "init"], project_root)
        if r.returncode != 0:
            print(f"[git_snapshot] git init 失败·跳过: {r.stderr.strip()[:120]}")
            return 0
    # 本地身份兜底（不动全局 config）
    for k, v in (("user.name", "ruoyuai"), ("user.email", "ruoyuai@local")):
        if not _run(["git", "config", "--local", k], project_root).stdout.strip():
            _run(["git", "config", "--local", k, v], project_root)
    _run(["git", "add", "-A"], project_root)
    # 无变更 → 跳过（diff --cached --quiet 退出码 0=无变更）
    if _run(["git", "diff", "--cached", "--quiet"], project_root).returncode == 0:
        print("[git_snapshot] 无变更·跳过", file=sys.stderr)
        return 0
    r = _run(["git", "commit", "-m", message], project_root)
    if r.returncode != 0:
        print(f"[git_snapshot] commit 失败: {(r.stderr or r.stdout).strip()[:160]}")
        return 1
    print(f"[git_snapshot] 已快照: {message}", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--message", required=True)
    args = ap.parse_args()
    pr = Path(args.project_root)
    if not pr.is_dir():
        print(f"[git_snapshot] 项目不存在·跳过: {pr}", file=sys.stderr)
        sys.exit(0)
    sys.exit(snapshot(pr, args.message))


if __name__ == "__main__":
    main()
