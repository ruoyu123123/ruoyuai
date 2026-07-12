#!/usr/bin/env python3
"""按 cluster changes 更新人物卡中的 offscreen action 状态。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import atomic_json
import cluster_lookup
import state_cli_guard


class OffscreenContractError(ValueError):
    """offscreen changes 或人物卡不符合状态合同。"""


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OffscreenContractError(f"必需文件不存在: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OffscreenContractError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OffscreenContractError(f"JSON 顶层必须是 object: {path}")
    return value


def _character_index(cards: dict) -> dict[str, dict]:
    characters = cards.get("characters")
    if not isinstance(characters, list) or not all(isinstance(item, dict) for item in characters):
        raise OffscreenContractError("人物卡.characters 必须是 object array")
    index: dict[str, dict] = {}
    for position, character in enumerate(characters):
        aliases = [character.get("id"), character.get("name")]
        if not any(isinstance(alias, str) and alias for alias in aliases):
            raise OffscreenContractError(f"人物卡.characters[{position}] 缺 id/name")
        for alias in aliases:
            if not isinstance(alias, str) or not alias:
                continue
            if alias in index and index[alias] is not character:
                raise OffscreenContractError(f"人物卡角色别名重复: {alias}")
            index[alias] = character
    return index


def apply(project: Path, cluster: str, *, dry_run: bool = False) -> dict:
    cluster_id = cluster_lookup.normalize_cluster_id(cluster)
    if not cluster_id:
        raise OffscreenContractError(f"非法 cluster: {cluster}")
    changes_path = project / "章节" / f"{cluster_id}_draft" / f"{cluster_id}_changes.json"
    changes = _read_object(changes_path)
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise OffscreenContractError("cluster changes.self_eval 必须是 object")
    executed = self_eval.get("offscreen_actions_executed", [])
    if not isinstance(executed, list) or not all(isinstance(item, dict) for item in executed):
        raise OffscreenContractError("offscreen_actions_executed 必须是 object array")

    cards_path = project / "_数据库" / "人物卡.json"
    cards = _read_object(cards_path)
    index = _character_index(cards)
    applied = 0
    already_done = 0
    pending = 0
    for row_number, execution in enumerate(executed):
        character_key = execution.get("character")
        action_index = execution.get("action_index")
        completed = execution.get("completed_fully")
        if not isinstance(character_key, str) or not character_key:
            raise OffscreenContractError(f"offscreen_actions_executed[{row_number}].character 无效")
        if not isinstance(action_index, int) or isinstance(action_index, bool) or action_index < 0:
            raise OffscreenContractError(f"offscreen_actions_executed[{row_number}].action_index 无效")
        if not isinstance(completed, bool):
            raise OffscreenContractError(f"offscreen_actions_executed[{row_number}].completed_fully 必须是 bool")
        character = index.get(character_key)
        if character is None:
            raise OffscreenContractError(f"offscreen action 引用未知角色: {character_key}")
        offscreen = character.get("offscreen")
        actions = offscreen.get("actions") if isinstance(offscreen, dict) else None
        if not isinstance(actions, list) or not all(isinstance(item, dict) for item in actions):
            raise OffscreenContractError(f"角色 {character_key} 的 offscreen.actions 必须是 object array")
        if action_index >= len(actions):
            raise OffscreenContractError(
                f"角色 {character_key} 的 action_index 越界: {action_index}/{len(actions)}"
            )
        action = actions[action_index]
        if not completed:
            pending += 1
        elif action.get("done") is True:
            already_done += 1
        else:
            action["done"] = True
            action["_done_at_cluster"] = cluster_id
            action.pop("_done_at_ch", None)
            applied += 1

    receipt = {
        "schema_version": "offscreen-update.receipt.v1",
        "cluster_id": cluster_id,
        "completed": True,
        "applied": applied,
        "already_done": already_done,
        "pending": pending,
    }
    if not dry_run:
        atomic_json.atomic_write_json(cards_path, cards)
        atomic_json.atomic_write_json(
            project / "_数据库" / ".wal" / f"{cluster_id}_offscreen_update.json",
            receipt,
        )
    return receipt


def main() -> int:
    state_cli_guard.require_internal("offscreen_update.py")
    parser = argparse.ArgumentParser(description="cluster offscreen 状态更新")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        receipt = apply(Path(args.project), args.cluster, dry_run=args.dry_run)
    except (OffscreenContractError, OSError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

