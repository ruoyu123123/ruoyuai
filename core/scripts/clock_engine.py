#!/usr/bin/env python3
"""按 cluster 事件推进剧情时钟，并提供写作前的活跃时钟快照。

时钟只响应两类显式信号：当前故事块结束 ``cluster_end``，或带类型和值的
叙事事件。所有读写都使用唯一 ``clocks[]`` 结构并记录 ``cluster_id``。

退出码：0 成功；1 有时钟满格；2 输入或状态契约损坏。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json
import state_cli_guard


SCHEMA_NAME = "cluster_clocks"
SCHEMA_VERSION = "1.0"
CLUSTER_RE = re.compile(r"^cluster_[0-9]{3,}$")
EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STATUSES = {"active", "triggered", "abandoned"}
PUBLIC_TOP_KEYS = {"schema_version", "consumption", "generated_at", "clocks"}
PUBLIC_CLOCK_KEYS = {
    "clock_id", "label", "category", "ticks", "max", "tick_on",
    "tick_per_event", "trigger_on_max", "visible_to_protagonist",
    "visible_to_writer", "is_surprise", "since_cluster", "spawned_by",
    "status", "triggered_at_cluster",
}
CLOCK_REQUIRED = PUBLIC_CLOCK_KEYS - {"triggered_at_cluster"}
SPAWN_REQUIRED = {
    "label", "category", "max", "tick_on", "tick_per_event",
    "trigger_on_max", "visible_to_protagonist", "visible_to_writer",
    "is_surprise", "spawned_by",
}


class ClockContractError(ValueError):
    """时钟文件、事件或调用参数不符合唯一契约。"""


def _require_cluster_id(value: str) -> str:
    if not isinstance(value, str) or not CLUSTER_RE.fullmatch(value):
        raise ClockContractError(f"非法 cluster_id: {value!r}")
    return value


def _read_json_object(path: Path) -> dict:
    if not path.is_file():
        raise ClockContractError(f"文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClockContractError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ClockContractError(f"JSON 顶层必须是 object: {path}")
    return data


def _validate_trigger(trigger: str) -> None:
    if trigger == "chapter_end":
        raise ClockContractError("tick_on 不接受 chapter_end，只能使用 cluster_end")
    if trigger == "cluster_end":
        return
    if not isinstance(trigger, str) or not trigger:
        raise ClockContractError("tick_on 条目必须是非空字符串")
    event_type = trigger.split(":", 1)[0]
    if not EVENT_RE.fullmatch(event_type):
        raise ClockContractError(f"非法事件触发器: {trigger!r}")


def _validate_clock(clock: dict, *, index: int) -> None:
    if not isinstance(clock, dict):
        raise ClockContractError(f"clocks[{index}] 必须是 object")
    public = {key for key in clock if not str(key).startswith("_")}
    missing = CLOCK_REQUIRED - set(clock)
    unknown = public - PUBLIC_CLOCK_KEYS
    if missing or unknown:
        raise ClockContractError(
            f"clocks[{index}] 字段错误: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if not isinstance(clock["clock_id"], str) or not clock["clock_id"]:
        raise ClockContractError(f"clocks[{index}].clock_id 必须是非空字符串")
    for field in ("label", "category", "trigger_on_max", "spawned_by"):
        if not isinstance(clock[field], str) or not clock[field].strip():
            raise ClockContractError(f"clocks[{index}].{field} 必须是非空字符串")
    if not isinstance(clock["ticks"], int) or isinstance(clock["ticks"], bool):
        raise ClockContractError(f"clocks[{index}].ticks 必须是整数")
    if not isinstance(clock["max"], int) or isinstance(clock["max"], bool) or clock["max"] <= 0:
        raise ClockContractError(f"clocks[{index}].max 必须是正整数")
    if not 0 <= clock["ticks"] <= clock["max"]:
        raise ClockContractError(f"clocks[{index}].ticks 必须位于 0..max")
    if (not isinstance(clock["tick_per_event"], int)
            or isinstance(clock["tick_per_event"], bool)
            or clock["tick_per_event"] <= 0):
        raise ClockContractError(f"clocks[{index}].tick_per_event 必须是正整数")
    triggers = clock["tick_on"]
    if not isinstance(triggers, list) or not triggers:
        raise ClockContractError(f"clocks[{index}].tick_on 必须是非空数组")
    for trigger in triggers:
        _validate_trigger(trigger)
    for field in ("visible_to_protagonist", "visible_to_writer", "is_surprise"):
        if not isinstance(clock[field], bool):
            raise ClockContractError(f"clocks[{index}].{field} 必须是 boolean")
    if clock["is_surprise"] and clock["visible_to_writer"]:
        raise ClockContractError(f"clocks[{index}] 暗线时钟不得向 writer 暴露")
    _require_cluster_id(clock["since_cluster"])
    if clock["status"] not in STATUSES:
        raise ClockContractError(f"clocks[{index}].status 非法: {clock['status']!r}")
    triggered_at = clock.get("triggered_at_cluster")
    if triggered_at is not None:
        _require_cluster_id(triggered_at)
    if clock["status"] == "triggered" and triggered_at is None:
        raise ClockContractError(f"clocks[{index}] 已触发但缺 triggered_at_cluster")


def validate_clocks(data: dict) -> dict:
    if data.get("_schema") != SCHEMA_NAME or data.get("schema_version") != SCHEMA_VERSION:
        raise ClockContractError(
            f"时钟表 schema 必须是 _schema={SCHEMA_NAME!r}, schema_version={SCHEMA_VERSION!r}"
        )
    unknown_top = {
        key for key in data
        if not str(key).startswith("_") and key not in PUBLIC_TOP_KEYS
    }
    if unknown_top:
        raise ClockContractError(f"时钟表未知顶层字段: {sorted(unknown_top)}")
    clocks = data.get("clocks")
    if not isinstance(clocks, list):
        raise ClockContractError("时钟表.clocks 必须是数组")
    seen: set[str] = set()
    for index, clock in enumerate(clocks):
        _validate_clock(clock, index=index)
        clock_id = clock["clock_id"]
        if clock_id in seen:
            raise ClockContractError(f"重复 clock_id: {clock_id}")
        seen.add(clock_id)
    return data


def load_clocks(project_root: Path) -> dict:
    path = Path(project_root) / "_数据库" / "时钟表.json"
    return validate_clocks(_read_json_object(path))


def save_clocks(project_root: Path, data: dict) -> None:
    validate_clocks(data)
    atomic_json.atomic_write_json(Path(project_root) / "_数据库" / "时钟表.json", data)


def _matches(trigger: str, event_type: str, event_value: str) -> bool:
    if ":" not in trigger:
        return trigger == event_type
    trigger_type, pattern = trigger.split(":", 1)
    return trigger_type == event_type and fnmatch.fnmatchcase(event_value, pattern)


def _validate_event(event_type: str, event_value: str) -> None:
    if event_type == "chapter_end":
        raise ClockContractError("事件类型不接受 chapter_end")
    if event_type != "cluster_end" and not EVENT_RE.fullmatch(event_type or ""):
        raise ClockContractError(f"非法 event_type: {event_type!r}")
    if not isinstance(event_value, str):
        raise ClockContractError("event_value 必须是字符串")
    if event_type == "cluster_end" and event_value:
        raise ClockContractError("cluster_end 不接受 event_value")


def tick_event(
    project_root: Path,
    cluster_id: str,
    event_type: str,
    event_value: str = "",
) -> dict:
    """按当前 cluster 的一个显式事件推进所有匹配时钟。"""
    cluster_id = _require_cluster_id(cluster_id)
    _validate_event(event_type, event_value)
    data = load_clocks(project_root)
    ticked: list[dict] = []
    triggered: list[dict] = []
    for clock in data["clocks"]:
        if clock["status"] != "active":
            continue
        if not any(_matches(trigger, event_type, event_value) for trigger in clock["tick_on"]):
            continue
        old = clock["ticks"]
        new = min(clock["max"], old + clock["tick_per_event"])
        clock["ticks"] = new
        ticked.append({
            "clock_id": clock["clock_id"],
            "label": clock["label"],
            "old": old,
            "new": new,
            "max": clock["max"],
            "remaining": clock["max"] - new,
        })
        if new >= clock["max"]:
            clock["status"] = "triggered"
            clock["triggered_at_cluster"] = cluster_id
            triggered.append({
                "clock_id": clock["clock_id"],
                "label": clock["label"],
                "trigger_on_max": clock["trigger_on_max"],
                "category": clock["category"],
            })
    save_clocks(project_root, data)
    return {
        "cluster_id": cluster_id,
        "event": {"type": event_type, "value": event_value},
        "ticked": ticked,
        "triggered": triggered,
    }


def tick_cluster_end(project_root: Path, cluster_id: str) -> dict:
    """发送当前故事块结束事件。"""
    return tick_event(project_root, cluster_id, "cluster_end")


def list_active(project_root: Path, cluster_id: str) -> dict:
    """返回当前 cluster 写作前可消费的活跃时钟快照。"""
    cluster_id = _require_cluster_id(cluster_id)
    data = load_clocks(project_root)
    active: list[dict] = []
    for clock in data["clocks"]:
        if clock["status"] != "active":
            continue
        remaining = clock["max"] - clock["ticks"]
        urgency = "urgent" if remaining <= 2 else (
            "approaching" if remaining <= clock["max"] * 0.3 else "normal"
        )
        active.append({
            "clock_id": clock["clock_id"],
            "label": clock["label"],
            "category": clock["category"],
            "ticks": clock["ticks"],
            "max": clock["max"],
            "remaining": remaining,
            "urgency": urgency,
            "trigger_on_max": clock["trigger_on_max"],
            "visible_to_protagonist": clock["visible_to_protagonist"],
            "visible_to_writer": clock["visible_to_writer"],
            "is_surprise": clock["is_surprise"],
            "_reason": clock.get("_reason", ""),
        })
    active.sort(key=lambda item: (item["remaining"], -item["ticks"], item["clock_id"]))
    return {"cluster_id": cluster_id, "active_clocks": active, "total_active": len(active)}


def spawn(project_root: Path, cluster_id: str, clock_def: dict) -> dict:
    """在当前 cluster 创建从零开始的活跃时钟。"""
    cluster_id = _require_cluster_id(cluster_id)
    if not isinstance(clock_def, dict):
        raise ClockContractError("spawn JSON 必须是 object")
    public = {key for key in clock_def if not str(key).startswith("_")}
    missing = SPAWN_REQUIRED - set(clock_def)
    unknown = public - SPAWN_REQUIRED
    if missing or unknown:
        raise ClockContractError(
            f"spawn 字段错误: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    data = load_clocks(project_root)
    used_numbers = []
    for clock in data["clocks"]:
        match = re.fullmatch(r"CK_([0-9]+)", clock["clock_id"])
        if match:
            used_numbers.append(int(match.group(1)))
    clock_id = f"CK_{(max(used_numbers, default=0) + 1):03d}"
    new_clock = {
        "clock_id": clock_id,
        "label": clock_def["label"],
        "category": clock_def["category"],
        "ticks": 0,
        "max": clock_def["max"],
        "tick_on": clock_def["tick_on"],
        "tick_per_event": clock_def["tick_per_event"],
        "trigger_on_max": clock_def["trigger_on_max"],
        "visible_to_protagonist": clock_def["visible_to_protagonist"],
        "visible_to_writer": clock_def["visible_to_writer"],
        "is_surprise": clock_def["is_surprise"],
        "since_cluster": cluster_id,
        "spawned_by": clock_def["spawned_by"],
        "status": "active",
        "_reason": clock_def.get("_reason", ""),
    }
    _validate_clock(new_clock, index=len(data["clocks"]))
    data["clocks"].append(new_clock)
    save_clocks(project_root, data)
    return {"cluster_id": cluster_id, "created": clock_id, "label": new_clock["label"]}


def dashboard(project_root: Path) -> dict:
    """汇总全部时钟的状态、类别与紧迫度。"""
    data = load_clocks(project_root)
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_urgency = {"urgent": 0, "approaching": 0, "normal": 0, "triggered": 0}
    for clock in data["clocks"]:
        status = clock["status"]
        by_status[status] = by_status.get(status, 0) + 1
        category = clock["category"]
        by_category[category] = by_category.get(category, 0) + 1
        if status == "triggered":
            by_urgency["triggered"] += 1
        elif status == "active":
            remaining = clock["max"] - clock["ticks"]
            urgency = "urgent" if remaining <= 2 else (
                "approaching" if remaining <= clock["max"] * 0.3 else "normal"
            )
            by_urgency[urgency] += 1
    return {
        "total_clocks": len(data["clocks"]),
        "by_status": by_status,
        "by_category": by_category,
        "by_urgency": by_urgency,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="cluster 剧情时钟")
    parser.add_argument("project")
    parser.add_argument(
        "action",
        choices=["tick-cluster-end", "tick-event", "list", "spawn", "dashboard"],
    )
    parser.add_argument("--cluster")
    parser.add_argument("--event-type")
    parser.add_argument("--event-value", default="")
    parser.add_argument("--json", help="spawn 的 clock 定义 JSON")
    return parser


def main() -> int:
    state_cli_guard.require_internal("clock_engine.py")
    parser = _parser()
    args = parser.parse_args()
    try:
        root = Path(args.project)
        if args.action == "dashboard":
            result = dashboard(root)
        else:
            cluster_id = _require_cluster_id(args.cluster)
            if args.action == "tick-cluster-end":
                result = tick_cluster_end(root, cluster_id)
            elif args.action == "tick-event":
                if not args.event_type:
                    raise ClockContractError("tick-event 需要 --event-type")
                result = tick_event(root, cluster_id, args.event_type, args.event_value)
            elif args.action == "list":
                result = list_active(root, cluster_id)
            else:
                if not args.json:
                    raise ClockContractError("spawn 需要 --json")
                try:
                    definition = json.loads(args.json)
                except json.JSONDecodeError as exc:
                    raise ClockContractError(f"spawn JSON 无效: {exc}") from exc
                result = spawn(root, cluster_id, definition)
    except (ClockContractError, OSError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    triggered = result.get("triggered") or []
    if triggered:
        print(f"[CLOCK TRIGGERED] {len(triggered)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
