#!/usr/bin/env python3
"""校验并应用一个 cluster 的客观状态增量。"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import cluster_lookup
from atomic_json import atomic_write_json

SCHEMA_PATH = (Path(__file__).resolve().parents[1] / "claude-home" / "schemas"
               / "cluster_state_delta_schema.json")


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"必需状态库不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 损坏: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 顶层必须是 object")
    return data


def validate_schema(value, schema: dict, where: str = "$") -> None:
    """执行本仓库 artifact schema 使用的 JSON Schema 子集。"""
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        checks = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "null": lambda x: x is None,
        }
        if not any(checks[t](value) for t in types):
            raise ValueError(f"{where} 类型必须是 {'/'.join(types)}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{where} 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where} 不在允许值中")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{where} 长度不足")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{where} 长度超过 {schema['maxLength']}")
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            raise ValueError(f"{where} 不匹配 {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{where} 小于最小值 {schema['minimum']}")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{where}[{index}]")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ValueError(f"{where} 不允许重复项")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ValueError(f"{where} 缺少字段: {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"{where} 含未知字段: {extra}")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], f"{where}.{key}")


def load_delta(project: Path, cluster: str) -> tuple[Path, dict]:
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise ValueError(f"非法 cluster: {cluster}")
    path = project / "_数据库" / ".wal" / f"{cluster_id}_state_delta.json"
    data = _json(path)
    schema = _json(SCHEMA_PATH)
    validate_schema(data, schema)
    if data["cluster_id"] != cluster_id:
        raise ValueError("state delta.cluster_id 与命令参数不一致")
    return path, data


def _object_array(document: dict, key: str, label: str) -> list[dict]:
    rows = document.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}.{key} 必须是 object array")
    return rows


def _unique_index(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for index, row in enumerate(rows):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label}[{index}] 缺少 {key}")
        if value in result:
            raise ValueError(f"{label} {key} 重复: {value}")
        result[value] = row
    return result


def _preflight(project: Path, data: dict) -> dict:
    db = project / "_数据库"
    docs = {
        "timeline": _json(db / "时间线.json"),
        "map": _json(db / "地图.json"),
        "hubs": _json(db / "枢纽场景.json"),
        "major": _json(db / "大势卡.json"),
        "world": _json(db / "世界状态.json"),
        "ensemble": _json(db / "群像档.json"),
    }
    if not isinstance(docs["timeline"].get("current_time"), dict):
        raise ValueError("时间线.current_time 必须是 object")
    _object_array(docs["timeline"], "time_log", "时间线")
    locations = _unique_index(_object_array(docs["map"], "locations", "地图"), "id", "地图.locations")
    hubs = _unique_index(_object_array(docs["hubs"], "hubs", "枢纽场景"), "hub_id", "枢纽场景.hubs")
    _object_array(docs["hubs"], "cluster_hub_log", "枢纽场景")
    events = _unique_index(_object_array(docs["major"], "major_events", "大势卡"), "id", "大势卡.major_events")
    opportunities = _unique_index(
        _object_array(docs["world"], "emergent_opportunities", "世界状态"), "id",
        "世界状态.emergent_opportunities")
    threads = _unique_index(
        _object_array(docs["world"], "active_npc_threads", "世界状态"), "thread_id",
        "世界状态.active_npc_threads")
    characters = docs["ensemble"].get("characters")
    if not isinstance(characters, dict):
        raise ValueError("群像档.characters 必须是 object")
    heart_events: dict[str, dict] = {}
    for npc, record in characters.items():
        if not isinstance(record, dict):
            raise ValueError(f"群像档.characters.{npc} 必须是 object")
        for event in _object_array(record, "heart_events", f"群像档.characters.{npc}"):
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError(f"群像档.characters.{npc}.heart_events 缺 event_id")
            if event_id in heart_events:
                raise ValueError(f"群像档 heart event id 重复: {event_id}")
            heart_events[event_id] = event

    references = {
        "location_id": ({row["location_id"] for row in data["location_changes"]}, locations),
        "hub_id": ({row["hub_id"] for row in data["hub_usage"]}, hubs),
        "ME event_id": ({row["event_id"] for row in data["fate_events_triggered"]}, events),
        "opportunity_id": (set(data["world_state_consumption"]["emergent_opportunities_consumed"]), opportunities),
        "thread_id": (set(data["world_state_consumption"]["thread_responded"]), threads),
        "HE event_id": ({row["event_id"] for row in data["heart_events_revealed"]}, heart_events),
    }
    for label, (requested, known) in references.items():
        unknown = sorted(requested - set(known))
        if unknown:
            raise ValueError(f"未知 {label}: {unknown}")
    return docs


def _build_documents(data: dict, docs: dict) -> dict:
    cluster_id = data["cluster_id"]
    timeline = copy.deepcopy(docs["timeline"])
    time_log = timeline["time_log"]
    time_log[:] = [row for row in time_log if row.get("cluster_id") != cluster_id]
    if data["time_advance"]:
        time_log.append({"cluster_id": cluster_id, **data["time_advance"]})
        timeline["current_time"]["cluster"] = cluster_id
        if data["time_advance"].get("period"):
            timeline["current_time"]["period"] = data["time_advance"]["period"]

    map_data = copy.deepcopy(docs["map"])
    locations = {row["id"]: row for row in map_data["locations"]}
    for change in data["location_changes"]:
        locations[change["location_id"]]["status"] = change["new_status"]
        locations[change["location_id"]]["updated_at_cluster"] = cluster_id

    hubs = copy.deepcopy(docs["hubs"])
    log = hubs["cluster_hub_log"]
    log[:] = [row for row in log if row.get("cluster_id") != cluster_id]
    log.extend({"cluster_id": cluster_id, **row} for row in data["hub_usage"])
    return {"时间线": timeline, "地图": map_data, "枢纽场景": hubs}


def apply_delta(project: Path, data: dict) -> dict:
    """所有引用先验证，验证通过后才构造并写入直接运行态。"""
    schema = _json(SCHEMA_PATH)
    validate_schema(data, schema)
    docs = _preflight(project, data)
    updated = _build_documents(data, docs)
    db = project / "_数据库"
    paths = {name: db / f"{name}.json" for name in updated}
    for name, document in updated.items():
        atomic_write_json(paths[name], document)
    receipt = {
        "cluster_id": data["cluster_id"],
        "source": f"{data['cluster_id']}_state_delta.json",
        "validated_domains": ["time", "location", "hub", "fate", "world", "heart_event"],
        "applied_domains": ["时间线", "地图", "枢纽场景"],
        "required_consumers": {
            "fate+world": "world_evolution_apply_cluster.py",
            "heart_event": "relationship_evaluator.py",
        },
    }
    atomic_write_json(db / ".wal" / f"{data['cluster_id']}_state_delta_applied.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        path, data = load_delta(Path(args.project), args.cluster)
        if args.validate_only:
            _preflight(Path(args.project), data)
            print(f"[OK] {path}")
        else:
            receipt = apply_delta(Path(args.project), data)
            print(f"[OK] state delta applied: {receipt['cluster_id']}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
