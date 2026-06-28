"""character_lazy_spawn.py — 涌现角色 lazy spawn 处理（v20 F7 新增）

writer 写正文时遇到新角色 → 在 _changes.json.new_entities.characters 添加：
  {"name": "X", "role": "...", "_propose_emerged": true/false}

本脚本 save-state 时处理：
- _propose_emerged=true → 扶正为 角色池.emerged_characters[]
- _propose_emerged=false → 进 角色池.extras[]
- 同时在 人物卡.json 加最小骨架（仅 name+role+first_appear_ch，详细 voice_pack 由 distill-character 后续蒸馏）

用法：python character_lazy_spawn.py <project> <ch> [--dry-run]
退出码: 0 成功 / 1 部分跳过
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 2026-05-29 修 章号当cluster号：章号 ⇄ cluster_id 反查走单一权威工具
sys.path.insert(0, str(Path(__file__).parent))
import cluster_lookup


def _resolve_cluster(project_root: Path, ch: int) -> tuple[str, bool]:
    """章号反查真实 cluster_id。返回 (cluster_id, inferred)。

    反查不到 → fallback 到 normalize_cluster_id(ch) 并标 inferred=True
    （表示按章号推断、不可信，调用方应给记录加 `_cluster_inferred`）。
    """
    cid = cluster_lookup.ch_to_cluster_id(project_root, ch)
    if cid:
        return cid, False
    return cluster_lookup.infer_cluster_id_by_chapter(ch), True  # 北极星①：兜底集中到 cluster_lookup 唯一出处


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    ch = args.ch

    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    changes = load_json(changes_path, {})
    # 🔴 2026-06-28：new_entities 双形兼容（consumer-tolerant）。gen_writer CHANGES prompt 让 writer 产
    # factual.new_entities=[{name,role}]（list 形式）·旧契约是 {"characters":[...]}（dict 形式）。
    # 原只读 dict.get("characters") → list 形式撞 AttributeError 崩（cluster_006 实测 7 章全崩）。
    _ne = changes.get("factual", {}).get("new_entities", [])
    if isinstance(_ne, dict):
        new_chars = _ne.get("characters", [])
    elif isinstance(_ne, list):
        new_chars = _ne
    else:
        new_chars = []
    new_chars = [c for c in new_chars if isinstance(c, dict)]  # 过滤模型可能混入的字符串项
    if not new_chars:
        print(f"[OK] ch{ch} 无新角色 spawn")
        sys.exit(0)

    pool_path = project_root / "_数据库" / "角色池.json"
    pool = load_json(pool_path, {
        "_schema": "character_pool_v20_lazy_spawn",
        "core_characters": [],
        "emerged_characters": [],
        "extras": [],
        "spawn_rules": {
            "writer_can_spawn_extras": True,
            "writer_can_spawn_emerged_with_note": True,
        },
    })
    cards_path = project_root / "_数据库" / "人物卡.json"
    cards = load_json(cards_path, {"characters": []})

    existing_ids = set()
    for c in pool.get("core_characters", []):
        existing_ids.add(c.get("id") or c.get("name"))
    for c in pool.get("emerged_characters", []):
        existing_ids.add(c.get("id") or c.get("name"))
    for c in pool.get("extras", []):
        existing_ids.add(c.get("id") or c.get("name"))

    promoted_emerged = []
    added_extras = []
    for nc in new_chars:
        name = nc.get("name") or nc.get("id")
        if not name or name in existing_ids:
            continue
        propose = nc.get("_propose_emerged", False)
        role = nc.get("role", "")
        if propose:
            pool["emerged_characters"].append({
                "id": name, "tier": "emerged",
                "role": role,
                "spawned_at_ch": ch,
                "promoted_to_emerged_at_ch": ch,
                "_note": "由 writer ch{} 提案 → save-state 扶正".format(ch),
            })
            promoted_emerged.append(name)
            # 同步加最小人物卡骨架
            if not any(c.get("name") == name or c.get("id") == name for c in cards.get("characters", [])):
                # 2026-05-29 修 章号当cluster号：first_appear_cluster / growth_arc.cluster 由 ch 反查真实 cluster_id
                appear_cid, appear_inferred = _resolve_cluster(project_root, ch)
                card_rec = {
                    "id": name, "name": name,
                    "name_aliases": [],
                    "role": role,
                    "appearance": "（待蒸馏）",
                    "personality": "（待蒸馏）",
                    "voice_pack": {"rhythm": "（待蒸馏）", "banned_phrases": [], "catchphrase": [], "style": "", "style_samples": [], "anti_samples": []},
                    "locked_facts": [],
                    "knowledge": {"knows": [], "doesnt_know": [], "will_learn": []},
                    "growth_arc": [],
                    "decision_patterns": [],
                    "arc": "（待蒸馏）",
                    "status": "活跃",
                    # v2 cluster 化（2026-05-28）：纯 cluster 模式
                    "first_appear_cluster": appear_cid,
                    "_lazy_spawned": True,
                }
                if appear_inferred:
                    card_rec["_cluster_inferred"] = True
                # 2026-05-29 cluster 化：蒸馏建议 cluster 走 ch 反查真实 cluster_id，
                # 取代旧残留 f"cluster_{ch+5:03d}"（章号当 cluster 号，正是 cluster_lookup 要消灭的模式）。
                distill_cid, distill_inferred = _resolve_cluster(project_root, ch + 5)
                card_rec["_distill_recommended_cluster"] = distill_cid
                if distill_inferred:
                    card_rec["_distill_recommended_cluster_inferred"] = True
                cards["characters"].append(card_rec)
        else:
            pool["extras"].append({"id": name, "ch": ch, "role": role})
            added_extras.append(name)

    if args.dry_run:
        print(f"[DRY-RUN] would promote emerged: {promoted_emerged}")
        print(f"[DRY-RUN] would add extras: {added_extras}")
        sys.exit(0)

    save_json(pool_path, pool)
    save_json(cards_path, cards)

    print(f"[character_lazy_spawn] ch{ch}: promoted {len(promoted_emerged)} emerged, added {len(added_extras)} extras")
    for name in promoted_emerged:
        print(f"  [EMERGED] {name} → 角色池 + 人物卡骨架（建议 ch{ch+5} 调 distill-character 蒸馏）")
    for name in added_extras:
        print(f"  [EXTRA] {name}")
    sys.exit(0)


if __name__ == "__main__":
    main()
