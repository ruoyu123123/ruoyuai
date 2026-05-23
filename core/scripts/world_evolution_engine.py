"""world_evolution_engine.py — 鬼谷八荒式世界自转引擎（v20.1 W3 新增）

核心理念：
- 世界状态.json 是世界活体快照（factions/NPC threads/emergent opportunities/consequences）
- 涟漪规则.json 定「事件 → 世界变化」的因果映射
- 每章 save-state 自动 tick：NPC threads 推进、超时 thread 触发完成事件、time roll
- 用户选定走向卡 → apply_minor_event：匹配 ripple_rule → 数值变化 + 新 thread spawn
- 大事件触发 → apply_fate_event：与 fate_engine 协同，触发更大涟漪

四个操作：
1. tick <ch>                      - 每章自动推进世界一格（auto_tick）
2. apply_minor_event <ch> --event <匹配字符串>  - 用户选定走向卡后触发
3. apply_fate_event <ch> --event <ME_id>        - fate_engine update 后触发
4. spawn_emergent <ch>            - 检查 emergent_opportunities 是否到期可用
5. dashboard                      - 世界全景

ripple 操作支持：
- target = "factions_state.X.Y"     + delta/set    -> 数值增减或设值
- target = "active_npc_threads"     + add_thread    -> 追加新 NPC 行动
- target = "active_npc_threads"     + evaluate_completion=true -> 评估超期 thread
- target = "emergent_opportunities" + spawn         -> 生成新机缘
- target = "consequence_tracker"    + add           -> 追加因果记录
- target = "current_world_time.ch"  + set_to_current_ch=true -> roll 时间

用法：
    python world_evolution_engine.py <project> tick <ch>
    python world_evolution_engine.py <project> apply_minor_event <ch> --event "B_鼻子先觉|铁锈味重锤埋设"
    python world_evolution_engine.py <project> apply_fate_event <ch> --event ME_003
    python world_evolution_engine.py <project> spawn_emergent <ch>
    python world_evolution_engine.py <project> dashboard

退出码: 0 成功 / 1 无规则匹配 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# ---------- IO ----------

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_world(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "世界状态.json", None)


def load_rules(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "涟漪规则.json", None)


def save_world(project_root: Path, world: dict):
    save_json(project_root / "_数据库" / "世界状态.json", world)


# ---------- ripple ops ----------

def _resolve_path(world: dict, dotted: str):
    """world 内点号路径解析，返回 (parent_dict, last_key)。不存在路径返回 (None, None)。"""
    parts = dotted.split(".")
    cur = world
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return (None, None)
        cur = cur[p]
    return (cur, parts[-1]) if isinstance(cur, dict) else (None, None)


def _apply_ripple(world: dict, ripple: dict, ch: int, applied_log: list) -> bool:
    """单条 ripple 落地。返回是否成功应用。"""
    target = ripple.get("target")
    if not target:
        return False
    reason = ripple.get("reason", "")

    # ---- 数值 delta（factions_state.X.Y） ----
    if "delta" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "delta", "result": "skip_path_missing"})
            return False
        old = parent.get(key, 0)
        if not isinstance(old, (int, float)):
            applied_log.append({"target": target, "op": "delta", "result": "skip_not_numeric"})
            return False
        delta = ripple["delta"]
        new = max(0, min(100, old + delta))  # 数值钳制 0-100
        parent[key] = new
        applied_log.append({"target": target, "op": "delta", "old": old, "new": new, "delta": delta, "reason": reason})
        return True

    # ---- set 值 ----
    if "set" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "set", "result": "skip_path_missing"})
            return False
        old = parent.get(key)
        parent[key] = ripple["set"]
        applied_log.append({"target": target, "op": "set", "old": old, "new": ripple["set"], "reason": reason})
        return True

    # ---- set_to_current_ch ----
    if ripple.get("set_to_current_ch"):
        parent, key = _resolve_path(world, target)
        if parent is None:
            return False
        parent[key] = ch
        applied_log.append({"target": target, "op": "set_ch", "new": ch})
        return True

    # ---- add_thread (active_npc_threads) ----
    if "add_thread" in ripple:
        threads = world.setdefault("active_npc_threads", [])
        td = ripple["add_thread"]
        # 自动分配 thread_id
        existing_ids = [t.get("thread_id", "") for t in threads]
        nums = []
        for tid in existing_ids:
            m = re.match(r"NT_(\d+)", tid)
            if m:
                nums.append(int(m.group(1)))
        next_num = (max(nums) + 1) if nums else 1
        new_thread = {
            "thread_id": f"NT_{next_num:03d}",
            "npc_id": td.get("npc_id", "?"),
            "current_action": td.get("action", ""),
            "since_ch": ch,
            "expected_complete_ch": td.get("expected_complete_ch"),
            "visible_to_protagonist": td.get("visible_to_protagonist", False),
            "outcome_if_complete": td.get("outcome_if_complete", ""),
            "_priority": td.get("_priority", 5),
            "_spawned_by_ripple": True,
        }
        threads.append(new_thread)
        applied_log.append({"target": "active_npc_threads", "op": "add_thread", "thread_id": new_thread["thread_id"], "npc": new_thread["npc_id"]})
        return True

    # ---- evaluate_completion ----
    if ripple.get("evaluate_completion"):
        threads = world.get("active_npc_threads", [])
        completed_log = world.setdefault("world_ticks_log", [])
        completed_threads = []
        remaining = []
        for t in threads:
            ec = t.get("expected_complete_ch")
            if ec is not None and isinstance(ec, int) and ch >= ec:
                completed_threads.append(t)
                # 自动 spawn 一条 consequence
                consequence_tracker = world.setdefault("consequence_tracker", {})
                key = f"ch{ch}_thread_complete_{t.get('thread_id','?')}"
                consequence_tracker[key] = {
                    "trigger": f"NT thread {t.get('thread_id')} 到期完成",
                    "npc": t.get("npc_id"),
                    "outcome": t.get("outcome_if_complete", ""),
                    "world_changes": [t.get("outcome_if_complete", "")],
                }
            else:
                remaining.append(t)
        world["active_npc_threads"] = remaining
        if completed_threads:
            applied_log.append({
                "target": "active_npc_threads",
                "op": "evaluate_completion",
                "completed_count": len(completed_threads),
                "completed_thread_ids": [t.get("thread_id") for t in completed_threads],
            })
        return True

    # ---- spawn (emergent_opportunities) ----
    if "spawn" in ripple and target == "emergent_opportunities":
        opps = world.setdefault("emergent_opportunities", [])
        sp = ripple["spawn"]
        existing_ids = [o.get("id", "") for o in opps]
        nums = []
        for oid in existing_ids:
            m = re.match(r"EO_(\d+)", oid)
            if m:
                nums.append(int(m.group(1)))
        next_num = (max(nums) + 1) if nums else 1
        expires_in = sp.get("expires_chapters", 5)
        new_opp = {
            "id": f"EO_{next_num:03d}",
            "trigger_ch": ch + 1,  # 下一章可用
            "type": sp.get("type", "副线机缘"),
            "description": sp.get("description", ""),
            "consumed_by_writer": False,
            "expires_at_ch": ch + expires_in,
            "_spawned_by_ripple": True,
        }
        opps.append(new_opp)
        applied_log.append({"target": "emergent_opportunities", "op": "spawn", "id": new_opp["id"]})
        return True

    # ---- add (consequence_tracker) ----
    if "add" in ripple and target == "consequence_tracker":
        consequences = world.setdefault("consequence_tracker", {})
        ad = ripple["add"]
        key = f"ch{ch}_{ad.get('event', 'consequence')[:30]}"
        consequences[key] = {
            "trigger": ad.get("event", ""),
            "world_changes": ad.get("world_changes", []),
            "added_at_ch": ch,
        }
        applied_log.append({"target": "consequence_tracker", "op": "add", "key": key})
        return True

    applied_log.append({"target": target, "op": "unknown", "ripple": ripple})
    return False


def _match_rule(rule: dict, trigger_type: str, trigger_value: str) -> bool:
    """rule 是否匹配本次触发。trigger_match 用 | 分隔多个候选关键词。"""
    if rule.get("trigger_type") != trigger_type:
        return False
    match_pattern = rule.get("trigger_match", "")
    if not match_pattern:
        return False
    if trigger_type == "auto_tick":
        return match_pattern == "every_chapter"
    candidates = [c.strip() for c in match_pattern.split("|") if c.strip()]
    return any(c in trigger_value or trigger_value in c for c in candidates)


def _apply_rules(world: dict, rules_json: dict, trigger_type: str, trigger_value: str, ch: int) -> dict:
    """匹配 + 应用所有命中规则。"""
    rules = rules_json.get("ripple_rules", [])
    applied_log: list = []
    matched_rules = []
    for rule in rules:
        if not _match_rule(rule, trigger_type, trigger_value):
            continue
        matched_rules.append(rule.get("id"))
        for ripple in rule.get("ripples", []):
            _apply_ripple(world, ripple, ch, applied_log)
    return {"matched_rules": matched_rules, "applied_log": applied_log}


# ---------- 公共操作 ----------

def tick(project_root: Path, ch: int) -> dict:
    """每章 auto_tick：触发 RR_AUTO_TICK 涟漪 + 推进 NPC threads + roll time。"""
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}
    if rules is None:
        return {"error": "涟漪规则.json 不存在"}

    # 1. roll current_world_time.ch
    cwt = world.setdefault("current_world_time", {})
    cwt["ch"] = ch

    # 2. 跑 auto_tick 规则
    result = _apply_rules(world, rules, "auto_tick", "every_chapter", ch)

    # 3. 追加 world_ticks_log
    log = world.setdefault("world_ticks_log", [])
    if not any(e.get("ch") == ch for e in log):
        log.append({
            "ch": ch,
            "tick_summary": f"auto_tick: {len(result['matched_rules'])} 规则触发, {len(result['applied_log'])} 涟漪落地",
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "tick",
        "matched_rules": result["matched_rules"],
        "applied_count": len(result["applied_log"]),
        "active_threads": len(world.get("active_npc_threads", [])),
        "active_opps": sum(1 for o in world.get("emergent_opportunities", []) if not o.get("consumed_by_writer")),
    }


def apply_minor_event(project_root: Path, ch: int, event_value: str) -> dict:
    """用户选定走向卡 → 触发 minor_event 类型涟漪。"""
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None or rules is None:
        return {"error": "世界状态.json/涟漪规则.json 不存在"}

    result = _apply_rules(world, rules, "minor_event", event_value, ch)

    # 写入 world_ticks_log
    log = world.setdefault("world_ticks_log", [])
    log.append({
        "ch": ch,
        "tick_summary": f"minor_event: {event_value} → {len(result['matched_rules'])} 规则",
        "trigger_type": "minor_event",
        "trigger_value": event_value,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "apply_minor_event",
        "trigger": event_value,
        "matched_rules": result["matched_rules"],
        "applied_log": result["applied_log"],
    }


def apply_fate_event(project_root: Path, ch: int, event_id: str) -> dict:
    """fate_engine update 完成后 → 触发 fate_event 类型涟漪。"""
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None or rules is None:
        return {"error": "世界状态.json/涟漪规则.json 不存在"}

    result = _apply_rules(world, rules, "fate_event", event_id, ch)

    log = world.setdefault("world_ticks_log", [])
    log.append({
        "ch": ch,
        "tick_summary": f"fate_event: {event_id} → {len(result['matched_rules'])} 规则",
        "trigger_type": "fate_event",
        "trigger_value": event_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "apply_fate_event",
        "event_id": event_id,
        "matched_rules": result["matched_rules"],
        "applied_log": result["applied_log"],
    }


def spawn_emergent(project_root: Path, ch: int) -> dict:
    """检查 emergent_opportunities：到期未消费 → 标 expired，可用未消费 → 列出。"""
    world = load_world(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}

    opps = world.get("emergent_opportunities", [])
    available = []
    expired_ids = []
    for o in opps:
        if o.get("consumed_by_writer"):
            continue
        trigger_ch = o.get("trigger_ch", 0)
        expires_at = o.get("expires_at_ch", 9999)
        if ch > expires_at:
            o["status"] = "expired"
            expired_ids.append(o.get("id"))
        elif ch >= trigger_ch:
            available.append({
                "id": o.get("id"),
                "type": o.get("type"),
                "description": o.get("description"),
                "expires_at_ch": expires_at,
                "remaining_chapters": expires_at - ch,
            })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "spawn_emergent",
        "available": available,
        "expired_this_ch": expired_ids,
    }


def dashboard(project_root: Path) -> dict:
    """世界全景。"""
    world = load_world(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}

    factions = world.get("factions_state", {})
    factions_summary = {}
    for name, f in factions.items():
        factions_summary[name] = {
            "power": f.get("power"),
            "stability": f.get("stability"),
            "wealth": f.get("wealth"),
            "focus": (f.get("current_focus", "") or "")[:40],
        }

    threads = world.get("active_npc_threads", [])
    threads_summary = [
        {
            "id": t.get("thread_id"),
            "npc": t.get("npc_id"),
            "action": (t.get("current_action") or "")[:40],
            "since_ch": t.get("since_ch"),
            "expected_complete_ch": t.get("expected_complete_ch"),
            "priority": t.get("_priority"),
        }
        for t in sorted(threads, key=lambda x: -(x.get("_priority", 0)))[:10]
    ]

    opps = world.get("emergent_opportunities", [])
    opps_active = [o for o in opps if not o.get("consumed_by_writer") and o.get("status") != "expired"]

    return {
        "current_ch": world.get("current_world_time", {}).get("ch"),
        "factions": factions_summary,
        "active_npc_threads_top10": threads_summary,
        "active_threads_total": len(threads),
        "active_emergent_opportunities": len(opps_active),
        "consequence_records": len(world.get("consequence_tracker", {})),
        "world_ticks_logged": len(world.get("world_ticks_log", [])),
    }


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["tick", "apply_minor_event", "apply_fate_event", "spawn_emergent", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    ap.add_argument("--event", type=str, default=None, help="apply_minor_event/apply_fate_event 触发值")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print(f"[ERROR] {args.action} 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    if args.action == "tick":
        r = tick(project_root, args.ch)
    elif args.action == "spawn_emergent":
        r = spawn_emergent(project_root, args.ch)
    elif args.action == "apply_minor_event":
        if not args.event:
            print("[ERROR] apply_minor_event 需要 --event <匹配字符串>", file=sys.stderr)
            sys.exit(2)
        r = apply_minor_event(project_root, args.ch, args.event)
    elif args.action == "apply_fate_event":
        if not args.event:
            print("[ERROR] apply_fate_event 需要 --event <ME_id>", file=sys.stderr)
            sys.exit(2)
        r = apply_fate_event(project_root, args.ch, args.event)
    else:
        sys.exit(2)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    if "error" in r:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
