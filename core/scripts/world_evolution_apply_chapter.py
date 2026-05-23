"""world_evolution_apply_chapter.py — save-state 用 wrapper（v20.1 W6 新增）

每章 save-state 调一次。串行执行：
1. auto_tick(ch)：推进世界一格 + RR_AUTO_TICK 涟漪 + 评估超期 NPC threads
2. apply_fate_event(ch, ME_id)：对 _changes.json.fate_events_triggered[] 中每个 event 触发 fate_event 类涟漪
3. 消费 emergent_opportunities：把 _changes.json.world_state_consumption.emergent_opportunities_consumed[] 中的 EO 标 consumed_by_writer=true
4. spawn_emergent(ch)：检查 emergent_opportunities 是否到期 → 标 expired

输出：保存到 _数据库/.world_evolution/ch{ch}_apply.json（汇总日志）

用法：python world_evolution_apply_chapter.py <project> <ch>

退出码：
  0 - 成功
  1 - 部分失败（有 ripple 没匹配规则）
  2 - 致命（世界状态/规则文件缺失）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import world_evolution_engine as wee


def load_changes(project_root: Path, ch: int) -> dict:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def consume_opportunities(project_root: Path, ch: int, opp_ids: list[str]) -> dict:
    """把 writer 标记消费的 emergent_opportunities 写回 consumed_by_writer=true。"""
    if not opp_ids:
        return {"consumed_count": 0, "missing": []}
    world = wee.load_world(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}
    opps = world.get("emergent_opportunities", [])
    consumed = []
    missing = []
    for oid in opp_ids:
        found = False
        for o in opps:
            if o.get("id") == oid:
                o["consumed_by_writer"] = True
                o["consumed_at_ch"] = ch
                consumed.append(oid)
                found = True
                break
        if not found:
            missing.append(oid)
    wee.save_world(project_root, world)
    return {"consumed_count": len(consumed), "consumed": consumed, "missing": missing}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int)
    args = ap.parse_args()

    project_root = Path(args.project)

    # 预检
    world_path = project_root / "_数据库" / "世界状态.json"
    rules_path = project_root / "_数据库" / "涟漪规则.json"
    if not world_path.exists() or not rules_path.exists():
        print("[SKIP] 世界状态.json 或 涟漪规则.json 不存在 — 项目未启用世界演化")
        sys.exit(0)

    changes = load_changes(project_root, args.ch)
    summary = {"ch": args.ch, "ts": datetime.now().isoformat(timespec="seconds"), "ops": []}

    # 1. auto_tick
    tick_r = wee.tick(project_root, args.ch)
    summary["ops"].append({"op": "tick", "result": tick_r})
    print(f"[tick] ch{args.ch}: matched={tick_r.get('matched_rules')} applied={tick_r.get('applied_count')}")

    # 2. apply_fate_event for each triggered ME_id
    fate_triggered = (changes.get("factual") or {}).get("fate_events_triggered") or []
    fate_results = []
    for ev in fate_triggered:
        eid = ev.get("event_id") if isinstance(ev, dict) else None
        if not eid:
            continue
        r = wee.apply_fate_event(project_root, args.ch, eid)
        fate_results.append({"event_id": eid, "matched_rules": r.get("matched_rules", []), "applied_count": len(r.get("applied_log", []))})
        print(f"[apply_fate_event] {eid}: matched={r.get('matched_rules')} applied={len(r.get('applied_log', []))}")
    summary["ops"].append({"op": "apply_fate_events", "count": len(fate_results), "results": fate_results})

    # 3. 消费 emergent_opportunities
    opp_consumed_ids = (changes.get("factual") or {}).get("world_state_consumption", {}).get("emergent_opportunities_consumed", [])
    if opp_consumed_ids:
        c_r = consume_opportunities(project_root, args.ch, opp_consumed_ids)
        summary["ops"].append({"op": "consume_opportunities", "result": c_r})
        print(f"[consume_opp] consumed={c_r.get('consumed', [])} missing={c_r.get('missing', [])}")

    # 4. spawn_emergent 过期扫描
    spawn_r = wee.spawn_emergent(project_root, args.ch)
    summary["ops"].append({"op": "spawn_emergent_scan", "available": spawn_r.get("available"), "expired_this_ch": spawn_r.get("expired_this_ch")})
    if spawn_r.get("expired_this_ch"):
        print(f"[spawn_emergent] expired={spawn_r['expired_this_ch']}")
    print(f"[spawn_emergent] available_next_ch={[o['id'] for o in spawn_r.get('available', [])]}")

    # 写汇总日志
    out_dir = project_root / "_数据库" / ".world_evolution"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ch{args.ch:03d}_apply.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 世界演化日志: {out_path}")

    # 异常退出码：fate_events_triggered 中存在但 apply 后 matched_rules 为空
    half_apply = any(not r["matched_rules"] for r in fate_results)
    if half_apply:
        print("[WARN] 部分 fate_event 没匹配涟漪规则 → 涟漪规则.json 可能缺定义")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
