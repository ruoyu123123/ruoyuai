#!/usr/bin/env python3
"""save_state_updates.py — cluster-save-state step 9 章节级数据库更新统一入口（合并 5 个 update 脚本 · v26 cluster-only）

合并源：
- offscreen_update.py（offscreen_actions_executed 字段处理）
- declarative_data_update.py（6 类声明式字段更新）
- character_arc_update.py（character_arc_state.json stage_log 更新）
- fate_engine.py update <ch>（fate event 应用）

🔴 2026-06-28 不降级收尾：character_lazy_spawn.py 已删除（孤儿码）——它读 writer
changes.factual.new_entities 自动入档新角色，但 writer 已不自报 factual（A 类清理删除），
其输入恒空=永远 no-op。新角色入档的唯一权威路径 = novel-archivist 读正文产 archive →
apply_archive.py 回库人物卡/角色池（cluster-save-state step5/6·archive 单一来源）。

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
from frozen_util import child_python, scripts_dir  # frozen-aware 子解释器/脚本目录（dev=no-op）
from pathlib import Path
import state_cli_guard

SCRIPT_DIR = scripts_dir()

# 5 个子模块（每个仍是独立 .py，本 wrapper 用 subprocess 调用 · 简单不重写）
SUB_MODULES = [
    ("offscreen", "offscreen_update.py", ["project", "chapter"]),
    ("declarative", "declarative_data_update.py", ["project", "chapter"]),
    ("character_arc", "character_arc_update.py", ["project", "chapter"]),
    # 🔴 2026-06-28 不降级收尾：character_lazy_spawn 已删（孤儿·输入 writer new_entities 已删）
    #   —— 新角色入档走 archivist → apply_archive（step5/6·archive 单一权威路径）。
    ("fate_engine_update", "fate_engine.py", ["project", "update", "chapter"]),  # 三段式 CLI
]

# 2026-05-29 复审修复 [H4]：split_cluster_changes.py 把整 cluster 的 factual/self_eval
# 段「平铺」到每章 _changes.json（每章内容完全相同）。declarative_data_update 读 factual
# 里的 relationship_changes / faction_standing_changes / travel_log_added 等「增量」字段——
# 逐章 apply N 次 → 关系/阵营 delta 被乘 N 倍、travel_log 被复制 N 条（正典污染）。
# 修：declarative 是 cluster 级整份增量，只在「代表章」（cluster 首章）apply 一次，
# 不逐章重放。其余模块按需逐章跑：
#   - offscreen：self_eval.offscreen_actions_executed 也是 cluster 级整份（平铺相同），
#     且 offscreen_update 本身幂等（done=true 不反向），但同样只跑首章避免无谓 N 次。
#   - character_arc：按 ch 映射 stage（每章语义不同，幂等 set 不累加）→ 逐章跑。
#   - fate_engine_update：按 status!=completed 守卫幂等 → 逐章跑（真正应用在世界演化层另有幂等）。
# 「只跑首章」的模块（读 cluster 级整份增量，逐章会乘倍）：
CLUSTER_ONCE_MODULES = {"offscreen", "declarative"}


def get_cluster_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
    """拿 cluster 的 chapter_range，返回 [ch_start, ..., ch_end]。

    🔴 2026-06-17 bug-hunt 修：改走 `cluster_lookup.cluster_id_to_range`（唯一权威反查·北极星①·
    可读 splitter 写回前的 blueprint 范围）。原只读 事件簇.json → blueprint-only 状态返 [] → main FATAL exit2
    卡死 cluster-save-state step9（与 save_state.py / evaluators 权威源不一致·三脚本对齐 cluster_lookup）。"""
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
    """对单章跑各子模块。

    2026-05-29 复审修复 [H4]：is_representative_ch=False 时跳过 CLUSTER_ONCE_MODULES
    （declarative/offscreen 这类读 cluster 级整份增量的模块），只让它们在 cluster 首章
    跑一次，避免 relationship/faction delta 被乘 N 倍、travel_log 被复制 N 条。
    """
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
    # 2026-05-29 复审修复 [H4]：cluster 首章 = 代表章，cluster 级整份增量模块只在此跑一次。
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
