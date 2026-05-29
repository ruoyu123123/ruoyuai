"""relationship_evaluator.py — 关系档位强制揭密评估器（v21 R1.5 新增）

借鉴 Stardew Valley 的 heart events：每核心配角有档位事件清单。每章 save-state 检查
当前 关系.json 数值是否到了某个 trigger_at → 满足且 consumed=false → 标 next_chapter_must_reveal[]
让主代理在下章 outline-planner 阶段把 reveal 安排进走向卡。

用法：python relationship_evaluator.py <project> [--ch N]
退出码: 0 健康 / 1 有 heart_event 待揭密 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_relationship_to(rels: list[dict], from_char: str, to_char: str) -> dict:
    for r in rels:
        if r.get("from") == from_char and r.get("to") == to_char:
            return r
    return {}


def trigger_satisfied(trigger_at: dict, rel: dict) -> bool:
    """trigger_at 形如 {'trust': 9, 'affinity': 6}, 全部条件 ≥ 才算满足。"""
    if not trigger_at:
        return False
    for dim, threshold in trigger_at.items():
        cur = rel.get(dim)
        if cur is None or cur < threshold:
            return False
    return True


def get_protagonist(project_root: Path) -> str | None:
    """2026-05-29 cluster 化：从 人物卡.json 读 role==主角/protagonist 的角色名，
    取代旧硬编码 "陆衍"。兼容两种 人物卡 形态：
      · {"characters": [{"name": ..., "role": "主角"}]}（build_manifest 主形态）
      · {name: {"role": "protagonist"/"is_protagonist": true}}（audit_hub 形态）
    读不到再 fallback 到第一个角色。
    """
    cards = load_json(project_root / "_数据库" / "人物卡.json", None)
    if not cards:
        return None
    # 形态一：{"characters": [...]}
    if isinstance(cards, dict) and isinstance(cards.get("characters"), list):
        chars = cards["characters"]
        for c in chars:
            if isinstance(c, dict) and c.get("role") in ("主角", "protagonist"):
                return c.get("name")
            if isinstance(c, dict) and c.get("is_protagonist"):
                return c.get("name")
        # fallback：第一个有名字的角色
        for c in chars:
            if isinstance(c, dict) and c.get("name"):
                return c.get("name")
        return None
    # 形态二：{name: {...}}
    if isinstance(cards, dict):
        for name, info in cards.items():
            if isinstance(info, dict) and (
                info.get("role") in ("主角", "protagonist") or info.get("is_protagonist")
            ):
                return name
        return next(iter(cards.keys()), None)
    return None


def evaluate(project_root: Path, ch: int) -> dict:
    ensemble_path = project_root / "_数据库" / "群像档.json"
    rels_path = project_root / "_数据库" / "关系.json"
    if not ensemble_path.exists():
        return {"skipped": "群像档.json 不存在"}
    ensemble = load_json(ensemble_path, {})
    rels_data = load_json(rels_path, {"relationships": []})
    rels = rels_data.get("relationships", [])

    # 2026-05-29 cluster 化：从 人物卡.json 读主角名，取代旧硬编码 "陆衍"。
    protagonist = get_protagonist(project_root)
    if not protagonist:
        return {"skipped": "人物卡.json 无主角，无法评估关系揭密"}
    pending_reveals = []
    for npc, npc_data in (ensemble.get("characters") or {}).items():
        rel = get_relationship_to(rels, protagonist, npc)
        if not rel:
            continue
        for he in npc_data.get("heart_events", []):
            if he.get("consumed", False):
                continue
            if trigger_satisfied(he.get("trigger_at", {}), rel):
                pending_reveals.append({
                    "npc": npc,
                    "event_id": he.get("event_id"),
                    "tier_label": he.get("tier_label"),
                    "reveal": he.get("reveal"),
                    "physical_evidence": he.get("physical_evidence", []),
                    "current_relationship": rel,
                    "trigger_at": he.get("trigger_at"),
                })

    # 持久化 pending 标记 → 下次 build_manifest 注入
    out_path = project_root / "_数据库" / ".ensemble_pending_reveals.json"
    save_json(out_path, {
        "ch": ch,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "pending_reveals": pending_reveals,
    })

    return {
        "ch": ch,
        "pending_reveals_count": len(pending_reveals),
        "pending_reveals": pending_reveals,
        "_persisted_to": str(out_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not (project_root / "_数据库" / "群像档.json").exists():
        print("[SKIP] 群像档.json 不存在 — 项目未启用群像档系统")
        sys.exit(0)

    ch = args.ch
    if ch is None or args.auto:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        ch = chapters[-1]

    r = evaluate(project_root, ch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(1 if r.get("pending_reveals_count", 0) > 0 else 0)


if __name__ == "__main__":
    main()
