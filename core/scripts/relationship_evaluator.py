"""relationship_evaluator.py — 关系档位强制揭密评估器（v21 R1.5 新增）

借鉴 Stardew Valley 的 heart events：每核心配角有档位事件清单。每章 save-state 检查
当前 关系.json 数值是否到了某个 trigger_at → 满足且 consumed=false → 标 next_chapter_must_reveal[]
让主代理在下章 outline-planner 阶段把 reveal 安排进走向卡。

2026-05-30 修（#5 状态机闭环）：补「写回端」。本脚本是 heart_event consumed 字段的**唯一读处**
（line ~110 `he.get("consumed")` 跳过已揭密），但全仓此前**无任何脚本写 consumed=true**——
关系数值单调累积，trigger_at 一旦满足永久满足 → 每章 save-state 把同一 heart_event 反复重写进
.ensemble_pending_reveals.json → build_manifest 反复要求 writer 重揭已揭的秘密；且
cross_cluster_data_consumption_aggregate.scan_heart_event_consistency 只处理 consumed==true
变成死代码。修：evaluate() 先据 writer 实际产出（_changes.json factual.heart_events_revealed，
schema 已声明的 {event_id, evidence_appeared}）把对应 heart_event 标 consumed=true + consumed_at_ch，
再算 pending —— 闭合「触发一次即消费」状态机（与 群像档.example.json 每个 heart_event 硬写
consumed:false 的设计意图一致）。

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

# 2026-05-30 修：用原子写持久化 群像档.json 的 consumed 写回（与 declarative_data_update 一致）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import atomic_json
    _ATOMIC_WRITE = atomic_json.atomic_write_json
except Exception:  # pragma: no cover — fallback：atomic_json 缺失时退化为裸写，不阻塞流水线
    _ATOMIC_WRITE = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    if _ATOMIC_WRITE is not None:
        _ATOMIC_WRITE(p, data)
    else:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_changes_factual(project_root: Path, ch: int) -> dict:
    """读本章 _changes.json 的 factual 段（cluster 模式下 split_cluster_changes 平铺到每章）。
    路径与 declarative_data_update 一致：章节/第NNN章/第NNN章_changes.json。读不到返回 {}。"""
    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    changes = load_json(changes_path, {})
    if not isinstance(changes, dict):
        return {}
    factual = changes.get("factual", {})
    return factual if isinstance(factual, dict) else {}


def mark_consumed_from_changes(ensemble: dict, factual: dict, ch: int) -> list[dict]:
    """据 writer 实际产出（factual.heart_events_revealed[{event_id, evidence_appeared}]）
    把 群像档 中对应 heart_event 标 consumed=true + consumed_at_ch=ch，闭合状态机。

    幂等：已 consumed 的不重复写（set 非累加）。返回本次新标记的 [{npc, event_id, consumed_at_ch}]。
    设计：event_id 唯一定位（schema pattern ^HE_），不靠数值阈值——揭密由模型判断、脚本只记账。
    """
    revealed = factual.get("heart_events_revealed", []) or []
    if not isinstance(revealed, list):
        return []
    # 收集本章 writer 报告的 event_id（容错：元素是 dict 取 event_id，是裸字符串直接用）
    revealed_ids = set()
    for item in revealed:
        if isinstance(item, dict) and item.get("event_id"):
            revealed_ids.add(item["event_id"])
        elif isinstance(item, str) and item:
            revealed_ids.add(item)
    if not revealed_ids:
        return []

    newly_marked = []
    for npc, npc_data in (ensemble.get("characters") or {}).items():
        if not isinstance(npc_data, dict):
            continue
        for he in npc_data.get("heart_events", []) or []:
            if not isinstance(he, dict):
                continue
            eid = he.get("event_id")
            if eid in revealed_ids and not he.get("consumed", False):
                he["consumed"] = True
                he["consumed_at_ch"] = ch
                newly_marked.append({"npc": npc, "event_id": eid, "consumed_at_ch": ch})
    return newly_marked


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
        # 2026 健壮性：非数值脏值（手工 /db 或弱模型写入 'high' 等）视作未满足而非崩溃
        if not isinstance(cur, (int, float)) or cur < threshold:
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

    # 2026-05-30 修（#5 状态机闭环 · 写回端）：先据本章 writer 实际产出标 consumed，
    # 再算 pending —— 否则数值阈值满足后会反复 pending 已揭的秘密。
    factual = _load_changes_factual(project_root, ch)
    newly_consumed = mark_consumed_from_changes(ensemble, factual, ch)
    if newly_consumed:
        save_json(ensemble_path, ensemble)  # 写回 群像档.json，关闭状态机

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
            if not isinstance(he, dict):
                continue
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
        "newly_consumed_count": len(newly_consumed),
        "newly_consumed": newly_consumed,
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
