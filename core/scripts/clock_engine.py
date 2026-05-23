"""clock_engine.py — 显式 Clock 进度系统（v21 R1.1 新增）

借鉴 Citizen Sleeper 的 Clocks 机制 + CK3 的 Schemes 进度。**所有"逐渐变化的事"显式建模为 clock**。

设计目标：
- 解决「节奏失控」：clock 满格 → 强制触发，避免主代理凭感觉拖延
- 解决「伏笔忘埋」：foreshadowing due_by 自动注册为 clock，到期前 N 章告警
- 解决「反派步步紧逼无量化」：反派耐心/势力 schemes 都建模为 tick

五个操作：
1. tick <ch>                              - 按 chapter_end 触发所有相关 clock +tick_per_event
2. tick_event <ch> <event_type> <value>   - 按特定事件触发匹配 clock（如 minor_event）
3. list <ch>                              - 列出当前活跃 clock + 距离满格距离
4. spawn <ch> <id> <label> ... (json)     - 动态创建 clock
5. dashboard                              - 全 clock 全景

退出码: 0 健康 / 1 有 clock 满格触发 / 2 致命
"""

from __future__ import annotations

import argparse
import fnmatch
import json
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


def load_clocks(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "时钟表.json", None)


def save_clocks(project_root: Path, data: dict):
    save_json(project_root / "_数据库" / "时钟表.json", data)


def _match_tick_on(tick_on_list: list[str], event_type: str, value: str = "") -> bool:
    """tick_on 匹配。支持 chapter_end / event_type:<glob_pattern>。"""
    for to in tick_on_list:
        if to == event_type:
            return True
        if ":" in to:
            t, pat = to.split(":", 1)
            if t == event_type and fnmatch.fnmatch(value, pat):
                return True
    return False


def _do_tick(clocks_data: dict, ch: int, event_type: str, event_value: str = "") -> dict:
    """对所有 status=active 且匹配 tick_on 的 clock 执行 tick。返回触发的 clock 列表。"""
    triggered = []
    ticked = []
    for c in clocks_data.get("clocks", []):
        if c.get("status") != "active":
            continue
        if not _match_tick_on(c.get("tick_on", []), event_type, event_value):
            continue
        delta = c.get("tick_per_event", 1)
        old = c.get("ticks", 0)
        new = min(c.get("max", 99), old + delta)
        c["ticks"] = new
        ticked.append({
            "clock_id": c.get("clock_id"),
            "label": c.get("label"),
            "old": old,
            "new": new,
            "max": c.get("max"),
            "remaining": c.get("max", 99) - new,
        })
        if new >= c.get("max", 99):
            c["status"] = "triggered"
            c["triggered_at_ch"] = ch
            triggered.append({
                "clock_id": c.get("clock_id"),
                "label": c.get("label"),
                "trigger_on_max": c.get("trigger_on_max"),
                "category": c.get("category"),
            })
    return {"ticked": ticked, "triggered": triggered}


def tick_chapter(project_root: Path, ch: int) -> dict:
    """每章末统一 tick（chapter_end 触发的所有 clock）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    r = _do_tick(data, ch, "chapter_end")
    save_clocks(project_root, data)
    return {"ch": ch, "event": "chapter_end", **r}


def tick_event(project_root: Path, ch: int, event_type: str, event_value: str) -> dict:
    """事件触发（minor_event:<match> / fate_event:<id> / faction_drop:<faction>:<dim>）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    r = _do_tick(data, ch, event_type, event_value)
    save_clocks(project_root, data)
    return {"ch": ch, "event": f"{event_type}:{event_value}", **r}


