#!/usr/bin/env python3
"""cluster-save-state step 9 的数据库更新统一入口。

入口接收 `--cluster <key>`，反查物理章节范围后调用：
- offscreen_update.py（offscreen_actions_executed 字段处理）
- declarative_data_update.py（6 类声明式字段更新）
- character_arc_update.py（character_arc_state.json stage_log 更新）
- fate_engine.py update <ch>（fate event 应用）

新角色由 novel-archivist 读取正文生成 archive，再由 apply_archive.py 回库。

调用方式：
- python save_state_updates.py <project> --cluster <key> [--all | --only offscreen,declarative,...]
  （自动展开 cluster 的 chapter_range，for each ch 调子模块）

退出码：0 成功 / 1 部分失败 / 2 fatal
"""
import argparse
import json
import subprocess
import sys
from frozen_util import child_python, scripts_dir  # frozen-aware 子解释器/脚本目录（dev=no-op）
from pathlib import Path
import state_cli_guard

SCRIPT_DIR = scripts_dir()

# 子模块保持独立，本 wrapper 通过 subprocess 调用。
SUB_MODULES = [
    ("offscreen", "offscreen_update.py", ["project", "chapter"]),
    ("declarative", "declarative_data_update.py", ["project", "chapter"]),
    ("character_arc", "character_arc_update.py", ["project", "chapter"]),
    ("fate_engine_update", "fate_engine.py", ["project", "update", "chapter"]),  # 三段式 CLI
]

# `split_cluster_changes.py` 会把 cluster 级 changes 平铺到物理章节。
# 读取整块增量的模块只在代表章执行一次，避免重复应用：
#   - offscreen：self_eval.offscreen_actions_executed 也是 cluster 级整份（平铺相同），
#     且 offscreen_update 本身幂等（done=true 不反向），但同样只跑首章避免无谓 N 次。
#   - character_arc：按 ch 映射 stage（每章语义不同，幂等 set 不累加）→ 逐章跑。
#   - fate_engine_update：按 status!=completed 守卫幂等 → 逐章跑（真正应用在世界演化层另有幂等）。
# 只跑代表章的模块：
CLUSTER_ONCE_MODULES = {"offscreen", "declarative"}


def get_cluster_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
    """通过 cluster_lookup 返回 `[ch_start, ..., ch_end]`。"""
    import cluster_lookup as _cl
    cid = _cl.normalize_cluster_id(cluster_key)
    if not cid:
        raise RuntimeError(f"非法 cluster_key: {cluster_key!r}")
    cr = _cl.cluster_id_to_range(project_root, cid)
    if cr and len(cr) == 2:
        return list(range(int(cr[0]), int(cr[1]) + 1))
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
            [child_python(), str(script_path), *cli_args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            env=state_cli_guard.internal_env(),
        )
        if r.returncode != 0:
            return False, f"rc={r.returncode}: {(r.stderr or r.stdout)[:200]}"
        return True, (r.stdout or "")[:200]
    except subprocess.TimeoutExpired:
        return False, "timeout (120s)"
    except Exception as e:
        return False, str(e)[:200]


def run_updates_for_chapter(project: str, chapter: int, only: set[str] | None,
                            is_representative_ch: bool = True) -> dict:
    """对单章运行子模块；非代表章跳过 cluster 级整份增量模块。"""
    results = {}
    for name, script, args_spec in SUB_MODULES:
        if only is not None and name not in only:
            continue
        if not is_representative_ch and name in CLUSTER_ONCE_MODULES:
            # 非代表章：cluster 级整份增量模块跳过（已在首章 apply 过，重放会乘倍）
            results[name] = {"ok": True, "msg": "skipped (cluster-once, applied at representative ch)"}
            print(f"  [ch{chapter}] · {name}: 跳过（cluster 级整份增量已在首章应用）")
            continue
        ok, msg = run_one_module(name, script, args_spec, project, chapter)
        results[name] = {"ok": ok, "msg": msg}
        status = "✓" if ok else "✗"
        print(f"  [ch{chapter}] {status} {name}: {msg[:80]}")
    return results


def main():
    parser = argparse.ArgumentParser(description="cluster-save-state step 9 章节级数据库更新（5 in 1）")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True, help="必填 cluster_key")
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
    # cluster 首章是代表章，cluster 级整份增量模块只在此执行。
    representative_ch = chapters[0]
    for ch in chapters:
        r = run_updates_for_chapter(str(project_root), ch, only_set,
                                    is_representative_ch=(ch == representative_ch))
        all_results[ch] = r
        fail_count += sum(1 for v in r.values() if not v["ok"])

    total_runs = sum(len(r) for r in all_results.values())
    success_runs = total_runs - fail_count
    print(f"\n[完成] 总跑 {total_runs}, 成功 {success_runs}, 失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
