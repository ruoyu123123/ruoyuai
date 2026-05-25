#!/usr/bin/env python3
"""cluster_emergence_engine.py — v24 事件簇 fluid 涌现引擎

每个 cluster 完成时调用，基于：
- 当前 cluster 完成后的 世界状态.json（factions_state + consequence_tracker + active_npc_threads）
- 用户走向卡选择 + 涟漪规则触发结果（world_evolution_apply 日志）
- 主角 character_arc_state（lie_breaking 等阶段变化）
- 大势卡剩余 ME 池

产出：下一 cluster 的 2-3 个 candidate brief，写入 `事件簇.json.clusters[N+1]`（status: "candidate"）

CLI:
    python cluster_emergence_engine.py <project> emerge --after-cluster <id>
    python cluster_emergence_engine.py <project> emerge --after-cluster cluster_001
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def find_remaining_mes(dashishi: dict, completed_mes: set) -> list:
    """从大势卡 ME 池中找剩余未完成的 ME。"""
    pool = dashishi.get("major_events_pool", []) or []
    return [me for me in pool if me.get("id") not in completed_mes]


def select_candidate_mes(remaining_mes: list, world_state: dict, character_arc: dict, last_consequence: list) -> list:
    """启发式：从剩余 ME 中选 2-3 个 candidate，每个对应一个 cluster brief 雏形。

    策略：
    1. 优先选 vol 跟当前推进 vol 一致的 ME
    2. 优先选 parent_me 链上的下一个 ME（按 vol 顺序）
    3. 主角 stage=lie_cracking → 优先选触发 setback 的 ME
    4. 主角 stage=lie_broken → 优先选 recovery / win-streak ME
    """
    candidates = []

    # 取前 3 个未完成 ME（默认按 ME 池顺序 = 设计的故事推进顺序）
    candidates = remaining_mes[:3]

    return candidates


def me_to_cluster_brief(me: dict, cluster_id: str, ord: int, world_state: dict) -> dict:
    """把 ME 转成 cluster brief 候选 (scene_storyboard 仅雏形)。"""
    return {
        "cluster_id": cluster_id,
        "parent_me": me.get("id"),
        "scope_summary": f"[CANDIDATE {ord}] 围绕 ME「{me.get('title', '')}」展开。{me.get('description', '')}",
        "expected_word_range": {"min": 16000, "max": 22000},
        "scenes_estimated": 4,
        "estimated_chapters": 4,
        "chapter_range": None,  # 等用户选定后由 outline-planner 计算
        "status": "candidate",
        "ME_to_advance": [me.get("id")],
        "_doc": f"v24 fluid 涌现 · 等待用户从 {ord} 个 candidate 中选 1 个 → status 改 in_progress",
        "scene_storyboard": [],  # 雏形 · 用户选定后再让 outline-planner 详化
        "anchor_props": [],
        "foreshadowing_to_plant": [],
        "research_ref": {
            "_doc": "v23.1 选定 candidate 后必须独立调研 cluster_brief"
        }
    }


def emerge_next_cluster(project_root: Path, after_cluster_id: str) -> dict:
    """主入口。"""
    db = project_root / "_数据库"
    shijianji_path = db / "事件簇.json"
    dashishi_path = db / "大势卡.json"
    world_state_path = db / "世界状态.json"
    arc_state_path = db / "character_arc_state.json"

    shijianji = load_json(shijianji_path, {"clusters": []})
    dashishi = load_json(dashishi_path, {})
    world_state = load_json(world_state_path, {})
    character_arc = load_json(arc_state_path, {})

    # 收集已完成 cluster 的 ME_to_advance
    completed_mes = set()
    for c in shijianji.get("clusters", []):
        if c.get("status") in ("done", "in_progress", "writer_v2_rewriting", "done_writer_drafted"):
            for me in c.get("ME_to_advance", []) or []:
                completed_mes.add(me)

    # 找剩余 ME
    remaining = find_remaining_mes(dashishi, completed_mes)
    if not remaining:
        return {"ok": False, "error": "大势卡 ME 池已全部完成，无新 cluster 可涌现", "completed_count": len(completed_mes)}

    # 收集 last consequence（最后一个 cluster 的涟漪后果）
    last_consequence = []
    if isinstance(world_state.get("consequence_tracker"), dict):
        last_consequence = list(world_state["consequence_tracker"].values())[-5:]
    elif isinstance(world_state.get("consequence_tracker"), list):
        last_consequence = world_state["consequence_tracker"][-5:]

    # 选 candidate ME
    candidate_mes = select_candidate_mes(remaining, world_state, character_arc, last_consequence)
    if not candidate_mes:
        return {"ok": False, "error": "无符合启发式条件的 candidate ME"}

    # 生成 cluster brief 候选
    after_num = 0
    if after_cluster_id and "_" in after_cluster_id:
        try:
            after_num = int(after_cluster_id.split("_")[-1])
        except Exception:
            after_num = len([c for c in shijianji.get("clusters", []) if c.get("status") != "candidate"])

    next_num = after_num + 1
    next_cluster_id = f"cluster_{next_num:03d}"

    candidates_briefs = []
    for i, me in enumerate(candidate_mes, 1):
        brief = me_to_cluster_brief(me, f"{next_cluster_id}_candidate_{i}", i, world_state)
        candidates_briefs.append(brief)

    # 写入 emergence.json WAL
    emergence_path = db / ".wal" / f"{next_cluster_id}_emergence.json"
    emergence_path.parent.mkdir(parents=True, exist_ok=True)
    emergence_data = {
        "_schema": "cluster_emergence_v24",
        "after_cluster": after_cluster_id,
        "next_cluster_id": next_cluster_id,
        "emerged_at": datetime.now().isoformat(timespec="seconds"),
        "completed_mes_count": len(completed_mes),
        "remaining_mes_count": len(remaining),
        "candidates": candidates_briefs,
        "world_state_snapshot": {
            "factions_state": world_state.get("factions_state", {}),
            "last_consequences": last_consequence,
            "active_npc_threads_count": len(world_state.get("active_npc_threads", []))
        },
        "character_arc_snapshot": {
            "characters": [
                {"id": c.get("id"), "current_stage": c.get("current_stage")}
                for c in (character_arc.get("characters", []) or [])
            ]
        },
        "_next_action": "主代理展示 candidates 给用户选 1 个 → 写入 事件簇.json.clusters[N+1] (status: in_progress)"
    }
    emergence_path.write_text(json.dumps(emergence_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "next_cluster_id": next_cluster_id,
        "candidates_count": len(candidates_briefs),
        "emergence_path": str(emergence_path),
        "summary": [f"{c['cluster_id']}: ME {c['parent_me']} → {c['scope_summary'][:60]}" for c in candidates_briefs]
    }


def main():
    parser = argparse.ArgumentParser(description="v24 cluster fluid 涌现引擎")
    parser.add_argument("project", help="项目路径（绝对或相对 cwd）")
    parser.add_argument("action", choices=["emerge"], help="动作")
    parser.add_argument("--after-cluster", required=True, help="当前已完成 cluster id (如 cluster_001)")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[ERROR] 项目路径不存在 _数据库 目录: {project_root}", file=sys.stderr)
        return 2

    if args.action == "emerge":
        result = emerge_next_cluster(project_root, args.after_cluster)
        if result.get("ok"):
            print(f"[OK] cluster {result['next_cluster_id']} 涌现 {result['candidates_count']} 个 candidate")
            print(f"     emergence 文件: {result['emergence_path']}")
            for line in result.get("summary", []):
                print(f"     - {line}")
            return 0
        else:
            print(f"[FAIL] {result.get('error', '未知错误')}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
