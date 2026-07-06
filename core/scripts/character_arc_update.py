"""character_arc_update.py — character_arc_state 自动滚动（v19.4 新增）

cluster-save-state 时按当前 cluster / 章范围映射 stages_by_chapter，更新角色的 current_stage_at_ch。

举例：陆衍 stages_by_chapter={1:"lie", 8:"lie_cracking", 14:"want_threatened"}
- ch5 时 → current_stage="lie"（最近一个 ≤5 的 key 是 1）
- ch10 时 → current_stage="lie_cracking"（最近一个 ≤10 的 key 是 8）

用法：python character_arc_update.py <项目> <章节>
退出码: 0 成功 / 1 部分跳过 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import state_cli_guard


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_current_stage(stages_by_chapter: dict, ch: int) -> str:
    """找最近一个 ch_key <= 当前 ch 的 stage。"""
    valid = [(int(k), v) for k, v in stages_by_chapter.items() if str(k).isdigit() and int(k) <= ch]
    if not valid:
        return "pre_start"
    valid.sort()
    return valid[-1][1]


def main():
    state_cli_guard.require_internal("character_arc_update.py")
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    args = ap.parse_args()

    project_root = Path(args.project)
    ch = args.chapter
    arc_path = project_root / "_数据库" / "character_arc_state.json"
    arc = load_json(arc_path, None)
    if arc is None:
        print(f"[SKIP] character_arc_state.json 不存在")
        sys.exit(0)

    # 兼容 character_arc_state.json 三套 schema（对齐 build_manifest.py:1435-1481 /
    # cluster_emergence_engine.py:617-623）：
    #   (A) {"characters": {name: {...}}}  (B) {"arcs": {name: {...}}}  (C) {"characters": [ {...} ]}
    arcs_map = arc.get("arcs")
    if isinstance(arcs_map, dict):
        chars_map = arcs_map
    else:
        raw_chars = arc.get("characters", {})
        if isinstance(raw_chars, list):
            chars_map = {(c.get("name") or c.get("id")): c
                         for c in raw_chars if isinstance(c, dict) and (c.get("name") or c.get("id"))}
        elif isinstance(raw_chars, dict):
            chars_map = raw_chars
        else:
            chars_map = {}

    updated = 0
    for name, data in chars_map.items():
        if not isinstance(data, dict):
            continue
        stages = data.get("stages_by_chapter", {})
        if not stages:
            continue
        stage = find_current_stage(stages, ch)
        old_stage = data.get("current_stage_at_ch")
        if old_stage != f"{ch}:{stage}":
            data["current_stage_at_ch"] = f"{ch}:{stage}"
            data["_last_updated_at_ch"] = ch
            updated += 1
            print(f"  [OK] {name} ch{ch}: stage={stage}")

    if updated:
        arc_path.write_text(json.dumps(arc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[character_arc_update] ch{ch}: {updated} 个角色 stage 已更新")
    else:
        print(f"[OK] ch{ch} 无 stage 变化")
    sys.exit(0)


if __name__ == "__main__":
    main()
