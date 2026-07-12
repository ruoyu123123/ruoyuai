#!/usr/bin/env python3
"""把一个 cluster 的世界状态增量幂等应用到世界状态与大势卡。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup
import cluster_state_delta
import world_evolution_engine as engine
from atomic_json import atomic_write_json


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 必须是 object")
    return data


def _digest(delta: dict) -> str:
    payload = json.dumps(delta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _major_events(major: dict) -> list[dict]:
    events = major.get("major_events")
    if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
        raise ValueError("大势卡.major_events 必须是 object array")
    return events


def _event_id(event: dict) -> str:
    value = str(event.get("id") or "").strip()
    if not value:
        raise ValueError("大势卡 major_event 缺少 id")
    return value


def _event_map(major: dict) -> dict[str, dict]:
    mapping = {}
    for event in _major_events(major):
        event_id = _event_id(event)
        if event_id in mapping:
            raise ValueError(f"大势卡 ME id 重复: {event_id}")
        mapping[event_id] = event
    return mapping


def _validate_world(world: dict) -> None:
    required = {
        "current_world_time": dict,
        "active_npc_threads": list,
        "emergent_opportunities": list,
        "consequence_tracker": dict,
    }
    for key, expected in required.items():
        if not isinstance(world.get(key), expected):
            raise ValueError(f"世界状态.{key} 必须是 {expected.__name__}")
    for thread in world["active_npc_threads"]:
        if not isinstance(thread, dict) or not thread.get("thread_id"):
            raise ValueError("active_npc_threads 条目必须包含 thread_id")
    for opportunity in world["emergent_opportunities"]:
        if not isinstance(opportunity, dict) or not opportunity.get("id"):
            raise ValueError("emergent_opportunities 条目必须包含 id")


def _preflight(world: dict, rules: dict, major: dict, cluster_id: str,
               delta: dict) -> dict:
    _validate_world(world)
    engine.require_cluster_id(cluster_id)
    engine._canonical_rules(rules)
    auto_rules = engine.matching_rule_ids(rules, "auto_tick", "every_cluster")
    if not auto_rules:
        raise ValueError("涟漪规则缺少 every_cluster auto_tick")

    event_map = _event_map(major)
    event_ids = []
    for entry in delta["fate_events_triggered"]:
        event_id = str(entry.get("event_id") or "").strip()
        if event_id not in event_map:
            raise ValueError(f"state delta 引用了未知 ME: {event_id}")
        if not engine.matching_rule_ids(rules, "fate_event", event_id):
            raise ValueError(f"ME {event_id} 没有 fate_event 涟漪规则")
        event_ids.append(event_id)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("fate_events_triggered.event_id 不可重复")

    consumption = delta["world_state_consumption"]
    requested_opportunities = set(consumption["emergent_opportunities_consumed"])
    requested_threads = set(consumption["thread_responded"])
    opportunities = {row["id"]: row for row in world["emergent_opportunities"]}
    threads = {row["thread_id"]: row for row in world["active_npc_threads"]}
    missing_opportunities = sorted(requested_opportunities - opportunities.keys())
    missing_threads = sorted(requested_threads - threads.keys())
    if missing_opportunities:
        raise ValueError(f"state delta 引用了未知机缘: {missing_opportunities}")
    if missing_threads:
        raise ValueError(f"state delta 引用了未知 NPC thread: {missing_threads}")

    for opportunity_id in requested_opportunities:
        opportunity = opportunities[opportunity_id]
        consumed_at = opportunity.get("consumed_at_cluster")
        if consumed_at and consumed_at != cluster_id:
            raise ValueError(f"机缘 {opportunity_id} 已由 {consumed_at} 消费")
        active, _remaining = engine.opportunity_window(opportunity, cluster_id)
        if not active:
            raise ValueError(f"机缘 {opportunity_id} 不在 {cluster_id} 的有效窗口")

    for thread_id in requested_threads:
        thread = threads[thread_id]
        expected = thread.get("expected_responses")
        responded = thread.get("responded_by_cluster")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ValueError(f"thread {thread_id}.expected_responses 必须是正整数")
        if not isinstance(responded, list) or not all(isinstance(item, str) for item in responded):
            raise ValueError(f"thread {thread_id}.responded_by_cluster 必须是 string array")

    return {
        "auto_rules": auto_rules,
        "event_map": event_map,
        "event_ids": event_ids,
        "opportunity_ids": requested_opportunities,
        "thread_ids": requested_threads,
    }


def _consume_opportunities(world: dict, cluster_id: str,
                           opportunity_ids: set[str]) -> dict:
    consumed, skipped = [], []
    for opportunity in world["emergent_opportunities"]:
        if opportunity["id"] not in opportunity_ids:
            continue
        if opportunity.get("consumed_at_cluster") == cluster_id:
            skipped.append(opportunity["id"])
            continue
        opportunity["consumed_by_writer"] = True
        opportunity["consumed_at_cluster"] = cluster_id
        opportunity["status"] = "consumed"
        consumed.append(opportunity["id"])
    return {"consumed": consumed, "skipped_idempotent": skipped}


def _respond_threads(world: dict, cluster_id: str, thread_ids: set[str]) -> dict:
    responded, skipped, completed = [], [], []
    remaining = []
    for thread in world["active_npc_threads"]:
        thread_id = thread["thread_id"]
        if thread_id not in thread_ids:
            remaining.append(thread)
            continue
        clusters = thread["responded_by_cluster"]
        if cluster_id in clusters:
            skipped.append(thread_id)
        else:
            clusters.append(cluster_id)
            responded.append(thread_id)
        thread["responded_count"] = len(set(clusters))
        if thread["responded_count"] >= thread["expected_responses"]:
            completed.append(thread_id)
            engine._record_thread_completion(world, thread, cluster_id, "thread_responded")
        else:
            remaining.append(thread)
    world["active_npc_threads"] = remaining
    return {
        "responded": responded,
        "skipped_idempotent": skipped,
        "completed": completed,
        "responded_count": len(responded),
    }


def _apply_fate_events(world: dict, rules: dict, event_map: dict[str, dict],
                       cluster_id: str, entries: list[dict]) -> list[dict]:
    ledger = world.setdefault("applied_fate_events", {})
    if not isinstance(ledger, dict):
        raise ValueError("世界状态.applied_fate_events 必须是 object")
    results = []
    for entry in entries:
        event_id = entry["event_id"]
        previous = ledger.get(event_id)
        if previous:
            if previous.get("cluster_id") != cluster_id:
                raise ValueError(f"ME {event_id} 已由 {previous.get('cluster_id')} 应用")
            results.append({
                "event_id": event_id,
                "skipped_idempotent": True,
                "matched_rules": previous.get("matched_rules", []),
            })
            continue
        result = engine._apply_rules(world, rules, "fate_event", event_id, cluster_id)
        ledger[event_id] = {
            "cluster_id": cluster_id,
            "matched_rules": result["matched_rules"],
        }
        event = event_map[event_id]
        completed_at = event.get("completed_at_cluster")
        if completed_at and completed_at != cluster_id:
            raise ValueError(f"ME {event_id} 已在 {completed_at} 完成")
        event["status"] = "completed"
        event["completed_at_cluster"] = cluster_id
        event["completion_evidence"] = str(entry.get("evidence") or "")
        results.append({
            "event_id": event_id,
            "matched_rules": result["matched_rules"],
            "applied_count": len(result["applied_log"]),
        })
    return results


def _expire_opportunities(world: dict, cluster_id: str) -> list[str]:
    expired = []
    current = cluster_lookup.cluster_num(cluster_id)
    for opportunity in world["emergent_opportunities"]:
        if opportunity.get("consumed_by_writer"):
            continue
        _active, remaining = engine.opportunity_window(opportunity, cluster_id)
        if remaining is not None and remaining < 0:
            opportunity["status"] = "expired"
            expired.append(opportunity["id"])
        elif current >= cluster_lookup.cluster_num(opportunity["trigger_cluster"]):
            opportunity["status"] = "available"
    return expired


def _tick(world: dict, rules: dict, cluster_id: str) -> dict:
    applied_ticks = world.setdefault("applied_cluster_ticks", [])
    if not isinstance(applied_ticks, list):
        raise ValueError("世界状态.applied_cluster_ticks 必须是 array")
    if cluster_id in applied_ticks:
        return {"cluster_id": cluster_id, "skipped_idempotent": True}
    result = engine._apply_rules(world, rules, "auto_tick", "every_cluster", cluster_id)
    expired = _expire_opportunities(world, cluster_id)
    applied_ticks.append(cluster_id)
    world["current_world_time"]["cluster"] = cluster_id
    world.setdefault("world_ticks_log", []).append({
        "cluster_id": cluster_id,
        "trigger_type": "auto_tick",
        "matched_rules": result["matched_rules"],
    })
    return {
        "cluster_id": cluster_id,
        "matched_rules": result["matched_rules"],
        "applied_count": len(result["applied_log"]),
        "expired_opportunities": expired,
    }


def _repair_major_projection(major: dict, cluster_id: str,
                             entries: list[dict]) -> bool:
    changed = False
    events = _event_map(major)
    for entry in entries:
        event_id = entry["event_id"]
        event = events[event_id]
        completed_at = event.get("completed_at_cluster")
        if completed_at and completed_at != cluster_id:
            raise ValueError(f"ME {event_id} 已在 {completed_at} 完成")
        expected = str(entry.get("evidence") or "")
        if (event.get("status") != "completed"
                or completed_at != cluster_id
                or event.get("completion_evidence", "") != expected):
            event["status"] = "completed"
            event["completed_at_cluster"] = cluster_id
            event["completion_evidence"] = expected
            changed = True
    return changed


def apply_cluster(project: Path, cluster_id: str, delta: dict) -> dict:
    cluster_id = engine.require_cluster_id(cluster_id)
    if delta.get("cluster_id") != cluster_id:
        raise ValueError("state delta.cluster_id 与命令参数不一致")
    db = Path(project) / "_数据库"
    world_path = db / "世界状态.json"
    rules_path = db / "涟漪规则.json"
    major_path = db / "大势卡.json"
    world = _read_json(world_path)
    rules = _read_json(rules_path)
    major = _read_json(major_path)
    digest = _digest(delta)

    ledger = world.setdefault("applied_cluster_state_deltas", {})
    if not isinstance(ledger, dict):
        raise ValueError("世界状态.applied_cluster_state_deltas 必须是 object")
    previous = ledger.get(cluster_id)
    if previous:
        if previous.get("sha256") != digest:
            raise ValueError(f"{cluster_id} 已应用不同 state delta，禁止覆盖")
        if _repair_major_projection(major, cluster_id, delta["fate_events_triggered"]):
            atomic_write_json(major_path, major)
        return {**previous["report"], "skipped_idempotent": True}

    context = _preflight(world, rules, major, cluster_id, delta)
    consumption = {
        "opportunities": _consume_opportunities(
            world, cluster_id, context["opportunity_ids"]),
        "threads": _respond_threads(world, cluster_id, context["thread_ids"]),
    }
    fate_events = _apply_fate_events(
        world, rules, context["event_map"], cluster_id,
        delta["fate_events_triggered"],
    )
    tick = _tick(world, rules, cluster_id)
    report = {
        "cluster_id": cluster_id,
        "world_state_consumption": consumption,
        "fate_events": fate_events,
        "tick": tick,
    }
    ledger[cluster_id] = {"sha256": digest, "report": report}

    atomic_write_json(world_path, world)
    atomic_write_json(major_path, major)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="应用 cluster 世界演化状态")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    project = Path(args.project)
    try:
        cluster_id = engine.require_cluster_id(args.cluster)
        _path, delta = cluster_state_delta.load_delta(project, cluster_id)
        report = apply_cluster(project, cluster_id, delta)
        out = project / "_数据库" / ".world_evolution" / f"{cluster_id}_apply.json"
        atomic_write_json(out, {
            **report,
            "applied_at": datetime.now().isoformat(timespec="seconds"),
        })
        print(f"[OK] {out}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
