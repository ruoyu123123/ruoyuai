#!/usr/bin/env python3
"""按 cluster 草稿评估主角压力，并记录可追踪的 cluster 状态。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json
import state_cli_guard


SCHEMA_NAME = "cluster_protagonist_stress"
SCHEMA_VERSION = "1.0"
CLUSTER_RE = re.compile(r"^cluster_[0-9]{3,}$")
OUTCOME_TRIGGER_TYPES = {"persona_violation", "persona_align", "neutral"}


class StressContractError(ValueError):
    """压力档或 cluster 创作产物不符合唯一契约。"""


def _require_cluster_id(value: str) -> str:
    if not isinstance(value, str) or not CLUSTER_RE.fullmatch(value):
        raise StressContractError(f"非法 cluster_id: {value!r}")
    return value


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise StressContractError(f"文件不存在: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StressContractError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StressContractError(f"JSON 顶层必须是 object: {path}")
    return value


def _require_nonempty_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StressContractError(f"{field} 必须是非空字符串")
    return value


def _validate_trait(trait: dict, index: int) -> None:
    required = {"trait", "violation_keywords", "align_keywords", "stress_per_violation"}
    if not isinstance(trait, dict) or not required <= set(trait):
        raise StressContractError(f"core_traits[{index}] 字段不完整")
    _require_nonempty_string(trait["trait"], f"core_traits[{index}].trait")
    for field in ("violation_keywords", "align_keywords"):
        if not isinstance(trait[field], list) or not all(isinstance(x, str) and x for x in trait[field]):
            raise StressContractError(f"core_traits[{index}].{field} 必须是字符串数组")
    value = trait["stress_per_violation"]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StressContractError(f"core_traits[{index}].stress_per_violation 必须是正整数")


def _validate_card(card: dict, index: int) -> None:
    required = {"card_id", "label", "trigger_min_stress", "weight", "permanent_persona_changes", "narrative_effect"}
    if not isinstance(card, dict) or not required <= set(card):
        raise StressContractError(f"mental_break_pool[{index}] 字段不完整")
    _require_nonempty_string(card["card_id"], f"mental_break_pool[{index}].card_id")
    _require_nonempty_string(card["label"], f"mental_break_pool[{index}].label")
    for field in ("trigger_min_stress", "weight"):
        value = card[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise StressContractError(f"mental_break_pool[{index}].{field} 必须是非负整数")
    if card["weight"] == 0:
        raise StressContractError(f"mental_break_pool[{index}].weight 必须大于 0")
    if not isinstance(card["permanent_persona_changes"], list):
        raise StressContractError(f"mental_break_pool[{index}].permanent_persona_changes 必须是数组")
    _require_nonempty_string(card["narrative_effect"], f"mental_break_pool[{index}].narrative_effect")


def validate_stress(stress: dict) -> dict:
    required = {
        "_schema", "schema_version", "protagonist", "stress_level", "stress_max",
        "stress_threshold_break", "stress_log", "persona_violations_tracked",
        "mental_break_pool", "coping_mechanisms",
    }
    missing = required - set(stress)
    public = {key for key in stress if not str(key).startswith("_")}
    # consumption = scaffold 骨架统一的消费方声明元数据（narrator_calibrate 同款豁免）
    allowed_public = required - {"_schema"} | {"consumption"}
    unknown = public - allowed_public
    if missing or unknown:
        raise StressContractError(
            f"主角压力档字段错误: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if stress["_schema"] != SCHEMA_NAME or stress["schema_version"] != SCHEMA_VERSION:
        raise StressContractError(f"主角压力档 schema 必须是 {SCHEMA_NAME!r}/{SCHEMA_VERSION!r}")
    _require_nonempty_string(stress["protagonist"], "protagonist")
    for field in ("stress_level", "stress_max", "stress_threshold_break"):
        value = stress[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise StressContractError(f"{field} 必须是非负整数")
    if stress["stress_max"] <= 0 or stress["stress_level"] > stress["stress_max"]:
        raise StressContractError("stress_level/stress_max 范围无效")
    if not 0 < stress["stress_threshold_break"] <= stress["stress_max"]:
        raise StressContractError("stress_threshold_break 必须位于 1..stress_max")
    if not isinstance(stress["stress_log"], list):
        raise StressContractError("stress_log 必须是数组")
    tracked = stress["persona_violations_tracked"]
    if not isinstance(tracked, dict) or not isinstance(tracked.get("core_traits"), list):
        raise StressContractError("persona_violations_tracked.core_traits 必须是数组")
    for index, trait in enumerate(tracked["core_traits"]):
        _validate_trait(trait, index)
    if not isinstance(stress["mental_break_pool"], list):
        raise StressContractError("mental_break_pool 必须是数组")
    for index, card in enumerate(stress["mental_break_pool"]):
        _validate_card(card, index)
    if not isinstance(stress["coping_mechanisms"], dict):
        raise StressContractError("coping_mechanisms 必须是 object")
    for index, entry in enumerate(stress["stress_log"]):
        if not isinstance(entry, dict):
            raise StressContractError(f"stress_log[{index}] 必须是 object")
        _require_cluster_id(entry.get("cluster_id"))
        if entry.get("trigger_type") not in OUTCOME_TRIGGER_TYPES:
            raise StressContractError(f"stress_log[{index}].trigger_type 非法")
    return stress


def load_stress(project_root: Path) -> dict:
    return validate_stress(_read_json(Path(project_root) / "_数据库" / "主角压力档.json"))


def save_stress(project_root: Path, stress: dict) -> None:
    validate_stress(stress)
    atomic_json.atomic_write_json(Path(project_root) / "_数据库" / "主角压力档.json", stress)


def stress_view(stress: dict) -> dict:
    """返回 canonical 压力快照，供 evaluator 与 manifest 共用。"""
    validate_stress(stress)
    traits = stress["persona_violations_tracked"]["core_traits"]
    return {
        "mode": "cluster",
        "stress_level": stress["stress_level"],
        "stress_max": stress["stress_max"],
        "stress_threshold_break": stress["stress_threshold_break"],
        "traits": traits,
    }


def _cluster_artifacts(project_root: Path, cluster_id: str) -> tuple[dict, str]:
    draft_dir = Path(project_root) / "章节" / f"{cluster_id}_draft"
    changes = _read_json(draft_dir / f"{cluster_id}_changes.json")
    if not isinstance(changes.get("self_eval"), dict):
        raise StressContractError("cluster changes.self_eval 必须是 object")
    draft_path = draft_dir / f"{cluster_id}_draft.txt"
    if not draft_path.is_file():
        raise StressContractError(f"文件不存在: {draft_path}")
    try:
        draft = draft_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StressContractError(f"正文读取失败: {draft_path}: {exc}") from exc
    if not draft.strip():
        raise StressContractError(f"cluster 正文为空: {draft_path}")
    return changes, draft


def evaluate_stress_delta_from_self_eval(stress_self: dict, traits: list[dict]):
    """将 writer 对本 cluster 的压力自评转成数值变化。"""
    if stress_self is None:
        return None
    if not isinstance(stress_self, dict):
        raise StressContractError("stress_evaluation_self 必须是 object")
    violations = stress_self.get("violations_made", [])
    alignments = stress_self.get("alignments_made", [])
    estimated = stress_self.get("estimated_stress_change")
    if not isinstance(violations, list) or not all(isinstance(x, str) for x in violations):
        raise StressContractError("violations_made 必须是字符串数组")
    if not isinstance(alignments, list) or not all(isinstance(x, str) for x in alignments):
        raise StressContractError("alignments_made 必须是字符串数组")
    if estimated is not None and (not isinstance(estimated, str) or not re.fullmatch(r"[+-]?\d+", estimated.strip())):
        raise StressContractError("estimated_stress_change 必须是带符号整数文本")
    if not violations and not alignments and estimated is None:
        return None
    per = traits[0]["stress_per_violation"] if traits else 2
    delta = per * min(len(violations), 3) - (1 if len(alignments) >= 2 else 0)
    if estimated is not None:
        delta = int(estimated)
    return {
        "delta": delta,
        "violations": [{"trait": "writer_declared", "declared": violations, "count": len(violations)}] if violations else [],
        "alignments": [{"trait": "writer_declared", "declared": alignments, "count": len(alignments)}] if alignments else [],
        "source": "writer_declared",
    }


def evaluate_stress_delta(text: str, traits: list[dict]) -> dict:
    """按 persona trait 关键词计算本 cluster 的 advisory 压力变化。"""
    delta = 0
    violations = []
    alignments = []
    for trait in traits:
        v_hits = sum(text.count(keyword) for keyword in trait["violation_keywords"])
        a_hits = sum(text.count(keyword) for keyword in trait["align_keywords"])
        if v_hits:
            added = trait["stress_per_violation"] * min(v_hits, 3)
            delta += added
            violations.append({"trait": trait["trait"], "hits": v_hits, "stress_added": added})
        elif a_hits >= 2:
            delta -= 1
            alignments.append({"trait": trait["trait"], "hits": a_hits, "stress_relief": -1})
    return {"delta": delta, "violations": violations, "alignments": alignments, "source": "keyword_scan"}


def draw_mental_break_card(pool: list[dict], stress_level: int, cluster_id: str) -> dict | None:
    """用 cluster 稳定选择满足阈值的卡，重复运行得到同一结果。"""
    _require_cluster_id(cluster_id)
    eligible = [card for card in pool if stress_level >= card["trigger_min_stress"]]
    if not eligible:
        return None
    total = sum(card["weight"] for card in eligible)
    seed = int.from_bytes(hashlib.sha256(f"{cluster_id}:{stress_level}".encode()).digest()[:8], "big")
    cursor = seed % total
    for card in eligible:
        cursor -= card["weight"]
        if cursor < 0:
            return card
    return eligible[-1]


def apply_card_to_event_table(project_root: Path, cluster_id: str, card: dict, protagonist: str) -> dict:
    """将 mental-break 结果作为 cluster 事件写入事件表。"""
    path = Path(project_root) / "_数据库" / "事件表.json"
    table = _read_json(path)
    events = table.get("events")
    if not isinstance(events, list):
        raise StressContractError("事件表.events 必须是数组")
    event_id = f"MB_event_{cluster_id}_{card['card_id']}"
    record = {
        "id": event_id,
        "cluster_id": cluster_id,
        "type": "mental_break_triggered",
        "protagonist": protagonist,
        "card": card["label"],
        "card_id": card["card_id"],
        "permanent_persona_changes": card["permanent_persona_changes"],
        "narrative_effect": card["narrative_effect"],
    }
    table["events"] = [item for item in events if not (isinstance(item, dict) and item.get("id") == event_id)]
    table["events"].append(record)
    atomic_json.atomic_write_json(path, table)
    return record


def is_unignited_skeleton(stress: dict) -> bool:
    """未点火裸骨架 = protagonist 空且 stress_log/mental_break_pool 全空（scaffold 初始态）。
    北极星纪律：31 子系统裸骨架合法 fluid（仅涟漪/ME池/storyboard 三码 hard），
    未点火跳过评估；已点火（任一字段有值）则走 validate_stress 全套硬校验。"""
    return (isinstance(stress, dict)
            and not str(stress.get("protagonist") or "").strip()
            and not stress.get("stress_log")
            and not stress.get("mental_break_pool"))


def evaluate(project_root: Path, cluster_id: str) -> dict:
    """评估并持久化一个 cluster，日志按 cluster_id 幂等替换。"""
    cluster_id = _require_cluster_id(cluster_id)
    raw = _read_json(Path(project_root) / "_数据库" / "主角压力档.json")
    if is_unignited_skeleton(raw):
        return {
            "cluster_id": cluster_id, "skipped": True,
            "reason": "主角压力档为未点火裸骨架（protagonist/stress_log/mental_break_pool 全空）·合法 fluid·跳过评估",
            "high_stress_warning": False, "mental_break_triggered": False,
        }
    stress = load_stress(project_root)
    changes, draft = _cluster_artifacts(project_root, cluster_id)
    view = stress_view(stress)
    traits = view["traits"]
    stress_self = (changes["self_eval"].get("stress_evaluation_self"))
    result = evaluate_stress_delta_from_self_eval(stress_self, traits)
    if result is None:
        result = evaluate_stress_delta(draft, traits)
    existing = next((entry for entry in stress["stress_log"] if entry["cluster_id"] == cluster_id), None)
    old = existing["stress_old"] if isinstance(existing, dict) and isinstance(existing.get("stress_old"), int) else stress["stress_level"]
    new = max(0, min(view["stress_max"], old + result["delta"]))
    card = None
    if new >= view["stress_threshold_break"]:
        card = draw_mental_break_card(stress["mental_break_pool"], new, cluster_id)
        if card:
            apply_card_to_event_table(project_root, cluster_id, card, stress["protagonist"])
            new = 0
    stress["stress_level"] = new
    entry = {
        "cluster_id": cluster_id,
        "stress_old": old,
        "change": result["delta"],
        "new_total": new,
        "trigger_type": "persona_violation" if result["delta"] > 0 else ("persona_align" if result["delta"] < 0 else "neutral"),
        "delta_source": result["source"],
        "violations": result["violations"],
        "alignments": result["alignments"],
        "mental_break_card": card["card_id"] if card else None,
    }
    stress["stress_log"] = [e for e in stress["stress_log"] if e["cluster_id"] != cluster_id] + [entry]
    stress["stress_log"].sort(key=lambda e: int(e["cluster_id"].rsplit("_", 1)[1]))
    save_stress(project_root, stress)
    return {
        "cluster_id": cluster_id,
        "schema_mode": view["mode"],
        "stress_delta": result["delta"],
        "delta_source": result["source"],
        "stress_old": old,
        "stress_new": new,
        "violations": result["violations"],
        "alignments": result["alignments"],
        "high_stress_warning": new >= view["stress_threshold_break"] * 0.75,
        "mental_break_triggered": card is not None,
        "card": ({
            "id": card["card_id"],
            "label": card["label"],
            "permanent_changes": card["permanent_persona_changes"],
            "narrative_effect": card["narrative_effect"],
        } if card else None),
    }


def main() -> int:
    state_cli_guard.require_internal("stress_evaluator.py")
    parser = argparse.ArgumentParser(description="cluster 主角压力评估")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        result = evaluate(Path(args.project), args.cluster)
    except (StressContractError, OSError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["high_stress_warning"] or result["mental_break_triggered"] else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
