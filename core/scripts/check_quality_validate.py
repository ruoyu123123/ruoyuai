#!/usr/bin/env python3
"""check_quality_validate.py — /check-quality step 2 wrapper（2026-06-22 G2 P0a）

把 validate_chapter.validate_cluster() 在 in-process 跑完后落盘为 JSON·plan
step 2 的 expected_outputs 直读该路径校验。不重新实现校验逻辑（北极星⑥），
仅薄包装：① 解析 --cluster <key> --out <path>；② import validate_chapter
跑 cluster 视野（CLUSTER_MODE=1·CLUSTER_PER_CHAPTER=1·逐章 hard_gate 聚合）；
③ JSON dump 落盘。

【为什么不直接复用 validate_chapter.py main()】
validate_chapter 主入口把 JSON 打到 stdout（plan step 不能据此判 expected_outputs
存在性）。给 main() 加 --out 会污染单章模式；薄 wrapper 隔离更干净，且让 check-quality
plan 完全自包含（step 1 audit_hub 自落盘 / step 2 本 wrapper 落盘 / step 3
check_quality_judge.py 落盘）。

【退出码】
  0  = passed (无 hard_gate 残留)
  1  = errors/warnings 但无 fatal（检查模式下仍归 ok 由 plan control_flow 决定）
  2  = fatal（章范围解析失败 / 章节不存在 / 校验器全挂）

【用法】
  python core/scripts/check_quality_validate.py <项目路径> --cluster <key> --out <相对路径>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import validate_chapter as vc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="check-quality step 2 · validate_cluster 落盘 wrapper")
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--cluster", required=True,
                    help="cluster key（纯数字或 cluster_NNN 形态·后者会去前缀）")
    ap.add_argument("--out", required=True,
                    help="输出 JSON 落盘路径（绝对或相对项目根）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        return 2

    key = args.cluster
    if key.startswith("cluster_"):
        key = key[len("cluster_"):]

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = project_root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = vc.validate_cluster(project_root, key)
    except Exception as e:  # noqa: BLE001 — fatal 兜底进 JSON 不让 plan 看不到
        result = {
            "passed": False,
            "fatal_count": 1, "error_count": 0, "warning_count": 0,
            "chapter_file": f"cluster_{key}",
            "errors": [{"code": "VALIDATE_CLUSTER_EXCEPTION",
                        "severity": "fatal",
                        "msg": f"validate_cluster() 抛异常: {type(e).__name__}: {e}"}],
        }

    # 写报告（不论通过与否都落盘·plan step 据 expected_outputs 校验存在性，verdict 看内容）
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要到 stderr（不污染 stdout · plan 日志可见）
    print(f"== cluster_{key} cross-chapter validate ==", file=sys.stderr)
    print(f"  chapters:   {result.get('chapter_file', '?')}", file=sys.stderr)
    print(f"  fatal:      {result.get('fatal_count', 0)}", file=sys.stderr)
    print(f"  errors:     {result.get('error_count', 0)}", file=sys.stderr)
    print(f"  warnings:   {result.get('warning_count', 0)}", file=sys.stderr)
    print(f"  报告:       {out_path}", file=sys.stderr)

    if result.get("fatal_count", 0) > 0:
        return 2
    if not result.get("passed", False):
        return 1
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
