"""读取 cluster 级客观状态 artifact 与已回库账本。"""
from __future__ import annotations

import json
from pathlib import Path

import atomic_json
import cluster_lookup


def load_json(path: Path, default=None):
    return atomic_json.load_json(path, default=default)


def normalize_cluster_id(value) -> str | None:
    return cluster_lookup.normalize_cluster_id(value)


def cluster_id_for_chapter(project_root: Path, chapter: int) -> str | None:
    return cluster_lookup.ch_to_cluster_id(project_root, chapter)


def state_delta_path(project_root: Path, cluster_id: str) -> Path:
    return Path(project_root) / "_数据库" / ".wal" / f"{cluster_id}_state_delta.json"


def archive_path(project_root: Path, cluster_id: str) -> Path:
    return Path(project_root) / "_数据库" / ".wal" / f"{cluster_id}_archive.json"


def load_state_delta(project_root: Path, cluster_id: str) -> dict:
    cid = normalize_cluster_id(cluster_id)
    if not cid:
        return {}
    data = load_json(state_delta_path(project_root, cid), {})
    return data if isinstance(data, dict) and data.get("cluster_id") == cid else {}


def load_archive(project_root: Path, cluster_id: str) -> dict:
    cid = normalize_cluster_id(cluster_id)
    if not cid:
        return {}
    data = load_json(archive_path(project_root, cid), {})
    if not isinstance(data, dict):
        return {}
    archive_cid = normalize_cluster_id(data.get("cluster_id"))
    return data if archive_cid in (None, cid) else {}


def load_cluster_ledger(project_root: Path, cluster_id: str) -> dict:
    cid = normalize_cluster_id(cluster_id)
    if not cid:
        return {}
    data = load_json(Path(project_root) / "_数据库" / "事件簇.json", {}) or {}
    for cluster in data.get("clusters", []) or []:
        if isinstance(cluster, dict) and normalize_cluster_id(cluster.get("cluster_id")) == cid:
            return cluster
    return {}


def iter_completed_clusters(project_root: Path, last_n: int | None = None):
    data = load_json(Path(project_root) / "_数据库" / "故事块摘要.json", {}) or {}
    records = [
        row for row in data.get("clusters", []) or []
        if isinstance(row, dict) and normalize_cluster_id(row.get("cluster_id"))
        and row.get("status") != "candidate"
    ]
    records.sort(key=lambda row: cluster_lookup.cluster_num(row.get("cluster_id")))
    if last_n is not None and last_n > 0:
        records = records[-last_n:]
    for row in records:
        cid = normalize_cluster_id(row.get("cluster_id"))
        yield cid, row
