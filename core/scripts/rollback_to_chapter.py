"""rollback_to_chapter.py — 章节级回滚（v17.5 C6 / LangGraph time-travel 启发）

回滚到指定章节的 git commit 状态：
- 13 JSON 数据库还原
- 章节 txt 仅保留 ≤ target 的
- .style_directive/ .manifest/ .wal/ 中超过 target 的清理
- 活跃 plan abort

用法：
    python rollback_to_chapter.py <项目路径> <target_ch> [--dry-run] [--keep-orphans]

退出码：
    0 成功
    1 未找到目标章节 commit
    2 git 操作失败
"""

import sys
import json
import subprocess
import re
from pathlib import Path


def find_chapter_commit(project_root: Path, target_ch: int) -> str | None:
    """找到 'feat(ch-{target_ch}):' 的最近 git commit hash。"""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_root), "log", "--format=%H %s", "-200"],
            text=True, encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        m = re.match(r"^(\w+)\s+feat\(ch-(\d+)\)", line)
        if m and int(m.group(2)) == target_ch:
            return m.group(1)
    return None


def list_chapters_above(project_root: Path, target_ch: int) -> list[Path]:
    """返回所有 ch > target_ch 的章节 txt 文件。"""
    out = []
    for f in project_root.rglob("第*章*.txt"):
        m = re.search(r"第(\d+)章", f.name)
        if m and int(m.group(1)) > target_ch:
            out.append(f)
    return out


def list_meta_files_above(project_root: Path, target_ch: int) -> list[Path]:
    """返回 .manifest / .style_directive / .wal 中 ch > target_ch 的文件。"""
    db = project_root / "_数据库"
    out = []
    for sub in [".manifest", ".style_directive", ".wal"]:
        d = db / sub
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            m = re.search(r"ch[_]?(\d+)|第(\d+)章", f.name)
            if m:
                num = int(next(g for g in m.groups() if g))
                if num > target_ch:
                    out.append(f)
    return out


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    target_ch = int(args[1])
    dry_run = "--dry-run" in args
    keep_orphans = "--keep-orphans" in args

    if not project_root.exists():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        sys.exit(2)
    if not (project_root / ".git").exists():
        print(f"[FATAL] 项目不是 git 仓库", file=sys.stderr)
        sys.exit(2)

    # 1) 查 target commit
    commit = find_chapter_commit(project_root, target_ch)
    if not commit:
        print(f"[FATAL] 未找到 ch-{target_ch} 的 commit（git log 中无 feat(ch-{target_ch}):）", file=sys.stderr)
        sys.exit(1)

    print(f"[Rollback Plan] 项目：{project_root.name}")
    print(f"  目标章节: ch{target_ch}")
    print(f"  目标 commit: {commit[:8]}")

    # 2) 列出要删除的文件
    chs_to_remove = list_chapters_above(project_root, target_ch)
    meta_to_remove = list_meta_files_above(project_root, target_ch)
    print(f"\n[要删除]")
    print(f"  章节 txt: {len(chs_to_remove)} 个")
    for f in chs_to_remove[:5]:
        print(f"    - {f.relative_to(project_root)}")
    if len(chs_to_remove) > 5:
        print(f"    ... +{len(chs_to_remove)-5} more")
    print(f"  meta 文件 (.manifest/.style_directive/.wal): {len(meta_to_remove)} 个")
    for f in meta_to_remove[:5]:
        print(f"    - {f.relative_to(project_root)}")
    if len(meta_to_remove) > 5:
        print(f"    ... +{len(meta_to_remove)-5} more")

    if dry_run:
        print(f"\n[DRY RUN] 不执行。去掉 --dry-run 实际回滚。")
        sys.exit(0)

    # 3) 用户确认
    print(f"\n⚠️  危险操作：将硬重置 git 到 commit {commit[:8]}")
    print(f"     建议先 git stash 或 git branch backup-$(date +%s) 备份")
    confirm = input("继续？输入 'YES' 确认：").strip()
    if confirm != "YES":
        print("已取消")
        sys.exit(0)

    # 4) 执行
    try:
        # 备份当前 HEAD 到一个 ref
        backup_ref = f"backup-rollback-{target_ch}-{subprocess.check_output(['git','-C',str(project_root),'rev-parse','--short','HEAD'],text=True).strip()}"
        subprocess.run(["git", "-C", str(project_root), "branch", backup_ref], check=False)
        print(f"  备份当前 HEAD 到 {backup_ref}")

        # hard reset 到 target
        subprocess.run(["git", "-C", str(project_root), "reset", "--hard", commit], check=True)
        print(f"  git reset --hard {commit[:8]} ✓")
    except subprocess.CalledProcessError as e:
        print(f"[FATAL] git 操作失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 5) 清理 orphan 文件（git reset 不会自动删除未跟踪文件）
    if not keep_orphans:
        for f in chs_to_remove:
            if f.exists():
                f.unlink()
        for f in meta_to_remove:
            if f.exists():
                f.unlink()
        print(f"  清理 orphan 文件 ✓")

    # 6) abort 活跃 plan
    plans_dir = project_root / "_数据库" / ".plans"
    if plans_dir.exists():
        for p in plans_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                ch = d.get("chapter")
                if ch and ch > target_ch and not d.get("abort_reason"):
                    d["abort_reason"] = f"rollback to ch{target_ch}"
                    d["completed_at"] = subprocess.check_output(
                        ["date", "-Iseconds"], text=True
                    ).strip()
                    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                continue
    print(f"  活跃 plan abort ✓")
    print(f"\n[OK] 已回滚到 ch{target_ch}。备份 ref: {backup_ref}")
    print(f"     如需还原：git -C {project_root} reset --hard {backup_ref}")


if __name__ == "__main__":
    main()
