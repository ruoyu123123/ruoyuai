"""fate_dice.py — 命运事件抽签器（v21 R1.4 新增）

借鉴 Wildermyth：emergent_opportunities 触发时不让 AI 自由编故事，从 事件池.json
按当前 manifest 上下文（章号/scene_type/pov/world_state）过滤后**加权随机抽 1**，
AI 只负责把抽到的 narrative_seed 写好。

效果：剧情走向更有"被命运撞上"的奇迹感，不是 AI 凭感觉编。

两个操作：
1. draw <ch> [--scene-type X] [--pov Y]   - 按当前上下文抽 1 个事件
2. dashboard                              - 事件池全景

退出码：0 抽到 / 1 池为空或全被过滤 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import operator
import random
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_dotted(obj: dict, path: str):
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


def _check_world_required(world_state: dict, requirements: dict) -> bool:
    """world_state_required 形如 {'factions_state.南海资本.power': '>= 80'}"""
    if not requirements:
        return True
    for path, expr in requirements.items():
        val = _resolve_dotted(world_state, path)
        if val is None:
            return False
        # 解析比较表达式
        for op_str in [">=", "<=", "==", "!=", ">", "<"]:
            if expr.startswith(op_str):
                target_str = expr[len(op_str):].strip()
                try:
                    target = float(target_str)
                    if not _OPS[op_str](float(val), target):
                        return False
                except ValueError:
                    if not _OPS[op_str](val, target_str):
                        return False
                break
        else:
            # 无运算符 → 等值
            if val != expr:
                return False
    return True


def _filter_event(event: dict, ch: int, scene_type: str, pov: str, world_state: dict, recent_drawn: set) -> bool:
    cf = event.get("context_filter", {})
    if event.get("event_id") in recent_drawn:
        return False
    # 2026-05-29 修：原 `cf.get("min_ch") and ...` 在 min_ch/max_ch==0 时被 falsy
    # 短路跳过守卫。改为 `is not None` 判断，让 0 也能正常参与边界过滤。
    if cf.get("min_ch") is not None and ch < cf["min_ch"]:
        return False
    if cf.get("max_ch") is not None and ch > cf["max_ch"]:
        return False
    scenes = cf.get("scene_types", [])
    if scenes and scene_type and scene_type not in scenes:
        return False
    req_pov = cf.get("required_pov")
    if req_pov and pov and req_pov != pov:
        return False
    if not _check_world_required(world_state, cf.get("world_state_required", {})):
        return False
    return True


def draw(project_root: Path, ch: int, scene_type: str = "", pov: str = "") -> dict:
    pool_path = project_root / "_数据库" / "事件池.json"
    pool = load_json(pool_path, None)
    if pool is None:
        return {"error": "事件池.json 不存在"}

    # world state for filter
    world_path = project_root / "_数据库" / "世界状态.json"
    world = load_json(world_path, {})

    # 最近 N 章已抽过的事件（避免重复）
    drawn_log = pool.get("drawn_events_log", [])
    recent = {d["event_id"] for d in drawn_log if ch - d.get("ch", 0) <= 3}

    candidates = [
        e for e in pool.get("events", [])
        if _filter_event(e, ch, scene_type, pov, world, recent)
    ]
    if not candidates:
        return {
            "ch": ch,
            "drawn": None,
            "reason": "全部事件被过滤 / 池子为空",
            "filter_ctx": {"scene_type": scene_type, "pov": pov, "ch": ch},
        }

    weights = [e.get("context_filter", {}).get("weight", 1) for e in candidates]
    # 2026-05-29 修：weights 全 0（或负）时 sum<=0，random.choices 抛 ValueError 崩溃。
    # 退回均匀抽样（weights=None），保证可用性而非崩溃。
    if sum(weights) <= 0:
        weights = None
    chosen = random.choices(candidates, weights=weights, k=1)[0]

    # 写入 drawn log
    drawn_log.append({
        "ch": ch,
        "event_id": chosen["event_id"],
        "label": chosen.get("label"),
        "drawn_at": datetime.now().isoformat(timespec="seconds"),
        "ctx": {"scene_type": scene_type, "pov": pov},
    })
    pool["drawn_events_log"] = drawn_log[-30:]  # 保留近 30 次
    save_json(pool_path, pool)

    return {
        "ch": ch,
        "drawn": {
            "event_id": chosen["event_id"],
            "label": chosen.get("label"),
            "category": chosen.get("category"),
            "narrative_seed": chosen.get("narrative_seed"),
            "physical_evidence": chosen.get("physical_evidence", []),
        },
        "candidates_count": len(candidates),
        "_note": "writer 应把 narrative_seed 写成本章一个具体场景，physical_evidence 必须出现在正文",
    }


def dashboard(project_root: Path) -> dict:
    pool_path = project_root / "_数据库" / "事件池.json"
    pool = load_json(pool_path, {"events": []})
    events = pool.get("events", [])
    by_cat = {}
    for e in events:
        c = e.get("category", "?")
        by_cat[c] = by_cat.get(c, 0) + 1
    return {
        "total_events": len(events),
        "by_category": by_cat,
        "drawn_log_count": len(pool.get("drawn_events_log", [])),
        "recent_drawn": [d.get("event_id") for d in (pool.get("drawn_events_log") or [])[-5:]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["draw", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    ap.add_argument("--scene-type", type=str, default="")
    ap.add_argument("--pov", type=str, default="")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print("[ERROR] draw 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    r = draw(project_root, args.ch, args.scene_type, args.pov)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("drawn"):
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
