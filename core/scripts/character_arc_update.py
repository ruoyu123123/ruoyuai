"""按 cluster 更新 character_arc_state 当前阶段。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import atomic_json
import cluster_lookup
import state_cli_guard


def stage_for_cluster(stages_by_cluster: dict, cluster_id: str) -> str | None:
    return stages_by_cluster.get(cluster_id)


def main() -> int:
    state_cli_guard.require_internal("character_arc_update.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    cluster_id = cluster_lookup.normalize_cluster_id(args.cluster)
    if not cluster_id:
        print(f"[FATAL] 非法 cluster: {args.cluster}", file=sys.stderr)
        return 2
    path = Path(args.project) / "_数据库" / "character_arc_state.json"
    if not path.exists():
        print("[FATAL] character_arc_state.json 不存在", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[FATAL] character_arc_state.json 读取失败: {exc}", file=sys.stderr)
        return 2
    characters = data.get("characters")
    if not isinstance(characters, dict):
        print("[FATAL] character_arc_state.characters 必须是 object", file=sys.stderr)
        return 2
    updated = 0
    for name, arc in characters.items():
        if not isinstance(arc, dict) or not isinstance(arc.get("stages_by_cluster"), dict):
            print(f"[FATAL] character_arc_state.characters.{name}.stages_by_cluster 必须是 object",
                  file=sys.stderr)
            return 2
        stage = stage_for_cluster(arc["stages_by_cluster"], cluster_id)
        if not stage:
            continue
        marker = f"{cluster_id}:{stage}"
        if arc.get("current_stage_at_cluster") != marker:
            arc["current_stage_at_cluster"] = marker
            updated += 1
            print(f"[OK] {name} {marker}")
    if updated:
        atomic_json.atomic_write_json(path, data)
    receipt = {
        "schema_version": "character-arc-update.receipt.v1",
        "cluster_id": cluster_id,
        "completed": True,
        "updated_characters": updated,
    }
    atomic_json.atomic_write_json(
        Path(args.project) / "_数据库" / ".wal" / f"{cluster_id}_character_arc_update.json",
        receipt,
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
