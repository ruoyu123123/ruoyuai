#!/usr/bin/env python3
"""Cluster 级世界涟漪规则引擎。

客观数值后果由规则确定性应用；叙事后果写入 narrative_consequences，供 writer
和涌现引擎解释。正式 cluster tick、ME 完成与世界状态消费由
world_evolution_apply_cluster.py 统一调度，本模块仅提供规则计算、走向卡应用和只读看板。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup
from atomic_json import atomic_write_json
from text_metrics import count_cjk as _cjk_count  # 字数口径单一真理源


TRIGGER_TYPES = {"minor_event", "fate_event", "auto_tick"}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def load_world(project_root: Path) -> dict | None:
    return load_json(Path(project_root) / "_数据库" / "世界状态.json")


def load_rules(project_root: Path) -> dict | None:
    return load_json(Path(project_root) / "_数据库" / "涟漪规则.json")


def save_world(project_root: Path, world: dict) -> None:
    save_json(Path(project_root) / "_数据库" / "世界状态.json", world)


def require_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise ValueError(f"非法 cluster_id: {value!r}")
    return cluster_id


def _resolve_path(world: dict, dotted: str):
    parts = dotted.split(".")
    current = world
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return None, None
        current = current[part]
    return (current, parts[-1]) if isinstance(current, dict) else (None, None)


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _next_id(rows: list[dict], key: str, prefix: str) -> str:
    numbers = []
    for row in rows:
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", str(row.get(key) or ""))
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def _record_thread_completion(world: dict, thread: dict, cluster_id: str,
                              cause: str) -> str:
    thread_id = str(thread.get("thread_id") or "unknown")
    key = f"{cluster_id}_{cause}_{thread_id}"
    tracker = world.setdefault("consequence_tracker", {})
    if not isinstance(tracker, dict):
        raise ValueError("世界状态.consequence_tracker 必须是 object")
    tracker[key] = {
        "trigger": cause,
        "thread_id": thread_id,
        "npc": thread.get("npc_id"),
        "outcome": thread.get("outcome_if_complete", ""),
        "world_changes": [thread.get("outcome_if_complete", "")],
        "added_at_cluster": cluster_id,
    }
    return key


# ───────── producer 契约静态校验（单一真理源·db_schema_validate 与 volume_arc 骨架验收共用）─────────
# 涟漪规则的形态判定只在本模块定义：_apply_ripple 入口先跑 validate_ripple_shape（apply 与校验
# 永不漂移）；消费端 db_schema_validate.check_ripple_rules_contract 与 producer 端
# gen_creative_volume_arc._normalize_skeleton 都导入 validate_rules_contract，禁止另抄形态清单。

ME_ID_RE = re.compile(r"ME-V\d+-\d+")

_CANONICAL_RIPPLE_OPS = (
    "narrative", "delta", "advance", "set", "set_to_current_cluster",
    "add_thread", "evaluate_completion", "spawn", "add",
)


def validate_ripple_shape(ripple) -> str | None:
    """静态判定一条 ripple 是否为 _apply_ripple 可应用的 canonical 形态。

    返回错误描述或 None（合法）。只查 ripple 自身形态；路径是否存在、目标现值类型
    等需要世界状态的运行时问题由 _apply_ripple 处理。分支顺序与 _apply_ripple 一致。
    """
    if not isinstance(ripple, dict):
        return "ripple 必须是 object"
    target = ripple.get("target")

    if "narrative" in ripple:
        if target:
            return ('narrative ripple 不可带 target（canonical 形态 {"narrative": "<文本>"}·'
                    "带 target 会落入结构化通路 → apply 时刻 ValueError 硬炸）")
        if not str(ripple.get("narrative") or "").strip():
            return "narrative ripple 不可为空"
        return None

    if not isinstance(target, str) or not target:
        return "结构化 ripple 缺少 target"

    if "delta" in ripple:
        return None if _numeric(ripple["delta"]) else f"delta ripple 要求数值增量: {target}"

    if "advance" in ripple:
        return None if _numeric(ripple["advance"]) else f"advance ripple 要求数值增量: {target}"

    if "set" in ripple:
        return None

    if ripple.get("set_to_current_cluster") is True:
        return None

    if "add_thread" in ripple:
        if target != "active_npc_threads":
            return f"add_thread ripple 的 target 必须是 active_npc_threads: {target!r}"
        definition = ripple["add_thread"]
        if not isinstance(definition, dict):
            return "add_thread 必须是 object"
        if not str(definition.get("npc_id") or "").strip() or not str(definition.get("action") or "").strip():
            return "add_thread 必须包含 npc_id 与 action"
        try:
            if int(definition.get("expected_responses", 1)) < 1:
                return "expected_responses 必须 >= 1"
        except (TypeError, ValueError):
            return f"expected_responses 必须是整数: {definition.get('expected_responses')!r}"
        return None

    if ripple.get("evaluate_completion") is True:
        return (None if target == "active_npc_threads"
                else f"evaluate_completion ripple 的 target 必须是 active_npc_threads: {target!r}")

    if "spawn" in ripple:
        if target != "emergent_opportunities":
            return f"spawn ripple 的 target 必须是 emergent_opportunities: {target!r}"
        definition = ripple["spawn"]
        if not isinstance(definition, dict):
            return "spawn 必须是 object"
        expires = definition.get("expires_clusters", 2)
        if not isinstance(expires, int) or isinstance(expires, bool) or expires < 1:
            return "expires_clusters 必须是正整数"
        return None

    if "add" in ripple:
        if target != "consequence_tracker":
            return f"consequence add ripple 的 target 必须是 consequence_tracker: {target!r}"
        return None if isinstance(ripple["add"], dict) else "consequence add 必须是 object"

    hint = ""
    if "op" in ripple or "note" in ripple:
        hint = ('·op/note 字段不被 engine 认——叙事形态应写 {"narrative": "<文本>"} 且无 target，'
                '数值形态写 {"target","delta"} / {"target","advance"}')
    keys = sorted(k for k in ripple if not str(k).startswith("_"))
    return f"未知 ripple 操作（canonical 形态清单: {'/'.join(_CANONICAL_RIPPLE_OPS)}）: 键={keys}{hint}"


def validate_trigger_contract(trigger_type: str, trigger_match: str) -> str | None:
    """静态判定 trigger_type 与 trigger_match 形态能否在对应通路点火（死规则防线）。

    与 _match_rule 的通路对齐：fate_event 的 trigger_value 永远是大势卡 ME id
    （world_evolution_apply_cluster 传 event_id）；auto_tick 只以 every_cluster 点火；
    走向卡文本触发词只会从 minor_event 通路来。多别名权威分隔符是 |（_match_rule
    只按 | 拆 candidates），/ 分隔的多别名会被当单一整串做子串比对 → 静默永不点火。
    返回错误描述或 None（合法）。
    """
    raw = str(trigger_match or "")
    candidates = [p.strip() for p in raw.split("|") if p.strip()]
    if trigger_type in ("minor_event", "fate_event") and "/" in raw and "|" not in raw:
        segments = [seg.strip() for seg in raw.split("/")]
        if all(_cjk_count(seg) >= 2 for seg in segments):  # / 两侧均为 ≥2 字短语（区别于路径/日期文本）
            return (f"trigger_match 疑用 / 分隔多别名 {raw!r}——engine 权威分隔符是 |"
                    f"（_match_rule 只按 | 拆 candidates），/ 串会被当单一整串做子串比对"
                    f"→ 多值触发词双向都不命中 = 涟漪静默永不点火；请改写为 {'|'.join(segments)!r}")
    if trigger_type == "fate_event":
        bad = [c for c in candidates if not ME_ID_RE.fullmatch(c)]
        if bad:
            return (f"fate_event 规则的 trigger_match 必须是 ME id（ME-V<卷>-<序>·可用 | 分隔多值），"
                    f"实际含文本触发词 {bad!r}——fate_event 通路的 trigger_value 永远是 ME id，"
                    f"文本触发词标 fate_event = 任何通路都永不点火的死规则；文本触发词请标 minor_event")
    elif trigger_type == "auto_tick":
        if candidates != ["every_cluster"]:
            return (f"auto_tick 规则的 trigger_match 必须恰为 'every_cluster'（_match_rule 硬比对），"
                    f"实际 {trigger_match!r} = 永不点火的死规则；周期性世界漂移写 every_cluster，"
                    f"文本触发词请标 minor_event")
    return None


def validate_rules_contract(rules_json) -> list[str]:
    """静态校验整份 涟漪规则.json 的 producer 契约，返回错误描述列表（空 = 合法）。

    单一真理源：基础结构复用 _normalize_rule、触发通路对齐 _match_rule、ripple 形态对齐
    _apply_ripple。空 ripple_rules 不在此报（载荷非空归 RIPPLE_RULES_EMPTY hard gate）。
    """
    if not isinstance(rules_json, dict) or not isinstance(rules_json.get("ripple_rules"), list):
        return ["涟漪规则.json 必须包含 ripple_rules array"]
    errors: list[str] = []
    for i, rule in enumerate(rules_json["ripple_rules"]):
        rid = rule.get("id") if isinstance(rule, dict) else None
        prefix = f"ripple_rules[{i}]({rid or '?'})"
        try:
            canonical = _normalize_rule(rule)
        except ValueError as e:
            errors.append(f"{prefix}: {e}")
            continue
        trig_err = validate_trigger_contract(canonical["trigger_type"], canonical["trigger_match"])
        if trig_err:
            errors.append(f"{prefix}: {trig_err}")
        for j, ripple in enumerate(canonical["ripples"]):
            shape_err = validate_ripple_shape(ripple)
            if shape_err:
                errors.append(f"{prefix}.ripples[{j}]: {shape_err}")
    return errors


def _apply_ripple(world: dict, ripple: dict, cluster_id: str,
                  applied_log: list[dict]) -> bool:
    """应用一条 canonical ripple；无法定位目标路径时记录跳过。

    入口先过 validate_ripple_shape（形态契约单一真理源），分支内只做需要世界状态的运行时检查。
    """
    cluster_id = require_cluster_id(cluster_id)
    shape_error = validate_ripple_shape(ripple)
    if shape_error:
        raise ValueError(shape_error)
    target = ripple.get("target")
    reason = str(ripple.get("reason") or "")

    if "narrative" in ripple:
        text = str(ripple["narrative"]).strip()
        consequences = world.setdefault("narrative_consequences", [])
        if not isinstance(consequences, list):
            raise ValueError("世界状态.narrative_consequences 必须是 array")
        duplicate = any(
            isinstance(item, dict)
            and item.get("cluster_id") == cluster_id
            and item.get("text") == text
            for item in consequences
        )
        if not duplicate:
            consequences.append({
                "cluster_id": cluster_id,
                "text": text,
                "reason": reason,
                "_kind": "narrative",
            })
            applied_log.append({
                "target": "narrative_consequences",
                "op": "narrative",
                "text": text[:60],
            })
        return True

    if not isinstance(target, str) or not target:
        raise ValueError("结构化 ripple 缺少 target")

    if "delta" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "delta", "result": "skip_path_missing"})
            return False
        old, delta = parent.get(key, 0), ripple["delta"]
        if not _numeric(old):
            raise ValueError(f"delta ripple 要求数值目标路径: {target}")
        new = max(0, min(100, old + delta))
        parent[key] = new
        applied_log.append({
            "target": target, "op": "delta", "old": old, "new": new,
            "delta": delta, "reason": reason,
        })
        return True

    if "advance" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "advance", "result": "skip_path_missing"})
            return False
        increment = ripple["advance"]
        old = parent.get(key, 0)
        if old is None:
            old = 0
        if not _numeric(old):
            raise ValueError(f"advance ripple 目标必须是数值或 null: {target}")
        parent[key] = old + increment
        applied_log.append({
            "target": target, "op": "advance", "old": old,
            "new": parent[key], "inc": increment, "reason": reason,
        })
        return True

    if "set" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "set", "result": "skip_path_missing"})
            return False
        old = parent.get(key)
        parent[key] = ripple["set"]
        applied_log.append({
            "target": target, "op": "set", "old": old,
            "new": ripple["set"], "reason": reason,
        })
        return True

    if ripple.get("set_to_current_cluster") is True:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "set_cluster", "result": "skip_path_missing"})
            return False
        parent[key] = cluster_id
        applied_log.append({"target": target, "op": "set_cluster", "new": cluster_id})
        return True

    if "add_thread" in ripple and target == "active_npc_threads":
        definition = ripple["add_thread"]
        npc_id = str(definition.get("npc_id") or "").strip()
        action = str(definition.get("action") or "").strip()
        threads = world.setdefault("active_npc_threads", [])
        if not isinstance(threads, list):
            raise ValueError("世界状态.active_npc_threads 必须是 array")
        thread = {
            "thread_id": _next_id(threads, "thread_id", "NT_"),
            "npc_id": npc_id,
            "current_action": action,
            "since_cluster": cluster_id,
            "expected_complete_cluster": definition.get("expected_complete_cluster"),
            "expected_responses": int(definition.get("expected_responses", 1)),
            "responded_count": 0,
            "responded_by_cluster": [],
            "visible_to_protagonist": bool(definition.get("visible_to_protagonist", False)),
            "outcome_if_complete": str(definition.get("outcome_if_complete") or ""),
            "_priority": int(definition.get("_priority", 5)),
            "_spawned_by_ripple": True,
        }
        threads.append(thread)
        applied_log.append({
            "target": target, "op": "add_thread",
            "thread_id": thread["thread_id"], "npc": npc_id,
        })
        return True

    if ripple.get("evaluate_completion") is True and target == "active_npc_threads":
        threads = world.setdefault("active_npc_threads", [])
        if not isinstance(threads, list):
            raise ValueError("世界状态.active_npc_threads 必须是 array")
        current_number = cluster_lookup.cluster_num(cluster_id)
        completed, remaining = [], []
        for thread in threads:
            if not isinstance(thread, dict):
                raise ValueError("active_npc_threads 条目必须是 object")
            due = cluster_lookup.cluster_num(thread.get("expected_complete_cluster"))
            if due is not None and current_number >= due:
                completed.append(thread)
                _record_thread_completion(world, thread, cluster_id, "thread_deadline")
            else:
                remaining.append(thread)
        world["active_npc_threads"] = remaining
        applied_log.append({
            "target": target,
            "op": "evaluate_completion",
            "completed_thread_ids": [row.get("thread_id") for row in completed],
        })
        return True

    if "spawn" in ripple and target == "emergent_opportunities":
        definition = ripple["spawn"]
        opportunities = world.setdefault("emergent_opportunities", [])
        if not isinstance(opportunities, list):
            raise ValueError("世界状态.emergent_opportunities 必须是 array")
        expires_clusters = definition.get("expires_clusters", 2)
        current_number = cluster_lookup.cluster_num(cluster_id)
        opportunity = {
            "id": _next_id(opportunities, "id", "EO_"),
            "trigger_cluster": f"cluster_{current_number + 1:03d}",
            "type": str(definition.get("type") or "副线机缘"),
            "description": str(definition.get("description") or ""),
            "consumed_by_writer": False,
            "expires_at_cluster": f"cluster_{current_number + expires_clusters:03d}",
            "_spawned_by_ripple": True,
        }
        opportunities.append(opportunity)
        applied_log.append({"target": target, "op": "spawn", "id": opportunity["id"]})
        return True

    if "add" in ripple and target == "consequence_tracker":
        addition = ripple["add"]
        tracker = world.setdefault("consequence_tracker", {})
        if not isinstance(tracker, dict):
            raise ValueError("世界状态.consequence_tracker 必须是 object")
        event = str(addition.get("event") or "consequence")
        key = f"{cluster_id}_{event[:30]}"
        tracker[key] = {
            "trigger": event,
            "world_changes": addition.get("world_changes", []),
            "added_at_cluster": cluster_id,
        }
        applied_log.append({"target": target, "op": "add", "key": key})
        return True

    raise ValueError(f"未知 ripple 操作: {ripple}")


def _normalize_rule(rule: dict) -> dict:
    """校验并返回 canonical 涟漪规则。"""
    if not isinstance(rule, dict):
        raise ValueError("ripple_rules 条目必须是 object")
    rule_id = str(rule.get("id") or "").strip()
    trigger_type = rule.get("trigger_type")
    trigger_match = str(rule.get("trigger_match") or "").strip()
    ripples = rule.get("ripples")
    if not rule_id or trigger_type not in TRIGGER_TYPES or not trigger_match:
        raise ValueError("涟漪规则必须包含 id、合法 trigger_type 与 trigger_match")
    if not isinstance(ripples, list) or not ripples or not all(isinstance(item, dict) for item in ripples):
        raise ValueError(f"涟漪规则 {rule_id} 的 ripples 必须是非空 object array")
    return {
        "id": rule_id,
        "trigger_type": trigger_type,
        "trigger_match": trigger_match,
        "ripples": ripples,
    }


def _canonical_rules(rules_json: dict) -> list[dict]:
    if not isinstance(rules_json, dict) or not isinstance(rules_json.get("ripple_rules"), list):
        raise ValueError("涟漪规则.json 必须包含 ripple_rules array")
    return [_normalize_rule(rule) for rule in rules_json["ripple_rules"]]


def _match_rule(rule: dict, trigger_type: str, trigger_value: str) -> bool:
    if rule.get("trigger_type") != trigger_type:
        return False
    candidates = [part.strip() for part in str(rule.get("trigger_match") or "").split("|") if part.strip()]
    if trigger_type == "auto_tick":
        return trigger_value == "every_cluster" and candidates == ["every_cluster"]
    return any(candidate in trigger_value or trigger_value in candidate for candidate in candidates)


def matching_rule_ids(rules_json: dict, trigger_type: str, trigger_value: str) -> list[str]:
    return [
        rule["id"] for rule in _canonical_rules(rules_json)
        if _match_rule(rule, trigger_type, trigger_value)
    ]


def _apply_rules(world: dict, rules_json: dict, trigger_type: str,
                 trigger_value: str, cluster_id: str) -> dict:
    cluster_id = require_cluster_id(cluster_id)
    applied_log: list[dict] = []
    matched_rules = []
    for rule in _canonical_rules(rules_json):
        if not _match_rule(rule, trigger_type, trigger_value):
            continue
        matched_rules.append(rule["id"])
        for ripple in rule["ripples"]:
            _apply_ripple(world, ripple, cluster_id, applied_log)
    return {"matched_rules": matched_rules, "applied_log": applied_log}


def apply_minor_event(project_root: Path, cluster_id: str, event_value: str) -> dict:
    """把用户选中的 cluster 走向卡涟漪幂等写入世界状态。"""
    cluster_id = require_cluster_id(cluster_id)
    event_value = str(event_value or "").strip()
    if not event_value:
        raise ValueError("minor_event trigger 不可为空")
    world = load_world(project_root)
    rules = load_rules(project_root)
    if not isinstance(world, dict) or not isinstance(rules, dict):
        raise ValueError("世界状态.json 与 涟漪规则.json 均为 required")

    ledger = world.setdefault("applied_minor_events", {})
    if not isinstance(ledger, dict):
        raise ValueError("世界状态.applied_minor_events 必须是 object")
    previous = ledger.get(cluster_id)
    if previous:
        if previous.get("trigger") != event_value:
            raise ValueError(f"{cluster_id} 已应用另一走向卡，禁止覆盖")
        return {
            "cluster_id": cluster_id,
            "action": "apply_minor_event",
            "trigger": event_value,
            "matched_rules": previous.get("matched_rules", []),
            "applied_log": [],
            "skipped_idempotent": True,
        }

    matched = matching_rule_ids(rules, "minor_event", event_value)
    if not matched:
        return {
            "cluster_id": cluster_id,
            "action": "apply_minor_event",
            "trigger": event_value,
            "matched_rules": [],
            "applied_log": [],
        }
    result = _apply_rules(world, rules, "minor_event", event_value, cluster_id)
    ledger[cluster_id] = {
        "trigger": event_value,
        "matched_rules": result["matched_rules"],
        "applied_at": datetime.now().isoformat(timespec="seconds"),
    }
    world.setdefault("world_ticks_log", []).append({
        "cluster_id": cluster_id,
        "trigger_type": "minor_event",
        "trigger_value": event_value,
        "matched_rules": result["matched_rules"],
    })
    save_world(project_root, world)
    return {
        "cluster_id": cluster_id,
        "action": "apply_minor_event",
        "trigger": event_value,
        **result,
    }


def opportunity_window(opportunity: dict, cluster_id: str) -> tuple[bool, int | None]:
    current = cluster_lookup.cluster_num(require_cluster_id(cluster_id))
    trigger = cluster_lookup.cluster_num(opportunity.get("trigger_cluster"))
    expires = cluster_lookup.cluster_num(opportunity.get("expires_at_cluster"))
    if trigger is None or expires is None or trigger > expires:
        raise ValueError(f"机缘窗口字段无效: {opportunity.get('id')}")
    return trigger <= current <= expires, expires - current


def dashboard(project_root: Path) -> dict:
    world = load_world(project_root)
    if not isinstance(world, dict):
        raise ValueError("世界状态.json 不存在或不是 object")
    current_cluster = world.get("current_world_time", {}).get("cluster")
    active_opportunities = []
    if current_cluster:
        for opportunity in world.get("emergent_opportunities", []) or []:
            if not isinstance(opportunity, dict) or opportunity.get("consumed_by_writer"):
                continue
            active, remaining = opportunity_window(opportunity, current_cluster)
            if active:
                active_opportunities.append({
                    "id": opportunity.get("id"),
                    "type": opportunity.get("type"),
                    "remaining_clusters": remaining,
                })
    return {
        "current_cluster": current_cluster,
        "current_day": world.get("current_world_time", {}).get("day"),
        "factions": world.get("factions_state", {}),
        "active_npc_threads": world.get("active_npc_threads", []),
        "active_emergent_opportunities": active_opportunities,
        "consequence_records": len(world.get("consequence_tracker", {})),
        "world_ticks_logged": len(world.get("world_ticks_log", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="世界演化只读看板")
    parser.add_argument("project")
    parser.add_argument("action", choices=["dashboard"])
    args = parser.parse_args()
    try:
        print(json.dumps(dashboard(Path(args.project)), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
