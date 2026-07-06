#!/usr/bin/env python3
"""git_snapshot.py — 项目 Git 自动快照（required）。

CLAUDE.md「Git 版本快照（自动）」承诺的程序驱动实现（轮次2 全旅程实测抓出：
distill-style/cluster-save-state plan 引用本脚本但文件不存在 → 每次空跑 WARN）。

Git 安全纪律（CLAUDE.md）：预检 git+.git·不 push/pull/force/reset·不改全局 config
（只设本地 user.name/email）·失败返回非 0，交给 plan_tracker 阻断当前 plan。

用法：
  python git_snapshot.py <project_root> --message "蒸馏作者风格" [--marker _数据库/.wal/xxx.json]
退出码：0 成功或工作区无变更 / 2 前置或 commit 失败
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _run(args: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    # 狩猎修：无 timeout 时用户机若配了 gpg 签名/钩子·git commit 交互等待 → 流水线挂死
    try:
        return subprocess.run(args, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "[git_snapshot] timeout")


def _write_marker(marker: Path | None, payload: dict) -> None:
    if marker is None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(project_root: Path, message: str, marker: Path | None = None) -> int:
    if shutil.which("git") is None:
        print("[git_snapshot][FATAL] git 不在 PATH", file=sys.stderr)
        return 2
    if not project_root.is_dir():
        print(f"[git_snapshot][FATAL] 项目不存在: {project_root}", file=sys.stderr)
        return 2
    if not (project_root / ".git").is_dir():
        r = _run(["git", "init"], project_root)
        if r.returncode != 0:
            print(f"[git_snapshot][FATAL] git init 失败: {(r.stderr or r.stdout).strip()[:160]}", file=sys.stderr)
            return 2
    # 本地身份兜底（不动全局 config）
    for k, v in (("user.name", "ruoyuai"), ("user.email", "ruoyuai@local")):
        if not _run(["git", "config", "--local", k], project_root).stdout.strip():
            _run(["git", "config", "--local", k, v], project_root)
    _run(["git", "add", "-A"], project_root)
    # 无变更 → 跳过（diff --cached --quiet 退出码 0=无变更）
    if _run(["git", "diff", "--cached", "--quiet"], project_root).returncode == 0:
        sha_r = _run(["git", "rev-parse", "--short", "HEAD"], project_root)
        sha = (sha_r.stdout or "").strip() if sha_r.returncode == 0 else ""
        _write_marker(marker, {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "message": message,
            "committed": False,
            "reason": "no_changes",
            "head": sha,
        })
        print("[git_snapshot] 无变更", file=sys.stderr)
        return 0
    r = _run(["git", "commit", "-m", message], project_root)
    if r.returncode != 0:
        print(f"[git_snapshot][FATAL] commit 失败: {(r.stderr or r.stdout).strip()[:160]}", file=sys.stderr)
        return 2
    sha_r = _run(["git", "rev-parse", "--short", "HEAD"], project_root)
    sha = (sha_r.stdout or "").strip() if sha_r.returncode == 0 else ""
    _write_marker(marker, {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "message": message,
        "committed": True,
        "head": sha,
    })
    print(f"[git_snapshot] 已快照: {message}", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--message", required=True)
    ap.add_argument("--marker", default=None, help="成功或无变更时写入的 marker JSON")
    args = ap.parse_args()
    pr = Path(args.project_root)
    marker = Path(args.marker) if args.marker else None
    if marker is not None and not marker.is_absolute():
        marker = pr / marker
    sys.exit(snapshot(pr, args.message, marker))


if __name__ == "__main__":
    main()
