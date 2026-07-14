#!/usr/bin/env python3
"""按 cluster state delta 更新并评估群像 heart events。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import atomic_json
import cluster_lookup
import protagonist_lookup
import state_cli_guard


class RelationshipContractError(ValueError):
    """人物、关系、群像或 state delta 不符合唯一合同。"""


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RelationshipContractError(f"必需文件不存在: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationshipContractError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RelationshipContractError(f"JSON 顶层必须是 object: {path}")
    return value


def _heart_event_index(ensemble: dict) -> dict[str, tuple[str, dict]]:
    characters = ensemble.get("characters")
    if not isinstance(characters, dict):
        raise RelationshipContractError("群像档.characters 必须是 object")
    index: dict[str, tuple[str, dict]] = {}
    for npc, record in characters.items():
        if not isinstance(npc, str) or not npc or not isinstance(record, dict):
            raise RelationshipContractError("群像档.characters 条目无效")
        events = record.get("heart_events")
        if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
            raise RelationshipContractError(f"群像档.characters.{npc}.heart_events 必须是 object array")
        for event in events:
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id.startswith("HE_"):
                raise RelationshipContractError(f"群像档 {npc} heart event id 无效")
            if event_id in index:
                raise RelationshipContractError(f"群像档 heart event id 重复: {event_id}")
            if not isinstance(event.get("consumed"), bool):
                raise RelationshipContractError(f"群像档 {event_id}.consumed 必须是 bool")
            if not isinstance(event.get("trigger_at"), dict):
                raise RelationshipContractError(f"群像档 {event_id}.trigger_at 必须是 object")
            index[event_id] = (npc, event)
    return index


def mark_consumed_from_delta(ensemble: dict, delta: dict,
                             cluster_id: str) -> list[dict]:
    """按专用 state delta 幂等标记正文已经揭示的 heart events。"""
    revealed = delta.get("heart_events_revealed")
    if not isinstance(revealed, list) or not all(isinstance(item, dict) for item in revealed):
        raise RelationshipContractError("state delta.heart_events_revealed 必须是 object array")
    index = _heart_event_index(ensemble)
    ids: set[str] = set()
    for position, item in enumerate(revealed):
        event_id = item.get("event_id")
        evidence = item.get("evidence")
        if not isinstance(event_id, str) or not event_id.startswith("HE_"):
            raise RelationshipContractError(f"heart_events_revealed[{position}].event_id 无效")
        if not isinstance(evidence, str) or not evidence.strip():
            raise RelationshipContractError(f"heart_events_revealed[{position}].evidence 为空")
        if event_id in ids:
            raise RelationshipContractError(f"heart_events_revealed 重复 event_id: {event_id}")
        if event_id not in index:
            raise RelationshipContractError(f"heart_events_revealed 引用未知 event_id: {event_id}")
        ids.add(event_id)
    newly_marked = []
    for event_id in sorted(ids):
        npc, event = index[event_id]
        if event["consumed"]:
            continue
        event["consumed"] = True
        event["consumed_at_cluster"] = cluster_id
        newly_marked.append({
            "npc": npc,
            "event_id": event_id,
            "consumed_at_cluster": cluster_id,
        })
    return newly_marked


def get_relationship_to(relationships: list[dict], from_char: str,
                        to_char: str) -> dict | None:
    matches = [row for row in relationships
               if row.get("from") == from_char and row.get("to") == to_char]
    if len(matches) > 1:
        raise RelationshipContractError(f"关系重复: {from_char}->{to_char}")
    return matches[0] if matches else None


def trigger_satisfied(trigger_at: dict, relationship: dict) -> bool:
    """全部已定义的数值门槛满足时返回 true。"""
    if not trigger_at:
        return False
    for dimension, threshold in trigger_at.items():
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise RelationshipContractError(f"heart event 阈值非数值: {dimension}")
        current = relationship.get(dimension)
        if current is None:
            return False
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            raise RelationshipContractError(f"关系维度非数值: {dimension}")
        if current < threshold:
            return False
    return True


def get_protagonist(cards: dict) -> str:
    characters = cards.get("characters")
    if not isinstance(characters, list) or not all(isinstance(item, dict) for item in characters):
        raise RelationshipContractError("人物卡.characters 必须是 object array")
    # 关系图谱中心 = 主位主角（protagonist_cards 信号强度排序首位）。多主角书（双女主等）
    # 合法，取主位做 heart-event 参照；0 个主角位才是契约错误（硬契约保留）。
    ranked = protagonist_lookup.protagonist_cards(characters)
    names = [c.get("name") for c in ranked
             if isinstance(c.get("name"), str) and c.get("name")]
    if not names:
        raise RelationshipContractError("人物卡缺主角位（role 须以「主角」开头·多主角合法取主位）")
    return names[0]


def _relationships(document: dict) -> list[dict]:
    rows = document.get("relationships")
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise RelationshipContractError("关系.relationships 必须是 object array")
    for position, row in enumerate(rows):
        if not isinstance(row.get("from"), str) or not isinstance(row.get("to"), str):
            raise RelationshipContractError(f"关系.relationships[{position}] 缺 from/to")
    return rows


def evaluate(project_root: Path, cluster: str) -> dict:
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise RelationshipContractError(f"非法 cluster: {cluster}")
    db = project_root / "_数据库"
    ensemble_path = db / "群像档.json"
    ensemble = _read_object(ensemble_path)
    relationships = _relationships(_read_object(db / "关系.json"))
    protagonist = get_protagonist(_read_object(db / "人物卡.json"))
    delta_path = db / ".wal" / f"{cluster_id}_state_delta.json"
    if not delta_path.is_file():
        raise RelationshipContractError(f"required state delta 不存在: {delta_path}")
    delta = _read_object(delta_path)
    if delta.get("cluster_id") != cluster_id:
        raise RelationshipContractError("state delta.cluster_id 与命令参数不一致")

    newly_consumed = mark_consumed_from_delta(ensemble, delta, cluster_id)
    event_index = _heart_event_index(ensemble)
    pending_reveals = []
    for event_id, (npc, event) in event_index.items():
        if event["consumed"]:
            continue
        relationship = get_relationship_to(relationships, protagonist, npc)
        if relationship is None or not trigger_satisfied(event["trigger_at"], relationship):
            continue
        pending_reveals.append({
            "npc": npc,
            "event_id": event_id,
            "tier_label": event.get("tier_label"),
            "reveal": event.get("reveal"),
            "physical_evidence": event.get("physical_evidence", []),
            "current_relationship": relationship,
            "trigger_at": event["trigger_at"],
        })

    atomic_json.atomic_write_json(ensemble_path, ensemble)
    pending_path = db / ".ensemble_pending_reveals.json"
    atomic_json.atomic_write_json(pending_path, {
        "cluster_id": cluster_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pending_reveals": pending_reveals,
    })
    return {
        "cluster_id": cluster_id,
        "newly_consumed_count": len(newly_consumed),
        "newly_consumed": newly_consumed,
        "pending_reveals_count": len(pending_reveals),
        "pending_reveals": pending_reveals,
        "persisted_to": str(pending_path),
    }


def main() -> int:
    state_cli_guard.require_internal("relationship_evaluator.py")
    parser = argparse.ArgumentParser(description="cluster heart event 状态评估")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        result = evaluate(Path(args.project), args.cluster)
    except (OSError, RelationshipContractError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["pending_reveals_count"] else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
