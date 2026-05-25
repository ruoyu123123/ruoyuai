#!/usr/bin/env python3
"""save_state_evaluators.py — save-state step 9 章节级 evaluator 统一入口（合并 4 个 evaluator）

合并源：
- clock_engine.py tick <ch>（Clock 系统满格触发）
- narrator_calibrate.py --ch <ch>（Storyteller 节拍器校准）
- stress_evaluator.py --ch <ch>（主角 Stress 评估 + Mental Break 抽卡）
- relationship_evaluator.py --ch <ch>（关系 heart_event 评估）

调用方式：
- chapter mode: python save_state_evaluators.py <project> --ch N [--all | --only clock,narrator,...]
- cluster mode: python save_state_evaluators.py <project> --cluster <key> [--all]

退出码：0 成功 / 1 部分失败（部分系统未启用算 OK，不算失败）/ 2 fatal
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

SUB_MODULES = [
    ("clock", "clock_engine.py", ["project", "tick", "chapter"]),
    ("narrator", "narrator_calibrate.py", ["project", "--ch", "chapter"]),
    ("stress", "stress_evaluator.py", ["project", "--ch", "chapter"]),
    ("relationship", "relationship_evaluator.py", ["project", "--ch", "chapter"]),
]


def get_cluster_chapter_range(project_root: Path, cluster_key: str) -> list[int]:
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
        return True, "[SKIP] 脚本不存在"  # 不算失败（evaluator 是可选系统）

    args_map = {"project": project, "chapter": str(chapter), "tick": "tick", "--ch": "--ch"}
    cli_args = [args_map.get(a, a) for a in args_spec]

    try:
        r = subprocess.run(
            [sys.executable, str(script_path), *cli_args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        # evaluator 退出码 1/2 通常是「触发 mental break / clock 满格」等业务事件，不算失败
        if r.returncode > 2:
            return False, f"rc={r.returncode}: {(r.stderr or r.stdout)[:200]}"
        # 检查 stderr 是否含 SKIP 标记（系统未启用）
        out = (r.stdout or "") + (r.stderr or "")
        if "[SKIP]" in out or "未启用" in out:
            return True, "[SKIP] 系统未启用"
        return True, (r.stdout or "")[:150]
    except subprocess.TimeoutExpired:
        return False, "timeout (60s)"
    except Exception as e:
        return False, str(e)[:200]


def run_evaluators_for_chapter(project: str, chapter: int, only: set[str] | None) -> dict:
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
    parser = argparse.ArgumentParser(description="save-state step 9 章节级 evaluator 统一入口（4 in 1）")
    parser.add_argument("project")
    parser.add_argument("--ch", type=int)
    parser.add_argument("--cluster")
    parser.add_argument("--only")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[FATAL] 项目 _数据库 不存在: {project_root}", file=sys.stderr)
        return 2

    only_set = set(args.only.split(",")) if args.only else None

    chapters = []
    if args.cluster:
        chapters = get_cluster_chapter_range(project_root, args.cluster)
        if not chapters:
            print(f"[FATAL] cluster {args.cluster} 未找到 chapter_range", file=sys.stderr)
            return 2
        print(f"[cluster {args.cluster}] 展开 {len(chapters)} 章: {chapters}")
    elif args.ch:
        chapters = [args.ch]
    else:
        print(f"[FATAL] 必须指定 --ch 或 --cluster", file=sys.stderr)
        return 2

    all_results = {}
    fail_count = 0
    for ch in chapters:
        r = run_evaluators_for_chapter(str(project_root), ch, only_set)
        all_results[ch] = r
        fail_count += sum(1 for v in r.values() if not v["ok"])

    total_runs = sum(len(r) for r in all_results.values())
    success_runs = total_runs - fail_count
    print(f"\n[完成] 总跑 {total_runs}, 成功 {success_runs}, 失败 {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
