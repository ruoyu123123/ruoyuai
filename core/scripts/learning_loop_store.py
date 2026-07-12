"""Cluster-native storage and ordering helpers for the writing learning loop."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import atomic_json
import cluster_lookup

EXPERIENCE_FILE = "写作经验.json"
AUDIT_DIR = ".audit"
EXPERIENCE_SCHEMA_VERSION = "v27"
EXPIRY_DAYS = 30
DECAY_DAYS = 14

_GENERATED_KEYS = (
    "tool_calibration_suggestions",
    "skill_rewrite_suggestions",
    "_recurrence_tracker",
    "_waiver_tracker",
    "_waiver_audit_ledger",
    "_efficacy_tracker",
    "_observed_clusters",
)
class ExperienceContractError(ValueError):
    """Raised when a learning artifact violates the cluster-native schema."""


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db_dir(project_root: Path) -> Path:
    return Path(project_root) / "_数据库"


def experience_path(project_root: Path) -> Path:
    return db_dir(project_root) / EXPERIENCE_FILE


def canonical_cluster_id(value, *, field: str = "cluster_id") -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if cluster_id is None or value != cluster_id:
        raise ExperienceContractError(
            f"{field} 必须是 canonical cluster_id（如 cluster_001），实际为 {value!r}"
        )
    return cluster_id


def cluster_number(cluster_id: str) -> int:
    canonical_cluster_id(cluster_id)
    number = cluster_lookup.cluster_num(cluster_id)
    if number is None:
        raise ExperienceContractError(f"无法解析 cluster_id: {cluster_id!r}")
    return number


def sort_clusters(values) -> list[str]:
    normalized = {canonical_cluster_id(value) for value in values or []}
    return sorted(normalized, key=cluster_number)


def has_consecutive_clusters(clusters: list[str], length: int) -> bool:
    if length <= 1:
        return bool(clusters)
    numbers = [cluster_number(cluster_id) for cluster_id in sort_clusters(clusters)]
    if len(numbers) < length:
        return False
    run = 1
    for previous, current in zip(numbers, numbers[1:]):
        run = run + 1 if current == previous + 1 else 1
        if run >= length:
            return True
    return False


def empty_experience() -> dict:
    return {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "success_patterns": [],
        "failure_patterns": [],
        "preferences": [],
        "tool_calibration_suggestions": [],
        "skill_rewrite_suggestions": [],
        "_recurrence_tracker": {},
        "_waiver_tracker": {},
        "_waiver_audit_ledger": {},
        "_efficacy_tracker": {},
        "_observed_clusters": [],
    }


def _assert_no_forbidden_keys(data: dict) -> None:
    if "entries" in data:
        raise ExperienceContractError("写作经验.json 不接受 reflection entries 输入结构")
    for bucket_name in ("success_patterns", "failure_patterns"):
        for entry in data.get(bucket_name, []):
            if not isinstance(entry, dict):
                raise ExperienceContractError(f"{bucket_name} 必须是 object 数组")
            bad = {
                key for key in entry
                if key.startswith("source_") and key != "source_clusters"
            }
            if bad:
                raise ExperienceContractError(
                    f"{bucket_name} 含非 cluster-native 字段: {sorted(bad)}"
                )
            sources = entry.get("source_clusters")
            if sources is not None:
                entry["source_clusters"] = sort_clusters(sources)

    for tracker_name in ("_recurrence_tracker", "_waiver_tracker", "_efficacy_tracker"):
        tracker = data.get(tracker_name, {})
        if not isinstance(tracker, dict):
            raise ExperienceContractError(f"{tracker_name} 必须是 object")
        for record in tracker.values():
            if not isinstance(record, dict):
                raise ExperienceContractError(f"{tracker_name} 记录必须是 object")
            bad = {key for key in record if key == "chapters" or key.endswith("_chapters")}
            if bad:
                raise ExperienceContractError(
                    f"{tracker_name} 含非 cluster-native 字段: {sorted(bad)}"
                )


def normalize_experience(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ExperienceContractError("写作经验.json 顶层必须是 object")
    for key in ("success_patterns", "failure_patterns", "preferences"):
        if key not in data or not isinstance(data[key], list):
            raise ExperienceContractError(f"写作经验.json 缺少 list 字段 {key}")

    defaults = empty_experience()
    data.setdefault("schema_version", EXPERIENCE_SCHEMA_VERSION)
    for key in _GENERATED_KEYS:
        if key not in data:
            data[key] = defaults[key]
    for key in ("tool_calibration_suggestions", "skill_rewrite_suggestions", "_observed_clusters"):
        if not isinstance(data[key], list):
            raise ExperienceContractError(f"{key} 必须是 list")
    data["_observed_clusters"] = sort_clusters(data["_observed_clusters"])
    _assert_no_forbidden_keys(data)
    return data


def load_experience(project_root: Path) -> dict:
    path = experience_path(project_root)
    if not path.is_file():
        return empty_experience()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperienceContractError(f"写作经验.json 读取失败: {exc}") from exc
    return normalize_experience(data)


def save_experience(project_root: Path, data: dict) -> Path:
    normalized = normalize_experience(data)
    path = experience_path(project_root)
    atomic_json.atomic_write_json(path, normalized)
    return path


def stamp_updated(entry: dict) -> dict:
    entry["updated_at"] = now_text()
    return entry


def route_entry(exp: dict, entry: dict) -> str:
    if not isinstance(entry, dict):
        return "skip"
    category = (entry.get("category") or "").strip().lower()
    if category == "success":
        bucket = exp["success_patterns"]
    elif category == "failure":
        bucket = exp["failure_patterns"]
    else:
        return "skip"
    entry_id = entry.get("id")
    if entry_id:
        bucket[:] = [item for item in bucket if item.get("id") != entry_id]
    stamp_updated(entry)
    bucket.append(entry)
    return category


def prune_and_decay(
    exp: dict,
    expiry_days: int = EXPIRY_DAYS,
    decay_days: int = DECAY_DAYS,
) -> dict:
    pruned, decayed = [], []
    current = datetime.now()
    for bucket_name in ("success_patterns", "failure_patterns"):
        retained = []
        for pattern in exp.get(bucket_name, []):
            updated = pattern.get("updated_at")
            if not updated:
                pattern["updated_at"] = now_text()
                retained.append(pattern)
                continue
            try:
                updated_at = datetime.fromisoformat(str(updated).split("+")[0].split("Z")[0])
            except ValueError:
                pattern["updated_at"] = now_text()
                retained.append(pattern)
                continue
            age_days = (current - updated_at).total_seconds() / 86400
            if age_days > expiry_days:
                pruned.append({
                    "category": bucket_name,
                    "id": pattern.get("id"),
                    "trigger": (pattern.get("trigger") or "")[:50],
                    "age_days": int(age_days),
                })
                continue
            confidence = pattern.get("confidence")
            if age_days > decay_days and isinstance(confidence, (int, float)) and confidence > 0.1:
                new_confidence = round(confidence * 0.8, 2)
                pattern["confidence"] = new_confidence
                decayed.append({
                    "category": bucket_name,
                    "id": pattern.get("id"),
                    "age_days": int(age_days),
                    "from": confidence,
                    "to": new_confidence,
                })
            retained.append(pattern)
        exp[bucket_name] = retained
    return {"pruned": pruned, "decayed": decayed}
