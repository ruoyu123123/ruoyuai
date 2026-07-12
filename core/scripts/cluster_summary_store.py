"""cluster 摘要账本的唯一锁内原子写入器。"""

from __future__ import annotations

import copy
from pathlib import Path

import atomic_json
import cluster_lookup
from cluster_summary_reader import (
    CLUSTER_FIELDS,
    SCHEMA_VERSION,
    ClusterSummaryError,
    load_summary,
    summary_path,
    validate_summary,
)


def _canonical_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise ClusterSummaryError(f"非法 cluster_id: {value!r}")
    return cluster_id


def _deep_merge(base: dict, patch: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def initialize_summary(project_root) -> dict:
    """仅供新项目初始化；目标已存在时拒绝覆盖。"""
    path = summary_path(project_root)
    with atomic_json.with_file_lock(path):
        if path.exists():
            raise ClusterSummaryError(f"摘要账本已存在，拒绝重新初始化: {path}")
        document = {
            "schema_version": SCHEMA_VERSION,
            "clusters": [],
            "volume_summaries": [],
        }
        atomic_json.atomic_write_json(path, document)
        return document


def upsert_cluster(project_root, cluster_id, patch: dict) -> dict:
    """在文件锁内严格读取、合并并校验一个 cluster 记录。"""
    canonical = _canonical_cluster_id(cluster_id)
    if not isinstance(patch, dict):
        raise ClusterSummaryError("cluster patch 必须是 object")
    unknown = sorted(set(patch) - CLUSTER_FIELDS)
    if unknown:
        raise ClusterSummaryError(f"cluster patch 含未知字段: {unknown}")
    if "cluster_id" in patch and _canonical_cluster_id(patch["cluster_id"]) != canonical:
        raise ClusterSummaryError("patch.cluster_id 与目标 cluster_id 不一致")

    path = summary_path(project_root)
    with atomic_json.with_file_lock(path):
        document = load_summary(project_root)
        records = document["clusters"]
        index = next((i for i, row in enumerate(records)
                      if row["cluster_id"] == canonical), None)
        if index is None:
            record = {**copy.deepcopy(patch), "cluster_id": canonical}
            missing = sorted(CLUSTER_FIELDS - set(record))
            if missing:
                raise ClusterSummaryError(
                    f"新 cluster 必须一次提供完整记录，缺少字段: {missing}"
                )
            records.append(record)
        else:
            records[index] = _deep_merge(records[index], patch)
            records[index]["cluster_id"] = canonical
        records.sort(key=lambda row: cluster_lookup.cluster_num(row["cluster_id"]))
        validate_summary(document)
        atomic_json.atomic_write_json(path, document)
        return document


def replace_cluster(project_root, cluster_id, record: dict) -> dict:
    """用完整记录替换目标 cluster，清除旧字段残留。"""
    canonical = _canonical_cluster_id(cluster_id)
    if not isinstance(record, dict):
        raise ClusterSummaryError("cluster record 必须是 object")
    replacement = copy.deepcopy(record)
    replacement["cluster_id"] = canonical
    missing = sorted(CLUSTER_FIELDS - set(replacement))
    extra = sorted(set(replacement) - CLUSTER_FIELDS)
    if missing or extra:
        raise ClusterSummaryError(f"cluster record 字段不完整: missing={missing} extra={extra}")

    path = summary_path(project_root)
    with atomic_json.with_file_lock(path):
        document = load_summary(project_root)
        records = document["clusters"]
        index = next((i for i, row in enumerate(records)
                      if row["cluster_id"] == canonical), None)
        if index is None:
            records.append(replacement)
        else:
            records[index] = replacement
        records.sort(key=lambda row: cluster_lookup.cluster_num(row["cluster_id"]))
        validate_summary(document)
        atomic_json.atomic_write_json(path, document)
        return document


def upsert_volume_summary(project_root, volume: int, record: dict) -> dict:
    """按卷号完整替换卷摘要，并在同一锁内校验整个账本。"""
    if not isinstance(volume, int) or isinstance(volume, bool) or volume < 1:
        raise ClusterSummaryError("volume 必须是正整数")
    if not isinstance(record, dict):
        raise ClusterSummaryError("volume summary 必须是 object")
    replacement = copy.deepcopy(record)
    declared_volume = replacement.get("volume")
    if declared_volume is not None and declared_volume != volume:
        raise ClusterSummaryError("volume summary.volume 与目标卷号不一致")
    replacement["volume"] = volume

    path = summary_path(project_root)
    with atomic_json.with_file_lock(path):
        document = load_summary(project_root)
        rows = document["volume_summaries"]
        index = next((i for i, row in enumerate(rows)
                      if row["volume"] == volume), None)
        if index is None:
            rows.append(replacement)
        else:
            rows[index] = replacement
        rows.sort(key=lambda row: row["volume"])
        validate_summary(document)
        atomic_json.atomic_write_json(path, document)
        return document


__all__ = [
    "initialize_summary", "replace_cluster", "upsert_cluster",
    "upsert_volume_summary",
]
