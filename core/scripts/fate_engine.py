"""fate_engine.py — 鬼谷八荒式大势引擎（v20 涌现叙事 F3 新增）

核心理念：
- 大势卡定大事件，**不定章号**
- 每章触发哪个事件，由 fate_engine 根据 prerequisites + window 涌现决定
- writer 不受 cluster_blueprint 死约束，按 active_fate_events 推进

四个操作：
1. evaluate <ch>   - 评估本章应该推进哪些 events（返回 active 列表）
2. update <ch>     - 章节完成后根据 _changes.json.fate_events_triggered 更新大势状态
3. drift <ch>      - 检测漂移（超期未触发）
4. dashboard       - 输出大势全景

用法：
    python fate_engine.py <project> evaluate <ch>
    python fate_engine.py <project> update <ch>
    python fate_engine.py <project> drift <ch>
    python fate_engine.py <project> dashboard

退出码: 0 健康 / 1 有漂移 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：裸写 → 原子写（atomic_write_json 内部已 mkdir + fsync）
    atomic_json.atomic_write_json(p, data)


def _events(fate: dict) -> list:
    """取 major_events 并滤掉非 dict 元素（2026-05-30 北极星复审：大势卡 ME 池可能混入字符串/None 占位，
    否则 e.get()/e['id'] 抛 AttributeError，且崩在 build_manifest:620 的 try 里被静默吞成 mode:error，
    丢失全部大势 fate 牵引——北极星「大势已定」软牵引静默失效）。"""
    return [e for e in (fate.get("major_events") or []) if isinstance(e, dict)]


def is_event_unlockable(event: dict, fate: dict) -> bool:
    """事件 prerequisites 是否全部 completed → 可以激活。"""
    prereqs = event.get("prerequisites", [])
    if not prereqs:
        return True
    completed_ids = {e["id"] for e in _events(fate) if e.get("status") == "completed"}
    return all(pid in completed_ids for pid in prereqs)


def evaluate(project_root: Path, ch: int) -> dict:
    """返回本章应推进的 active events 清单。"""
    fate_path = project_root / "_数据库" / "大势卡.json"
    fate = load_json(fate_path, None)
    if fate is None:
        return {"error": "大势卡.json 不存在"}

    events = _events(fate)
    active = []
    overdue = []
    for e in events:
        if e.get("status") != "scheduled":
            continue
        if not is_event_unlockable(e, fate):
            continue
        # 该事件可激活 - 计算"是否本章合适"
        window = e.get("expected_window_after")
        priority = 5  # 默认中
        if window and isinstance(window, dict):
            prereq_event_id = window.get("event")
            max_ch = window.get("max_chapters", 999)
            # 找 prerequisite 完成的章号
            prereq_completed_ch = None
            for pe in events:
                if pe.get("id") == prereq_event_id and pe.get("status") == "completed":
                    prereq_completed_ch = pe.get("completed_at_ch")
                    break
            if prereq_completed_ch is not None:
                gap = ch - prereq_completed_ch
                if gap > max_ch:
                    overdue.append({**e, "_gap": gap, "_max_ch": max_ch})
                    priority = 10  # 超期
                elif gap >= max_ch * 0.7:
                    priority = 8  # 接近窗口末
                else:
                    priority = 5
        active.append({
            "id": e["id"],
            "title": e["title"],
            "stage": e.get("stage"),
            "trigger_when": e.get("trigger_when"),
            "physical_evidence": e.get("physical_evidence"),
            "priority": priority,
            "downstream_unlocks": e.get("downstream_unlocks", []),
        })

    active.sort(key=lambda x: -x["priority"])
    return {
        "ch": ch,
        "active_fate_events": active,
        "overdue_events": overdue,
        "total_scheduled": sum(1 for e in events if e.get("status") == "scheduled"),
        "total_completed": sum(1 for e in events if e.get("status") == "completed"),
    }


def update(project_root: Path, ch: int) -> dict:
    """根据 _changes.json.fate_events_triggered 更新大势卡。"""
    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    changes = load_json(changes_path, {})
    triggered = changes.get("factual", {}).get("fate_events_triggered", [])
    if not triggered:
        return {"ch": ch, "updated": 0, "reason": "本章未触发 fate_events"}

    fate_path = project_root / "_数据库" / "大势卡.json"
    fate = load_json(fate_path, {"major_events": []})

    updated_ids = []
    for trig in triggered:
        eid = trig.get("event_id")
        if not eid:
            continue
        for e in _events(fate):
            if e.get("id") == eid and e.get("status") != "completed":
                e["status"] = "completed"
                e["completed_at_ch"] = ch
                e["completion_evidence"] = trig.get("evidence", "")[:120]
                updated_ids.append(eid)
                break

    save_json(fate_path, fate)
    return {"ch": ch, "updated": len(updated_ids), "event_ids": updated_ids}


def drift(project_root: Path, ch: int) -> dict:
    """检测漂移（超期未触发事件）。"""
    fate_path = project_root / "_数据库" / "大势卡.json"
    fate = load_json(fate_path, None)
    if fate is None:
        return {"error": "大势卡.json 不存在"}
    overdue = []
    for e in _events(fate):
        if e.get("status") != "scheduled":
            continue
        window = e.get("expected_window_after")
        if not window or not isinstance(window, dict):
            continue
        prereq_event_id = window.get("event")
        max_ch = window.get("max_chapters", 999)
        prereq_ch = None
        for pe in _events(fate):
            if pe.get("id") == prereq_event_id and pe.get("status") == "completed":
                prereq_ch = pe.get("completed_at_ch")
                break
        if prereq_ch is None:
            continue
        gap = ch - prereq_ch
        if gap > max_ch:
            overdue.append({
                "event_id": e["id"],
                "title": e["title"],
                "prereq": prereq_event_id,
                "prereq_completed_at_ch": prereq_ch,
                "current_ch": ch,
                "gap": gap,
                "max_chapters": max_ch,
                "overdue_by": gap - max_ch,
            })
    return {"ch": ch, "overdue_count": len(overdue), "overdue_events": overdue}


def dashboard(project_root: Path) -> dict:
    """大势全景。"""
    fate_path = project_root / "_数据库" / "大势卡.json"
    fate = load_json(fate_path, {"major_events": []})
    events = _events(fate)
    by_status = {}
    by_stage = {}
    for e in events:
        s = e.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        stg = e.get("stage", "?")
        by_stage[stg] = by_stage.get(stg, 0) + 1
    return {
        "total_events": len(events),
        "by_status": by_status,
        "by_stage": by_stage,
        "final_image": fate.get("story_destiny", {}).get("final_image", "")[:100],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["evaluate", "update", "drift", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print(f"[ERROR] {args.action} 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    if args.action == "evaluate":
        r = evaluate(project_root, args.ch)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.action == "update":
        r = update(project_root, args.ch)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.action == "drift":
        r = drift(project_root, args.ch)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(1 if r.get("overdue_count", 0) > 0 else 0)


if __name__ == "__main__":
    main()
