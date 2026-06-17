"""world_evolution_apply_card.py — 走向卡选定 → apply_minor_event 自动触发（v20.1 W7 新增）

主代理在用户选定走向卡后**第一时间**调本脚本：
  1. 读 _数据库/.wal/第<N>章_fate_cards.json
  2. 取 cards[label].ripple_match
  3. 调 world_evolution_engine apply_minor_event(ch, ripple_match)

本脚本是 /cluster-write 入口的"世界先动一格再写"环节（v26 chapter mode 废弃后·apply_minor_event 由 cluster 流程调）。

用法：python world_evolution_apply_card.py <project> <ch> <label>
       <label> = "A" / "B" / "C"

退出码：
  0 - 成功（含 ripple_match 为空时正常跳过）
  1 - 卡片文件不存在
  2 - 标签无效 / world 状态文件缺失
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import world_evolution_engine as wee


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int, help="走向卡对应的章节号（即将开写的那一章）")
    ap.add_argument("label", choices=["A", "B", "C"])
    args = ap.parse_args()

    project_root = Path(args.project)

    # 读卡片文件
    cards_path = project_root / "_数据库" / ".wal" / f"第{args.ch:03d}章_fate_cards.json"
    if not cards_path.exists():
        print(f"[ERROR] 走向卡文件不存在: {cards_path}")
        sys.exit(1)
    try:
        cards_data = json.loads(cards_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] 走向卡 JSON 解析失败: {e}")
        sys.exit(2)

    # 找选定卡
    selected = None
    for c in cards_data.get("cards", []):
        if c.get("label") == args.label:
            selected = c
            break
    if selected is None:
        print(f"[ERROR] 标签 {args.label} 在卡片中不存在 (可选: {[c.get('label') for c in cards_data.get('cards', [])]})")
        sys.exit(2)

    print(f"[选中走向卡] {args.label}: {selected.get('title', '')}")

    ripple_match = selected.get("ripple_match", "").strip()
    if not ripple_match:
        print("[SKIP] 该卡 ripple_match 为空 — 纯叙事推进无世界涟漪")
        sys.exit(0)

    # 检查世界文件
    world_path = project_root / "_数据库" / "世界状态.json"
    rules_path = project_root / "_数据库" / "涟漪规则.json"
    if not world_path.exists() or not rules_path.exists():
        print("[SKIP] 项目未启用世界演化 (世界状态.json/涟漪规则.json 缺失)")
        sys.exit(0)

    # 触发 apply_minor_event
    r = wee.apply_minor_event(project_root, args.ch, ripple_match)
    matched = r.get("matched_rules", [])
    applied = r.get("applied_log", [])
    print(f"[apply_minor_event] trigger=「{ripple_match}」")
    print(f"  matched_rules: {matched}")
    print(f"  applied_count: {len(applied)}")
    for a in applied[:5]:
        target = a.get("target", "?")
        op = a.get("op", "?")
        if op == "delta":
            print(f"    {target}: {a.get('old')} → {a.get('new')} (Δ {a.get('delta')}) [{a.get('reason', '')[:30]}]")
        elif op == "add_thread":
            print(f"    + thread {a.get('thread_id')} ({a.get('npc')})")
        elif op == "spawn":
            print(f"    + spawn {target}")
        else:
            print(f"    {op} {target}")

    if not matched:
        print(f"[WARN] ripple_match='{ripple_match}' 未匹配任何 涟漪规则 → 检查 涟漪规则.json 是否覆盖该卡")
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
