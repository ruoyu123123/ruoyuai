#!/usr/bin/env python3
"""按 cluster 检查角色弧线阶段跳跃、倒退与停滞。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup

ARC_STAGE_ORDER = {
    "lie": 0, "lie_cracking": 1, "want_threatened": 2, "debate": 3,
    "break_into_two": 4, "fun_and_games": 5, "false_victory": 6,
    "midpoint_revelation": 7, "bad_guys_close_in": 8, "all_is_lost": 9,
    "dark_night": 10, "break_into_three": 11, "truth_realized": 12, "final_image": 13,
}


def stage_order(stage: str) -> int:
    return ARC_STAGE_ORDER.get(str(stage).strip().lower(), -1)


def scan(data: dict) -> list[dict]:
    characters = data.get("characters")
    if not isinstance(characters, dict):
        raise ValueError("character_arc_state.characters 必须是 object")
    findings: list[dict] = []
    for character, arc in characters.items():
        stages = arc.get("stages_by_cluster") if isinstance(arc, dict) else None
        if not isinstance(stages, dict):
            continue
        ordered = sorted(
            ((cluster_lookup.cluster_num(cid), cid, stage) for cid, stage in stages.items()),
            key=lambda row: row[0] if row[0] is not None else 10**9,
        )
        ordered = [row for row in ordered if row[0] is not None]
        for previous, current in zip(ordered, ordered[1:]):
            prev_num, prev_cluster, prev_stage = previous
            cur_num, cur_cluster, cur_stage = current
            stage_gap = stage_order(cur_stage) - stage_order(prev_stage)
            cluster_gap = cur_num - prev_num
            if stage_gap >= 4 and cluster_gap < 5:
                findings.append({"severity": "warning", "code": "STAGE_JUMP", "character": character,
                                 "from": {"cluster_id": prev_cluster, "stage": prev_stage},
                                 "to": {"cluster_id": cur_cluster, "stage": cur_stage}})
            if stage_gap < 0:
                findings.append({"severity": "warning", "code": "STAGE_REGRESS", "character": character,
                                 "from": {"cluster_id": prev_cluster, "stage": prev_stage},
                                 "to": {"cluster_id": cur_cluster, "stage": cur_stage}})
            if stage_gap == 0 and cluster_gap >= 3:
                findings.append({"severity": "advisory", "code": "STAGE_STAGNATION",
                                 "character": character, "stage": cur_stage,
                                 "from_cluster": prev_cluster, "to_cluster": cur_cluster})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    args = parser.parse_args()
    path = Path(args.project) / "_数据库" / "character_arc_state.json"
    if not path.exists():
        print("[SKIP] character_arc_state.json 不存在")
        return 0
    try:
        findings = scan(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.project) / "_数据库" / ".cross_cluster_scan" / f"arc_progression_{timestamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"scan_type": "cluster_arc_progression", "findings": findings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
