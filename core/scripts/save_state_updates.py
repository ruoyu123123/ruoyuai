#!/usr/bin/env python3
"""save_state_updates.py — cluster-save-state step 9 章节级数据库更新统一入口（合并 5 个 update 脚本 · v26 cluster-only）

合并源：
- offscreen_update.py（offscreen_actions_executed 字段处理）
- declarative_data_update.py（6 类声明式字段更新）
- character_arc_update.py（character_arc_state.json stage_log 更新）
- character_lazy_spawn.py（章节中出现的新角色自动入档）
- fate_engine.py update <ch>（fate event 应用）

调用方式（v26 唯一入口）：
- python save_state_updates.py <project> --cluster <key> [--all | --only offscreen,declarative,...]
  （自动展开 cluster 的 chapter_range，for each ch 调子模块）
  🔴 v26: chapter mode --ch 已废弃移除

退出码：0 成功 / 1 部分失败 / 2 fatal
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

# 5 个子模块（每个仍是独立 .py，本 wrapper 用 subprocess 调用 · 简单不重写）
SUB_MODULES = [
    ("offscreen", "offscreen_update.py", ["project", "chapter"]),
    ("declarative", "declarative_data_update.py", ["project", "chapter"]),
    ("character_arc", "character_arc_update.py", ["project", "chapter"]),
    ("character_lazy_spawn", "character_lazy_spawn.py", ["project", "ch"]),
    ("fate_engine_update", "fate_engine.py", ["project", "update", "chapter"]),  # 三段式 CLI
]


def get_cluster_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
    """从 事件簇.json 找 cluster 的 chapter_range，返回 [ch_start, ..., ch_end]。"""
    shijianji_path = project_root / "_数据库" / "事件簇.json"
    if not shijianji_path.exists():
        return []
    try:
        data = json.loads(shijianji_path.read_text(encoding="utf-8"))
        for c in data.get("clusters", []):
            cid = c.get("cluster_id", "")
            if cid == cluster_key or cid.replace("cluster_", "") == cluster_key.replace("cluster_", ""):
                cr = c.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    return list(range(cr[0], cr[1] + 1))
                elif isinstance(cr, str) and "-" in cr:
                    a, b = cr.split("-")
                    return list(range(int(a), int(b) + 1))
    except Exception:
        pass
    return []


def run_one_module(module_name: str, script_name: str, args_spec: list, project: str, chapter: int) -> tuple[bool, str]:
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        return False, f"脚本不存在: {script_path}"

    # 构造 CLI 参数
    args_map = {"project": project, "chapter": str(chapter), "ch": str(chapter), "update": "update"}
    cli_args = [args_map[a] for a in args_spec]

    try:
        r = subprocess.run(
            [sys.executable, str(script_path), *cli_args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        if r.returncode != 0:
            return False, f"rc={r.returncode}: {(r.stderr or r.stdout)[:200]}"
        return True, (r.stdout or "")[:200]
    except subprocess.TimeoutExpired:
        return False, "timeout (120s)"
    except Exception as e:
        return False, str(e)[:200]


def run_updates_for_chapter(project: str, chapter: int, only: set[str] | None) -> dict:
    results = {}
    for name, script, args_spec in SUB_MODULES:
        if only is not None and name not in only:
            continue
        ok, msg = run_one_module(name, script, args_spec, project, chapter)
        results[name] = {"ok": ok, "msg": msg}
        status = "✓" if ok else "✗"
        print(f"  [ch{chapter}] {status} {name}: {msg[:80]}")
    return results


def main():
    # 🔴 v26: chapter mode --ch 已废弃移除，仅留 --cluster <key>。
    parser = argparse.ArgumentParser(description="cluster-save-state step 9 章节级数据库更新（5 in 1 · v26 cluster-only）")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True, help="v26: 必填 cluster_key（chapter mode --ch 已删）")
    parser.add_argument("--only", help="只跑特定模块，逗号分隔（offscreen,declarative,...）")
    parser.add_argument("--all", action="store_true", help="跑全部 5 个模块（默认）")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[FATAL] 项目 _数据库 不存在: {project_root}", file=sys.stderr)
        return 2

    only_set = set(args.only.split(",")) if args.only else None

    chapters = get_cluster_chapter_range(project_root, args.cluster)
    if not chapters:
        print(f"[FATAL] cluster {args.cluster} 未找到 chapter_range", file=sys.stderr)
        return 2
    print(f"[cluster {args.cluster}] 展开 {len(chapters)} 章: {chapters}")

    all_results = {}
    fail_count = 0
    for ch in chapters:
        r = run_updates_for_chapter(str(project_root), ch, only_set)
        all_results[ch] = r
        fail_count += sum(1 for v in r.values() if not v["ok"])

    total_runs = sum(len(r) for r in all_results.values())
    success_runs = total_runs - fail_count
    print(f"\n[完成] 总跑 {total_runs}, 成功 {success_runs}, 失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
