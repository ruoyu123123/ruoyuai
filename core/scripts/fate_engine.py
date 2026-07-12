"""按故事块评估大势事件的可推进性与窗口漂移。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cluster_lookup
import state_cli_guard


def _load_fate(project_root: Path) -> dict:
    path = Path(project_root) / "_数据库" / "大势卡.json"
    if not path.is_file():
        raise ValueError("大势卡.json 不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("大势卡.json 顶层必须是 object")
    return data


def _cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise ValueError(f"非法 cluster_id: {value}")
    return cluster_id


def _events(fate: dict) -> list[dict]:
    """返回 canonical ME 池，并拒绝缺字段、重复 id 与旧结构。"""
    if not isinstance(fate, dict):
        raise ValueError("大势卡必须是 object")
    events = fate.get("major_events")
    if not isinstance(events, list):
        raise ValueError("大势卡.major_events 必须是 object array")
    if not all(isinstance(event, dict) for event in events):
        raise ValueError("大势卡.major_events 只能包含 object")

    seen: set[str] = set()
    for event in events:
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("大势卡 major_event 缺少 id")
        if event_id in seen:
            raise ValueError(f"大势卡 ME id 重复: {event_id}")
        seen.add(event_id)
        if event.get("status") not in {"pending", "completed"}:
            raise ValueError(f"ME {event_id}.status 必须是 pending 或 completed")
    return events


def _event_map(fate: dict) -> dict[str, dict]:
    return {event["id"]: event for event in _events(fate)}


def _prerequisites(event: dict, event_map: dict[str, dict]) -> list[str]:
    value = event.get("prerequisites", [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"ME {event['id']}.prerequisites 必须是非空字符串数组")
    missing = sorted(set(value) - event_map.keys())
    if missing:
        raise ValueError(f"ME {event['id']} 引用了未知 prerequisites: {missing}")
    return value


def _window(event: dict, event_map: dict[str, dict]) -> tuple[str, int] | None:
    value = event.get("expected_window_after")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"ME {event['id']}.expected_window_after 必须是 object 或 null")
    anchor = value.get("event")
    maximum = value.get("max_clusters")
    if not isinstance(anchor, str) or not anchor:
        raise ValueError(f"ME {event['id']}.expected_window_after.event 缺失")
    if anchor not in event_map:
        raise ValueError(f"ME {event['id']} 的窗口引用未知事件: {anchor}")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValueError(f"ME {event['id']}.expected_window_after.max_clusters 必须是非负整数")
    return anchor, maximum


def is_event_unlockable(event: dict, fate: dict) -> bool:
    """所有前置 ME 已完成时，当前 ME 可进入推进候选。"""
    event_map = _event_map(fate)
    return all(event_map[event_id].get("status") == "completed"
               for event_id in _prerequisites(event, event_map))


def _timing(event: dict, event_map: dict[str, dict], current_cluster: str) -> dict | None:
    window = _window(event, event_map)
    if window is None:
        return None
    anchor_id, maximum = window
    anchor = event_map[anchor_id]
    if anchor.get("status") != "completed":
        return None
    completed_at = _cluster_id(anchor.get("completed_at_cluster"))
    gap = cluster_lookup.cluster_num(current_cluster) - cluster_lookup.cluster_num(completed_at)
    if gap < 0:
        raise ValueError(
            f"当前 {current_cluster} 早于窗口锚点 {anchor_id} 的完成位置 {completed_at}"
        )
    return {
        "anchor_id": anchor_id,
        "anchor_completed_at_cluster": completed_at,
        "gap_clusters": gap,
        "max_clusters": maximum,
        "overdue_by_clusters": max(0, gap - maximum),
    }


def _overdue_record(event: dict, timing: dict, current_cluster: str) -> dict:
    return {
        "event_id": event["id"],
        "title": str(event.get("title") or ""),
        "prereq": timing["anchor_id"],
        "prereq_completed_at_cluster": timing["anchor_completed_at_cluster"],
        "current_cluster": current_cluster,
        "gap_clusters": timing["gap_clusters"],
        "max_clusters": timing["max_clusters"],
        "overdue_by_clusters": timing["overdue_by_clusters"],
    }


def evaluate(project_root: Path, cluster_id: str) -> dict:
    """返回当前故事块可推进的 ME，保持只读顾问语义。"""
    current_cluster = _cluster_id(cluster_id)
    fate = _load_fate(Path(project_root))
    events = _events(fate)
    event_map = {event["id"]: event for event in events}
    active: list[dict] = []
    overdue: list[dict] = []

    for event in events:
        _prerequisites(event, event_map)
        if event["status"] != "pending" or not is_event_unlockable(event, fate):
            continue
        timing = _timing(event, event_map, current_cluster)
        window = _window(event, event_map)
        if window is not None and timing is None:
            continue

        priority = 5
        if timing:
            if timing["overdue_by_clusters"] > 0:
                priority = 10
                overdue.append(_overdue_record(event, timing, current_cluster))
            elif timing["max_clusters"] == 0 or timing["gap_clusters"] >= timing["max_clusters"] * 0.7:
                priority = 8

        active.append({
            "id": event["id"],
            "title": str(event.get("title") or ""),
            "stage": event.get("stage"),
            "trigger_when": event.get("trigger_when"),
            "physical_evidence": event.get("physical_evidence", []),
            "priority": priority,
            "downstream_unlocks": event.get("downstream_unlocks", []),
        })

    active.sort(key=lambda item: (-item["priority"], item["id"]))
    return {
        "cluster_id": current_cluster,
        "active_fate_events": active,
        "overdue_events": overdue,
        "total_pending": sum(1 for event in events if event["status"] == "pending"),
        "total_completed": sum(1 for event in events if event["status"] == "completed"),
    }


def drift(project_root: Path, cluster_id: str) -> dict:
    """检测当前故事块已超过软窗口的未完成 ME。"""
    current_cluster = _cluster_id(cluster_id)
    fate = _load_fate(Path(project_root))
    events = _events(fate)
    event_map = {event["id"]: event for event in events}
    overdue: list[dict] = []

    for event in events:
        _prerequisites(event, event_map)
        if event["status"] != "pending":
            continue
        timing = _timing(event, event_map, current_cluster)
        if timing and timing["overdue_by_clusters"] > 0:
            overdue.append(_overdue_record(event, timing, current_cluster))

    overdue.sort(key=lambda item: (-item["overdue_by_clusters"], item["event_id"]))
    return {
        "cluster_id": current_cluster,
        "overdue_count": len(overdue),
        "overdue_events": overdue,
    }


def dashboard(project_root: Path) -> dict:
    """汇总大势事件数量、状态和阶段分布。"""
    fate = _load_fate(Path(project_root))
    events = _events(fate)
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for event in events:
        status = event["status"]
        stage = str(event.get("stage") or "unspecified")
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {
        "total_events": len(events),
        "by_status": by_status,
        "by_stage": by_stage,
        "final_image": str(fate.get("story_destiny", {}).get("final_image") or "")[:100],
    }


def main() -> int:
    state_cli_guard.require_internal("fate_engine.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("action", choices=["evaluate", "drift", "dashboard"])
    parser.add_argument("cluster", nargs="?")
    args = parser.parse_args()

    try:
        project_root = Path(args.project)
        if args.action == "dashboard":
            result = dashboard(project_root)
            exit_code = 0
        else:
            if args.cluster is None:
                raise ValueError(f"{args.action} 需要 <cluster_id>")
            result = evaluate(project_root, args.cluster) if args.action == "evaluate" else drift(project_root, args.cluster)
            exit_code = 1 if args.action == "drift" and result["overdue_count"] else 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
