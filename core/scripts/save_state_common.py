"""save-state 子模块共享的严格 JSON、cluster id 和终态合同。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import atomic_json
import cluster_lookup


TERMINAL_STATE_VALUES = frozenset({"consumed", "resolved", "answered"})
_TERMINAL_COMPANION_KEYS = (
    "consumed_at_cluster",
    "resolved_at_cluster",
    "answered_at_cluster",
    "_consumed_by",
    "_resolved_by",
)


def require_cluster_id(cluster_key) -> str:
    cid = cluster_lookup.normalize_cluster_id(cluster_key)
    if cid is None:
        raise RuntimeError(f"非法 cluster_key: {cluster_key!r}")
    return cid


def load_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 无法读取: {path}: {exc}") from exc


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json.atomic_write_json(path, data)


def wal_dir(root: Path) -> Path:
    directory = Path(root) / "_数据库" / ".wal"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def strip_terminal_state_payload(payload, path="$"):
    """剥离抽取载荷中未经确定性写入层验证的终态声明。"""
    stripped = 0
    details = []
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, str) and status.strip().lower() in TERMINAL_STATE_VALUES:
            payload.pop("status", None)
            for key in _TERMINAL_COMPANION_KEYS:
                payload.pop(key, None)
            stripped += 1
            details.append({"path": path, "field": "status", "value": status})
        if payload.get("resolved") is True:
            payload.pop("resolved", None)
            for key in _TERMINAL_COMPANION_KEYS:
                payload.pop(key, None)
            stripped += 1
            details.append({"path": path, "field": "resolved", "value": True})
        if payload.get("terminal") is True:
            payload.pop("terminal", None)
            stripped += 1
            details.append({"path": path, "field": "terminal", "value": True})
        for key in ("kind", "type"):
            if payload.get(key) == "terminal":
                payload.pop(key, None)
                stripped += 1
                details.append({"path": path, "field": key, "value": "terminal"})
        answered = payload.get("answered")
        if isinstance(answered, list) and answered:
            payload["answered"] = []
            stripped += len(answered)
            details.append({
                "path": path,
                "field": "answered",
                "value": [row.get("qid") if isinstance(row, dict) else row for row in answered],
            })
        for key, value in list(payload.items()):
            count, nested = strip_terminal_state_payload(value, f"{path}.{key}")
            stripped += count
            details.extend(nested)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            count, nested = strip_terminal_state_payload(value, f"{path}[{index}]")
            stripped += count
            details.extend(nested)
    return stripped, details


def record_terminal_contract(db: Path, cid: str, section: str, payload: dict) -> None:
    """覆盖写入当前 cluster 某终态合同分区的审计结果。"""
    path = Path(db) / ".wal" / f"{cid}_terminal_contract.json"
    document = load_json(path, None)
    if document is None:
        document = {"schema_version": 1, "cluster_id": cid, "sections": {}}
    if not isinstance(document, dict) or document.get("cluster_id") != cid:
        raise ValueError(f"终态合同 WAL 无效: {path}")
    sections = document.get("sections")
    if not isinstance(sections, dict):
        raise ValueError(f"终态合同 sections 无效: {path}")
    entry = dict(payload)
    entry["at"] = datetime.now().isoformat(timespec="seconds")
    sections[section] = entry
    save_json(path, document)