def list_active(project_root: Path, ch: int) -> dict:
    """列出当前活跃 clock + 距离满格 + 紧迫度（remaining ≤ 2 → urgent）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    out = []
    for c in data.get("clocks", []):
        if c.get("status") != "active":
            continue
        max_v = c.get("max", 99)
        ticks = c.get("ticks", 0)
        remaining = max_v - ticks
        urgency = "urgent" if remaining <= 2 else ("approaching" if remaining <= max_v * 0.3 else "normal")
        out.append({
            "clock_id": c.get("clock_id"),
            "label": c.get("label"),
            "category": c.get("category"),
            "ticks": ticks,
            "max": max_v,
            "remaining": remaining,
            "urgency": urgency,
            "trigger_on_max": c.get("trigger_on_max"),
            "visible_to_protagonist": c.get("visible_to_protagonist", False),
            "_reason": c.get("_reason", ""),
        })
    out.sort(key=lambda x: (x["remaining"], -x["ticks"]))
    return {"ch": ch, "active_clocks": out, "total_active": len(out)}


def spawn(project_root: Path, ch: int, clock_def: dict) -> dict:
    """动态创建 clock。clock_def 必含 label/max/tick_on/trigger_on_max。"""
    data = load_clocks(project_root)
    if data is None:
        # 初始化文件
        data = {"_schema": "clocks_v21_explicit_progression", "clocks": []}
    clocks = data.setdefault("clocks", [])
    # 自动 ID
    nums = []
    for c in clocks:
        cid = c.get("clock_id", "")
        if cid.startswith("CK_"):
            try:
                nums.append(int(cid[3:]))
            except ValueError:
                pass
    next_num = (max(nums) + 1) if nums else 1
    new = {
        "clock_id": f"CK_{next_num:03d}",
        "label": clock_def.get("label", "未命名"),
        "category": clock_def.get("category", "other"),
        "ticks": clock_def.get("ticks", 0),
        "max": clock_def.get("max", 5),
        "tick_on": clock_def.get("tick_on", ["chapter_end"]),
        "tick_per_event": clock_def.get("tick_per_event", 1),
        "trigger_on_max": clock_def.get("trigger_on_max", ""),
        "visible_to_protagonist": clock_def.get("visible_to_protagonist", False),
        "visible_to_writer": clock_def.get("visible_to_writer", True),
        "since_ch": ch,
        "spawned_by": clock_def.get("spawned_by", "manual"),
        "status": "active",
        "_reason": clock_def.get("_reason", ""),
    }
    clocks.append(new)
    save_clocks(project_root, data)
    return {"created": new["clock_id"], "label": new["label"]}


def dashboard(project_root: Path) -> dict:
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    by_status = {}
    by_category = {}
    by_urgency = {"urgent": 0, "approaching": 0, "normal": 0, "triggered": 0}
    for c in data.get("clocks", []):
        s = c.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        cat = c.get("category", "?")
        by_category[cat] = by_category.get(cat, 0) + 1
        if s == "triggered":
            by_urgency["triggered"] += 1
        elif s == "active":
            ticks = c.get("ticks", 0)
            max_v = c.get("max", 99)
            remaining = max_v - ticks
            if remaining <= 2:
                by_urgency["urgent"] += 1
            elif remaining <= max_v * 0.3:
                by_urgency["approaching"] += 1
            else:
                by_urgency["normal"] += 1
    return {
        "total_clocks": len(data.get("clocks", [])),
        "by_status": by_status,
        "by_category": by_category,
        "by_urgency": by_urgency,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["tick", "tick_event", "list", "spawn", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    ap.add_argument("--event-type", type=str, default=None)
    ap.add_argument("--event-value", type=str, default="")
    ap.add_argument("--json", type=str, default=None, help="spawn 用的 clock_def JSON")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print(f"[ERROR] {args.action} 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    if args.action == "tick":
        r = tick_chapter(project_root, args.ch)
    elif args.action == "tick_event":
        if not args.event_type:
            print("[ERROR] tick_event 需要 --event-type", file=sys.stderr)
            sys.exit(2)
        r = tick_event(project_root, args.ch, args.event_type, args.event_value or "")
    elif args.action == "list":
        r = list_active(project_root, args.ch)
    elif args.action == "spawn":
        if not args.json:
            print("[ERROR] spawn 需要 --json '<def>'", file=sys.stderr)
            sys.exit(2)
        clock_def = json.loads(args.json)
        r = spawn(project_root, args.ch, clock_def)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    triggered = r.get("triggered") or []
    if triggered:
        print(f"\n[CLOCK TRIGGERED] {len(triggered)} clocks 满格:", file=sys.stderr)
        for t in triggered:
            print(f"  {t['clock_id']} {t['label']} → {t['trigger_on_max']}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
