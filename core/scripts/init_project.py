#!/usr/bin/env python3
"""init_project.py — 新书项目确定性初始化（outline plan step1·阶段2 创建书籍）。

解死锁②：新书项目目录必须在 plan 跑前真实存在（否则 plan_tracker.resolve_project_root 返
None → orchestrator 退化成裸书名相对路径 → scaffold/data_flow/事件簇.json 全错位）。

做三件确定性的事（无 gen-model·纯文件操作）：
  1. 建 workspace/novels/<书名>/_数据库/ + .wal/（GUI 已先 mkdir·此处幂等兜底）
  2. git init（本地·不碰全局 config·失败即返回非 0）
  3. --emit-style-options：扫 workspace/styles/* 出 style_options.json 供 pause 选择
  4. --style <风格库名>：拷该风格的 skill 双文件(作者风格.json + skill_FINAL.md→作者风格_skill.md)
     进项目 _数据库/（作者档第一权威·judge/writer 读·feedback_author_goldstandard_comparison_gate）

用法：
  python init_project.py <项目路径> --scaffold              # 🆕 一键建完整新书骨架（目录+git+34 子系统）
  python init_project.py <项目路径> --scaffold --style <名>  # 一键骨架 + 拷风格档（最全·建书一条命令）
  python init_project.py <项目路径> --emit-style-options    # 列风格库供 pause
  python init_project.py <项目路径> --style <风格库名>       # 拷选中风格的 skill 双文件
退出码：0 成功 / 2 致命（风格库不存在/作者档缺）

🔴 新书/新文件夹创建唯一 sanctioned 入口：禁止在 plan 之外手搓 mkdir 建项目目录——
   一律走本脚本（plan 内 outline.plan.json 分步 init+scaffold；plan 外/CLI 测试/手动用
   `--scaffold` 一键全建）。散落的 ad-hoc mkdir 会漏建 .wal/34 子系统 → 后续 plan 错位。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from proc_utils import run_utf8  # noqa: E402

# frozen-aware 仓库根（workspace 在仓库根下·dev=仓库根·frozen 下 workspace 是用户态外部目录·
# 但项目路径由调用方传绝对路径·此处只用相对推 styles 库）
try:
    from frozen_util import bundle_root as _bundle_root
    _REPO = _bundle_root()
except Exception:
    _REPO = _SCRIPTS.parent.parent


def _styles_dir(project_root: Path | None = None) -> Path:
    """风格库目录。🔴 风格库是**用户数据**(与小说项目是工作区兄弟目录)·**不在 bundle**：
    项目在 <workspace>/novels/<书> → 风格库在 <workspace>/styles。从**项目路径**派生
    (project_root.parent.parent/styles)·非 bundle_root(frozen 下 _internal 无 workspace)。
    无 project_root(测试)时回退 _REPO/workspace/styles(dev)。"""
    if project_root is not None:
        cand = project_root.resolve().parent.parent / "styles"
        if cand.is_dir():
            return cand
    return _REPO / "workspace" / "styles"


def emit_style_options(project_root: Path) -> int:
    """扫风格库出 style_options.json（含 作者风格.json 的才算有效风格）。"""
    styles = []
    sd = _styles_dir(project_root)
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
    src = _styles_dir(project_root) / style_name
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
        print(f"[init_project][WARN] 风格库 {style_name} 无 skill*.md（只拷了作者风格.json）")
    # 绑定风格名到项目：写 style_choice.json 标记（answer.name 口径与
    # reference_pattern_extract 的风格名解析一致），供下游按风格库原文抽取结构基线等消费方解析
    wal = db / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / "style_choice.json").write_text(
        json.dumps({"answer": {"name": style_name}, "source": "copy_style"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    # 拷贝第③步：在项目 作者风格.json 顶层写 style_source（相对仓库根·原文池反查唯一通路·
    # learning_loop/snippet_seed/audit_hub/best-of-N 与 SFS/AV 打分全靠它反查·缺失=打分静默双退化）
    if (src / "skill_FINAL.md").exists():
        rel_target = "skill_FINAL.md"
    elif skill and skill.exists():
        rel_target = skill.name
    else:
        rel_target = "作者风格.json"
    dst_prof = db / "作者风格.json"
    try:
        pdata = json.loads(dst_prof.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[init_project][FATAL] 作者风格.json 读取/解析失败，无法写 style_source: {e}",
              file=sys.stderr)
        return 2
    if not isinstance(pdata, dict):
        print(f"[init_project][FATAL] 作者风格.json 顶层非 dict（{type(pdata).__name__}），"
              f"无法写 style_source: {dst_prof}", file=sys.stderr)
        return 2
    pdata["style_source"] = f"workspace/styles/{style_name}/{rel_target}"
    dst_prof.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def scaffold_subsystems_emit(project_root: Path) -> int:
    """一键建齐 34 子系统骨架（调 scaffold_subsystems emit·幂等不覆盖已填）。
    单入口『建完整新书骨架』用——避免 plan 之外 ad-hoc mkdir 散落漏建子系统。"""
    try:
        import scaffold_subsystems as _scaf
        return _scaf.cmd_emit([str(project_root)])
    except Exception as e:  # scaffold 失败不静默吞·返非 0 让调用方知
        print(f"[init_project][FATAL] scaffold 34 子系统失败: {e}", file=sys.stderr)
        return 1


def git_init(project_root: Path) -> int:
    """本地 git init（不碰全局 config·失败返回非 0·路径含中文加引号由 subprocess list 规避）。"""
    if (project_root / ".git").exists():
        return 0
    if shutil.which("git") is None:
        print("[init_project][FATAL] git 不在 PATH，无法初始化项目仓库", file=sys.stderr)
        return 2
    try:
        r = run_utf8(["git", "init"], cwd=str(project_root), timeout=30)
    except Exception as e:
        print(f"[init_project][FATAL] git init 异常: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()[:200]
        print(f"[init_project][FATAL] git init 失败: {msg}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--style", default=None, help="选中的风格库名（拷 skill 双文件）")
    ap.add_argument("--emit-style-options", action="store_true",
                    help="扫风格库出 style_options.json 供 pause 选择")
    ap.add_argument("--scaffold", action="store_true",
                    help="🆕 一键建齐 34 子系统骨架（建完整新书骨架·plan 外/手动用·幂等）")
    ap.add_argument("--no-git", action="store_true", help="跳过 git init（测试用）")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_absolute():
        project_root = _REPO / args.project
    (project_root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
    if not args.no_git:
        rc = git_init(project_root)
        if rc != 0:
            return rc

    if args.scaffold:
        rc = scaffold_subsystems_emit(project_root)
        if rc != 0:
            return rc
        if not (args.emit_style_options or args.style):
            print(f"[init_project] 一键骨架完成 {project_root}（目录+git+34 子系统）")
            return 0

    if args.emit_style_options:
        return emit_style_options(project_root)
    if args.style:
        return copy_style(project_root, args.style)
    print(f"[init_project] 初始化 {project_root}（无 --style/--emit-style-options/--scaffold·仅建目录）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
