#!/usr/bin/env python3
"""finalize_distill.py — 蒸馏 phase-5 定稿（阶段3·确定性·零 LLM）。

把最新 skill_v{N}.md → skill_FINAL.md（出货版）+ 作者风格.json → 作者风格_FINAL.json +
写 distillation_log.md（蒸馏摘要）。供 distill_finalize_verify 回灌闸 + 写作端加载。

用法：
  python finalize_distill.py <project_root> [--sfs <eval.json>]
退出码：0 成功 / 1 无 skill 可定稿
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _latest_skill(project_root: Path) -> Path | None:
    skills = list(project_root.glob("skill_v*.md"))
    if not skills:
        # 兜底：任何 skill*.md（非 FINAL）
        skills = [p for p in project_root.glob("skill*.md")
                  if "FINAL" not in p.stem]
    if not skills:
        return None

    def _ver(p):
        m = re.search(r"v(\d+)", p.stem)
        return int(m.group(1)) if m else -1
    return max(skills, key=_ver)


def finalize(project_root: Path, sfs_path: Path | None = None) -> int:
    skill = _latest_skill(project_root)
    if skill is None:
        print(f"[finalize] 无 skill_v*.md 可定稿: {project_root}", file=sys.stderr)
        return 1
    final_skill = project_root / "skill_FINAL.md"
    final_skill.write_text(skill.read_text(encoding="utf-8"), encoding="utf-8")

    prof = project_root / "作者风格.json"
    if prof.exists():
        (project_root / "作者风格_FINAL.json").write_text(
            prof.read_text(encoding="utf-8"), encoding="utf-8")

    # distillation_log.md 摘要
    sfs_line = ""
    if sfs_path and sfs_path.exists():
        try:
            ev = json.loads(sfs_path.read_text(encoding="utf-8"))
            score = ev.get("sfs_quick") or ev.get("total") or \
                (ev.get("programmatic_score") or {}).get("total")
            grade = ev.get("grade") or (ev.get("programmatic_score") or {}).get("grade", "")
            if score is not None:
                sfs_line = f"- 复刻 SFS：{score} {grade}\n"
        except (OSError, json.JSONDecodeError):
            pass
    raw_n = len(list((project_root / "原文").glob("*.txt"))) \
        if (project_root / "原文").is_dir() else 0
    cl_n = 0
    ci = project_root / "cluster_index.json"
    if ci.exists():
        try:
            cl_n = len(json.loads(ci.read_text(encoding="utf-8")).get("clusters", []))
        except (OSError, json.JSONDecodeError):
            pass
    log = (f"# 蒸馏定稿日志\n\n"
           f"- 出货 skill：{final_skill.name}（源 {skill.name}）\n"
           f"- 原文章数：{raw_n}\n"
           f"- 故事块数：{cl_n}\n"
           f"{sfs_line}")
    (project_root / "distillation_log.md").write_text(log, encoding="utf-8")
    print(f"[finalize] 定稿 {skill.name} → skill_FINAL.md（原文 {raw_n} 章 / {cl_n} cluster）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--sfs", help="style_evaluator eval JSON（写进 log）")
    args = ap.parse_args()
    pr = Path(args.project_root)
    sfs = Path(args.sfs) if args.sfs else None
    if sfs and not sfs.is_absolute():
        sfs = pr / args.sfs
    sys.exit(finalize(pr, sfs))


if __name__ == "__main__":
    main()
