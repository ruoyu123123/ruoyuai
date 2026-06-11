#!/usr/bin/env python3
"""init_project.py — 新书项目确定性初始化（outline plan step1·阶段2 创建书籍）。

解死锁②：新书项目目录必须在 plan 跑前真实存在（否则 plan_tracker.resolve_project_root 返
None → orchestrator 退化成裸书名相对路径 → scaffold/data_flow/事件簇.json 全错位）。

做三件确定性的事（无 gen-model·纯文件操作）：
  1. 建 workspace/novels/<书名>/_数据库/ + .wal/（GUI 已先 mkdir·此处幂等兜底）
  2. git init（本地·不碰全局 config·失败不阻断）
  3. --emit-style-options：扫 workspace/styles/* 出 style_options.json 供 pause 选择
  4. --style <风格库名>：拷该风格的 skill 双文件(作者风格.json + skill_FINAL.md→作者风格_skill.md)
     进项目 _数据库/（作者档第一权威·judge/writer 读·feedback_author_goldstandard_comparison_gate）

用法：
  python init_project.py <项目路径> --emit-style-options    # 列风格库供 pause
  python init_project.py <项目路径> --style <风格库名>       # 拷选中风格的 skill 双文件
退出码：0 成功 / 2 致命（风格库不存在/作者档缺）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# frozen-aware 仓库根（workspace 在仓库根下·dev=仓库根·frozen 下 workspace 是用户态外部目录·
# 但项目路径由调用方传绝对路径·此处只用相对推 styles 库）
try:
    from frozen_util import bundle_root as _bundle_root
    _REPO = _bundle_root()
except Exception:
    _REPO = _SCRIPTS.parent.parent


def _styles_dir() -> Path:
    return _REPO / "workspace" / "styles"


def emit_style_options(project_root: Path) -> int:
    """扫风格库出 style_options.json（含 作者风格.json 的才算有效风格）。"""
    styles = []
    sd = _styles_dir()
    if sd.is_dir():
        for d in sorted(sd.iterdir()):
            if d.is_dir() and (d / "作者风格.json").exists():
                # 取风格档里的作者名/书名当 description（读不到则用目录名）
                desc = ""
                try:
                    prof = json.loads((d / "作者风格.json").read_text(encoding="utf-8"))
                    desc = str(prof.get("author") or prof.get("作者") or
                               prof.get("book") or prof.get("书名") or "")[:40]
                except (OSError, json.JSONDecodeError):
                    pass
                styles.append({"name": d.name, "title": d.name,
                               "description": desc or f"风格库：{d.name}"})
    wal = project_root / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    out = wal / "style_options.json"
    out.write_text(json.dumps({"styles": styles}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[init_project] {len(styles)} 个风格库 → {out}")
    return 0


def copy_style(project_root: Path, style_name: str) -> int:
    """拷选中风格的 skill 双文件进项目 _数据库（作者档第一权威）。"""
    src = _styles_dir() / style_name
    if not src.is_dir():
        print(f"[init_project][FATAL] 风格库不存在: {src}", file=sys.stderr)
        return 2
    prof = src / "作者风格.json"
    if not prof.exists():
        print(f"[init_project][FATAL] 风格库缺作者风格.json: {prof}", file=sys.stderr)
        return 2
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(prof), str(db / "作者风格.json"))
    # skill：优先 skill_FINAL.md·否则任一 skill*.md → 作者风格_skill.md
    skill = src / "skill_FINAL.md"
    if not skill.exists():
        cands = sorted(src.glob("skill*.md")) + sorted(src.glob("*skill*.md"))
        skill = cands[0] if cands else None
    if skill and skill.exists():
        shutil.copy2(str(skill), str(db / "作者风格_skill.md"))
        print(f"[init_project] 拷 skill 双文件(作者风格.json + 作者风格_skill.md) from {style_name}")
    else:
        print(f"[init_project][WARN] 风格库 {style_name} 无 skill*.md（只拷了作者风格.json）",
              file=sys.stderr)
    return 0


def git_init(project_root: Path) -> None:
    """本地 git init（不碰全局 config·失败不阻断·路径含中文加引号由 subprocess list 规避）。"""
    if (project_root / ".git").exists():
        return
    try:
        if shutil.which("git"):
            subprocess.run(["git", "init"], cwd=str(project_root),
                           capture_output=True, timeout=30)
    except Exception:
        pass  # git 失败不阻断初始化


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--style", default=None, help="选中的风格库名（拷 skill 双文件）")
    ap.add_argument("--emit-style-options", action="store_true",
                    help="扫风格库出 style_options.json 供 pause 选择")
    ap.add_argument("--no-git", action="store_true", help="跳过 git init（测试用）")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_absolute():
        project_root = _REPO / args.project
    (project_root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
    if not args.no_git:
        git_init(project_root)

    if args.emit_style_options:
        return emit_style_options(project_root)
    if args.style:
        return copy_style(project_root, args.style)
    print(f"[init_project] 初始化 {project_root}（无 --style/--emit-style-options·仅建目录）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
