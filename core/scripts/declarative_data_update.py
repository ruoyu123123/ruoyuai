"""declarative_data_update.py — 6 类声明式数据 save-state 自动更新（v19.2 新增）

读 _changes.json factual 段的 6 个字段，自动更新对应数据库：
1. relationship_changes      → 关系.json relationships[*].{affinity/trust/fear/respect} 增量
2. faction_standing_changes  → 关系.json faction_standings[key] 增量
3. event_triggers            → 事件表.json pending_events → triggered_events 转移
4. travel_log_added          → 地图.json travel_log[] append
5. secret_status_changes     → 伏笔表.json secrets[*].status 更新
6. knowledge_gained          → 人物卡.json characters[*].knowledge.knows append

设计原则：
- 不撤销已变化的数据（只增量/转移）
- 找不到对应记录 → 警告但不失败
- 干跑模式 --dry-run 预览
- 失败不阻塞 save-state

用法：python declarative_data_update.py <项目路径> <章节号> [--dry-run]
退出码：0 全成功 / 1 部分跳过 / 2 致命
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json


# 2026-05-29 复审修复 [H4]：声明式增量幂等账本。
# relationship_changes / faction_standing_changes / travel_log_added 等是「+= delta / append」
# 类增量，重复 apply 会乘倍/重复。split_cluster_changes 把整 cluster 的 factual 平铺进每章，
# 上层 save_state_updates 已改为「只在 cluster 首章跑一次」，但为防 WAL 断点续跑等场景再次
# 重放，这里再加一层幂等：以 (ch, 增量 payload 的 sha256) 为键记账，相同 payload 第二次进来
# 直接跳过实际写入。账本落 _数据库/.declarative_applied.json。
_APPLIED_LEDGER_NAME = ".declarative_applied.json"


def _payload_fingerprint(rc, fc, et, tl, sc, kg) -> str:
    """对本次声明式增量 payload 做稳定指纹（字段顺序固定 + sort_keys）。"""
    blob = json.dumps(
        {"rc": rc, "fc": fc, "et": et, "tl": tl, "sc": sc, "kg": kg},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：裸写 → 原子写（atomic_write_json 内部已 mkdir + fsync）
    atomic_json.atomic_write_json(p, data)


def update_relationships(rel_data: dict, changes: list[dict], ch: int) -> list[str]:
    logs = []
    rel_list = rel_data.setdefault("relationships", [])
    for rc in changes:
        f, t, field, delta = rc.get("from"), rc.get("to"), rc.get("field"), rc.get("delta")
        if not all([f, t, field, delta is not None]):
            logs.append(f"  [SKIP] relationship_change 缺字段: {rc}")
            continue
        found = False
        for r in rel_list:
            if r.get("from") == f and r.get("to") == t:
                old = r.get(field, 0)
                r[field] = old + delta
                r["_last_modified_at_ch"] = ch
                logs.append(f"  [OK] {f}→{t} {field}: {old} → {r[field]} ({rc.get('trigger', '')[:40]})")
                found = True
                break
        if not found:
            # 新关系
            new_rel = {"from": f, "to": t, "affinity": 0, "trust": 0, "fear": 0, "respect": 0, "_first_added_ch": ch}
            new_rel[field] = delta
            new_rel["notes"] = rc.get("trigger", "")
            rel_list.append(new_rel)
            logs.append(f"  [ADD] {f}→{t} {field}={delta} (新关系)")
    return logs


def update_faction_standings(rel_data: dict, changes: list[dict], ch: int) -> list[str]:
    logs = []
    standings = rel_data.setdefault("faction_standings", {})
    for fc in changes:
        key, delta = fc.get("factions"), fc.get("delta")
        if not key or delta is None:
            logs.append(f"  [SKIP] faction_change 缺字段: {fc}")
            continue
        old = standings.get(key, 0)
        standings[key] = old + delta
        logs.append(f"  [OK] {key}: {old} → {standings[key]} ({fc.get('trigger', '')[:40]})")
    return logs


def update_events(events_data: dict, triggers: list[dict], ch: int) -> list[str]:
    logs = []
    pending = events_data.setdefault("pending_events", [])
    triggered = events_data.setdefault("triggered_events", [])
    for ev in triggers:
        eid = ev.get("event_id")
        if not eid:
            logs.append(f"  [SKIP] event_trigger 缺 event_id: {ev}")
            continue
        moved = None
        for i, p in enumerate(pending):
            if p.get("id") == eid:
                moved = pending.pop(i)
                break
        if moved:
            moved["status"] = "triggered"
            moved["triggered_at_ch"] = ch
            moved["result_note"] = ev.get("note", "")
            triggered.append(moved)
            logs.append(f"  [OK] {eid} pending → triggered ({ev.get('note', '')[:40]})")
        else:
            # 不在 pending，但 writer 报触发——可能是新事件
            triggered.append({"id": eid, "status": "triggered", "triggered_at_ch": ch, "result_note": ev.get("note", "")})
            logs.append(f"  [ADD] {eid} 新触发事件（未在 pending）")
    return logs


def update_travel_log(map_data: dict, log_entries: list[dict], ch: int) -> list[str]:
    logs = []
    travel = map_data.setdefault("travel_log", [])
    for entry in log_entries:
        char = entry.get("character")
        if not char:
            logs.append(f"  [SKIP] travel_log_add 缺 character: {entry}")
            continue
        travel.append({
            "ch": ch,
            "character": char,
            "from": entry.get("from", ""),
            "to": entry.get("to", ""),
            "time": entry.get("time", ""),
        })
        logs.append(f"  [OK] ch{ch} {char}: {entry.get('from', '')} → {entry.get('to', '')}")
    return logs


def update_secrets(fs_data: dict, changes: list[dict], ch: int) -> list[str]:
    logs = []
    secrets = fs_data.setdefault("secrets", [])
    for sc in changes:
        sid = sc.get("secret_id")
        new_status = sc.get("new_status")
        if not sid or not new_status:
            logs.append(f"  [SKIP] secret_change 缺字段: {sc}")
            continue
        found = False
        for s in secrets:
            if s.get("id") == sid:
                old = s.get("status", "hidden")
                s["status"] = new_status
                s["_last_status_change_ch"] = ch
                if "leaked_to" in sc:
                    s.setdefault("known_by", []).extend(sc["leaked_to"])
                    s["known_by"] = list(set(s["known_by"]))
                logs.append(f"  [OK] secret {sid}: {old} → {new_status} ({sc.get('trigger', '')[:40]})")
                found = True
                break
        if not found:
            logs.append(f"  [SKIP] secret {sid} 找不到")
    return logs


def update_knowledge(cards_data: dict, gains: list[dict], ch: int) -> list[str]:
    logs = []
    for kg in gains:
        char_name = kg.get("character")
        fact = kg.get("fact")
        if not char_name or not fact:
            logs.append(f"  [SKIP] knowledge_gain 缺字段: {kg}")
            continue
        found = False
        for c in cards_data.get("characters", []):
            if c.get("name") == char_name or c.get("id") == char_name:
                k = c.setdefault("knowledge", {})
                knows = k.setdefault("knows", [])
                if fact not in knows:
                    knows.append(fact)
                # 从 will_learn 移除（如果有）
                wl = k.setdefault("will_learn", [])
                k["will_learn"] = [w for w in wl if w.get("fact") != fact]
                logs.append(f"  [OK] {char_name} 学到: {fact[:40]}")
                found = True
                break
        if not found:
            logs.append(f"  [SKIP] knowledge_gain 找不到角色: {char_name}")
    return logs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    ch = args.chapter

    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    if not changes_path.exists():
        print(f"[SKIP] ch{ch} _changes.json 不存在")
        sys.exit(0)
    changes = load_json(changes_path, {})
    factual = changes.get("factual", {})

    all_logs = []
    all_logs.append(f"[declarative_data_update] ch{ch}")

    # 先抽出 6 类增量字段（供幂等指纹 + 后续 apply）
    rc = factual.get("relationship_changes", [])
    fc = factual.get("faction_standing_changes", [])
    et = factual.get("event_triggers", [])
    tl = factual.get("travel_log_added", [])
    sc = factual.get("secret_status_changes", [])
    kg = factual.get("knowledge_gained", [])

    if not any([rc, fc, et, tl, sc, kg]):
        print(f"[OK] ch{ch} 6 类声明式字段都为空，无更新")
        sys.exit(0)

    # 2026-05-29 复审修复 [H4]：幂等去重——相同 (ch, payload) 已 apply 过则跳过，
    # 防 cluster 逐章重放 / WAL 续跑把增量乘倍。dry-run 不记账。
    fp = _payload_fingerprint(rc, fc, et, tl, sc, kg)
    ledger_path = project_root / "_数据库" / _APPLIED_LEDGER_NAME
    ledger = load_json(ledger_path, {}) or {}
    ledger_key = f"ch{ch}"
    if not args.dry_run and ledger.get(ledger_key, {}).get("fingerprint") == fp:
        print(f"[OK] ch{ch} 声明式增量已应用过（幂等跳过 · fp={fp[:12]}）")
        sys.exit(0)

    # 1. relationships
    if rc or fc:
        rel_path = project_root / "_数据库" / "关系.json"
        rel_data = load_json(rel_path, {"relationships": [], "faction_standings": {}})
        all_logs.append("\n=== relationships ===")
        all_logs.extend(update_relationships(rel_data, rc, ch))
        all_logs.append("\n=== faction_standings ===")
        all_logs.extend(update_faction_standings(rel_data, fc, ch))
        if not args.dry_run:
            save_json(rel_path, rel_data)

    # 2. events（et/tl/sc/kg 已在函数顶部抽出 · 2026-05-29 复审修复 [H4]）
    if et:
        ev_path = project_root / "_数据库" / "事件表.json"
        ev_data = load_json(ev_path, {"pending_events": [], "triggered_events": [], "recurring_events": []})
        all_logs.append("\n=== event_triggers ===")
        all_logs.extend(update_events(ev_data, et, ch))
        if not args.dry_run:
            save_json(ev_path, ev_data)

    # 3. travel_log
    if tl:
        map_path = project_root / "_数据库" / "地图.json"
        map_data = load_json(map_path, {"travel_log": []})
        all_logs.append("\n=== travel_log ===")
        all_logs.extend(update_travel_log(map_data, tl, ch))
        if not args.dry_run:
            save_json(map_path, map_data)

    # 4. secrets
    if sc:
        fs_path = project_root / "_数据库" / "伏笔表.json"
        fs_data = load_json(fs_path, {"secrets": []})
        all_logs.append("\n=== secrets ===")
        all_logs.extend(update_secrets(fs_data, sc, ch))
        if not args.dry_run:
            save_json(fs_path, fs_data)

    # 5. knowledge
    if kg:
        cards_path = project_root / "_数据库" / "人物卡.json"
        cards_data = load_json(cards_path, {"characters": []})
        all_logs.append("\n=== knowledge ===")
        all_logs.extend(update_knowledge(cards_data, kg, ch))
        if not args.dry_run:
            save_json(cards_path, cards_data)

    # 全空已在函数顶部提前返回，此处必有实际增量。
    for log in all_logs:
        print(log)
    if args.dry_run:
        print("\n[DRY-RUN] 未实际写入")
        sys.exit(0)

    # 2026-05-29 复审修复 [H4]：apply 成功后记账（幂等键 = 本章增量 payload 指纹），
    # 下次相同 payload 进来直接跳过，防乘倍。
    ledger[ledger_key] = {
        "fingerprint": fp,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
    }
    atomic_json.atomic_write_json(ledger_path, ledger)
    sys.exit(0)


if __name__ == "__main__":
    main()
