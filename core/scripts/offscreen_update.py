"""offscreen_update.py — save-state offscreen 状态更新（v19.2 新增）

读 _changes.json 的 self_eval.offscreen_actions_executed，把对应 character 的
offscreen.actions[action_index].done 标为 true（仅当 completed_fully=true）。

设计原则：
- 不撤销已 done 的 action（只能从 false → true，不能反向）
- 找不到 character / action_index 越界 → 警告但不失败
- 干跑模式（--dry-run）可预览将做的改动
- 失败不阻塞 save-state 主流水线

用法：
    python offscreen_update.py <项目路径> <章节号> [--dry-run]

退出码: 0 成功 / 1 部分跳过 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    ch = args.chapter

    # 读 _changes.json
    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    if not changes_path.exists():
        print(f"[SKIP] ch{ch} _changes.json 不存在，跳过 offscreen-update")
        sys.exit(0)

    changes = load_json(changes_path, {})
    executed = changes.get("self_eval", {}).get("offscreen_actions_executed", [])
    if not executed:
        print(f"[OK] ch{ch} offscreen_actions_executed 为空，无需更新")
        sys.exit(0)

    # 读人物卡
    cards_path = project_root / "_数据库" / "人物卡.json"
    cards = load_json(cards_path, None)
    if cards is None:
        print(f"[FATAL] 人物卡不存在或损坏: {cards_path}", file=sys.stderr)
        sys.exit(2)

    # 建索引
    char_index = {}
    for i, c in enumerate(cards.get("characters", [])):
        name = c.get("name")
        cid = c.get("id")
        if name:
            char_index[name] = i
        if cid and cid != name:
            char_index[cid] = i

    updates_planned = []
    skipped = []

    for ex in executed:
        char_name = ex.get("character", "")
        action_idx = ex.get("action_index")
        completed = ex.get("completed_fully", False)

        if action_idx is None:
            skipped.append({"character": char_name, "reason": "缺 action_index"})
            continue
        if not completed:
            skipped.append({"character": char_name, "action_index": action_idx, "reason": "completed_fully=false 不标 done"})
            continue
        if char_name not in char_index:
            skipped.append({"character": char_name, "reason": "人物卡找不到该角色"})
            continue

        ci = char_index[char_name]
        actions = cards["characters"][ci].get("offscreen", {}).get("actions", [])
        if action_idx >= len(actions):
            skipped.append({"character": char_name, "action_index": action_idx, "reason": f"action_index 越界（共 {len(actions)} 条）"})
            continue

        cur_done = actions[action_idx].get("done", False)
        if cur_done:
            skipped.append({"character": char_name, "action_index": action_idx, "reason": "already done"})
            continue

        updates_planned.append({
            "character": char_name,
            "action_index": action_idx,
            "action_preview": actions[action_idx].get("action", "")[:60],
            "evidence": ex.get("evidence", "")[:60],
        })

    print(f"[offscreen_update] ch{ch}: 计划更新 {len(updates_planned)} 条 / 跳过 {len(skipped)} 条")
    for u in updates_planned:
        print(f"  [PLAN] {u['character']} action[{u['action_index']}] done=true  ({u['action_preview']}...)")
    for s in skipped:
        print(f"  [SKIP] {s.get('character')}: {s.get('reason')}")

    if args.dry_run:
        print(f"[DRY-RUN] 未实际写入，使用 --dry-run=false 或省略该参数执行更新")
        sys.exit(0)

    # 实际写入
    if updates_planned:
        for u in updates_planned:
            ci = char_index[u["character"]]
            cards["characters"][ci]["offscreen"]["actions"][u["action_index"]]["done"] = True
            cards["characters"][ci]["offscreen"]["actions"][u["action_index"]]["_done_at_ch"] = ch
        cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 人物卡已更新: {cards_path}")

    if skipped:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
