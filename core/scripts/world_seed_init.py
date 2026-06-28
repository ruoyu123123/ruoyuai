#!/usr/bin/env python3
"""world_seed_init.py — 世界演化初始条件幂等播种器（SYS-1 + C02 · 2026-06-27）

让 world_evolution_engine 从「空转」复活。

背景（SYS-1 根因）：outline 阶段 scaffold 把 涟漪规则.json / 世界状态.json 建成空骨架
（ripple_rules=[] · factions_state={} · protagonist_state={}），于是每章 tick 都「0 规则触发，
0 涟漪落地」——世界永不演化（北极星② 涟漪核心 + 北极星③ 大势已定 软牵引 全部静默失效）。

本播种器从**已有创意产物**（大势卡 ME 池 / 群像档势力 / character_arc_state 主角+角色）确定性
reshape 出**最小起始集**写回 涟漪规则.json + 世界状态.json：

  ① ≥1 条基线 auto_tick 规则（trigger_type=auto_tick · trigger_match=every_chapter ·
     ripples = 推进 NPC thread(evaluate_completion) + 1 条 day 推进(advance) + 轻量 narrative drift）
  ② 每个 ME_id 一条 fate_event 规则（trigger_match=ME_id · narrative 型 · 交模型解读）
  ③ 走向卡关键词（势力名）→ minor_event 规则（narrative 型 · 走向卡有持续后果）
  + 为每势力播 {power,stability,wealth} 基线（factions_state）
  + protagonist_state arc 基线（从 character_arc_state 的主角）
  + 为 character_arc_state 里的非主角角色播 NPC thread
    · 已死角色 → expected_complete_cluster = death_cluster + outcome = 牺牲（逐章重放 tick 经
      evaluate_completion 把牺牲落进 consequence_tracker）
    · 在世角色 → 开放 thread（无 expected_complete_cluster·只作幕后线存在·不预设结局）

北极星护栏：
- 播「初始条件」非「预设剧情」：只播最小起始集（主角1张 + 1-3阵营 + seed涟漪 + 已见关系）。
  cluster_002+ 的人物/事件一律留 cluster_emergence 涌现，绝不预生成全书人物表/规则书。
- fate_event / minor_event 都是 narrative 型交模型解读，全 advisory，不新增 hard_gate。
- 与 gen_creative._emit_volume_arc_to_db（outline 创意投影）互补：_emit 优先，本器只补 _emit
  没覆盖的（按 rule-id / faction-name / npc-id 增量合并，非空不覆盖）。
- day 推进是机械时间（北极星③：不硬锁，只让世界自然往前走）。

幂等：非空不覆盖（增量合并）。--force 才清掉本器播过的 _seeded_by 项重播。

用法：
    python world_seed_init.py <project> [--factions "A,B,C"] [--force] [--reset-ticks] [--dry-run]

退出码：0 成功 / 2 致命（项目/数据库缺失）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SEED_TAG = "world_seed_init"
_ORG_SUFFIX = "局会殿庭教团盟门派署部堂会社帮阁宗"  # 角色 role 里的组织后缀 → 推势力名


# ---------- IO ----------

def _load(p: Path, default):
    if not p.exists():
        return default
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d
    except (OSError, json.JSONDecodeError):
        return default


def _save(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 派生 ----------

def _me_ids_and_titles(major: dict) -> list[tuple[str, str]]:
    """从 大势卡 取 (me_id, title)。兼容 major_events / major_events_pool + id / me_id。"""
    pool = major.get("major_events")
    if not pool:
        pool = major.get("major_events_pool", [])
    out = []
    for m in pool or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("me_id")
        if mid:
            out.append((str(mid), str(m.get("title") or m.get("name") or "")))
    return out


def derive_factions(arc_state: dict, ensemble: dict, explicit: list[str]) -> list[str]:
    """派生 1-3 个核心阵营名（确定性）。优先级：显式 --factions > 群像档.factions > 角色 role 组织后缀。

    北极星：最小起始集，钳到 ≤3 个，绝不铺全书势力。
    """
    names: list[str] = []

    def _add(n: str):
        n = (n or "").strip()
        if n and n not in names:
            names.append(n)

    for n in explicit:
        _add(n)
    # 群像档可能含 factions / 阵营 字段（schema 未必有·宽容读）
    for key in ("factions", "阵营", "factions_state"):
        v = ensemble.get(key)
        if isinstance(v, dict):
            for k in v.keys():
                _add(str(k))
        elif isinstance(v, list):
            for it in v:
                if isinstance(it, str):
                    _add(it)
                elif isinstance(it, dict):
                    _add(str(it.get("name") or it.get("id") or ""))
    # 角色 role 里的组织后缀 → 截出势力名（如「国安局局长」→「国安局」）
    if not names:
        chars = arc_state.get("characters", {})
        if isinstance(chars, dict):
            for info in chars.values():
                role = (info or {}).get("role", "") if isinstance(info, dict) else ""
                m = re.search(rf"([一-龥]{{2,8}}?[{_ORG_SUFFIX}])", role)
                if m:
                    _add(m.group(1))
    return names[:3]


def _protagonist(arc_state: dict) -> tuple[str, dict]:
    """从 character_arc_state 取主角 (name, info)。无显式 role==主角 时取第一个。"""
    chars = arc_state.get("characters", {})
    if not isinstance(chars, dict) or not chars:
        return "", {}
    for name, info in chars.items():
        if isinstance(info, dict) and info.get("role") == "主角":
            return name, info
    name = next(iter(chars))
    return name, chars[name] if isinstance(chars[name], dict) else {}


# ---------- 播种构造 ----------

def build_seed_rules(me_pairs: list[tuple[str, str]], faction_names: list[str]) -> list[dict]:
    """构造 seed 涟漪规则（① auto_tick base + ② per-ME fate + ③ per-faction minor）。"""
    rules: list[dict] = []
    # ① 基线 auto_tick（每章：评估 NPC thread / 推进 day / 轻量漂移）
    rules.append({
        "id": "RR_AUTO_TICK_BASE",
        "trigger_type": "auto_tick",
        "trigger_match": "every_chapter",
        "ripples": [
            {"target": "active_npc_threads", "evaluate_completion": True,
             "reason": "每章评估幕后 NPC thread 是否到期完成"},
            {"target": "current_world_time.day", "advance": 1,
             "reason": "机械时间推进一格（北极星③·不硬锁）"},
            {"narrative": "时间又往前走了一格，幕后各方按各自的节奏继续运转。",
             "reason": "auto_tick 轻量世界漂移"},
        ],
        "_seeded_by": _SEED_TAG,
    })
    # ② 每 ME 一条 fate_event（narrative 型·交模型解读）
    for mid, title in me_pairs:
        rules.append({
            "id": f"RR_FATE_{mid}",
            "trigger_type": "fate_event",
            "trigger_match": mid,
            "ripples": [
                {"narrative": f"大事件【{title or mid}】落地，世界因果链随之震荡——"
                              f"具体波及哪些人、哪些势力，交由叙事自行解读。",
                 "reason": f"fate_event {mid}"},
            ],
            "_seeded_by": _SEED_TAG,
        })
    # ③ 每势力一条 minor_event（走向卡命中势力名 → 该势力处境被记一笔·narrative 型）
    for fn in faction_names:
        rules.append({
            "id": f"RR_MINOR_{fn}",
            "trigger_type": "minor_event",
            "trigger_match": fn,
            "ripples": [
                {"narrative": f"涉及【{fn}】的走向被选中，这股势力的处境随之被牵动。",
                 "reason": "minor_event 势力响应（走向卡持续后果）"},
            ],
            "_seeded_by": _SEED_TAG,
        })
    return rules


def build_npc_threads(arc_state: dict, protagonist_name: str,
                      start_num: int) -> list[dict]:
    """从 character_arc_state 非主角角色派生 NPC threads。

    死角色 → expected_complete_cluster=death_cluster + outcome=牺牲（逐章重放经 evaluate_completion
    落 consequence_tracker）；在世角色 → 开放 thread（无 expected_complete·不预设结局）。
    """
    threads: list[dict] = []
    chars = arc_state.get("characters", {})
    if not isinstance(chars, dict):
        return threads
    n = start_num
    for name, info in chars.items():
        if name == protagonist_name or not isinstance(info, dict):
            continue
        if info.get("role") == "主角":
            continue
        stage = info.get("current_stage", "")
        is_dead = info.get("status") == "dead"
        death_cluster = info.get("death_cluster")
        thread = {
            "thread_id": f"NT_{n:03d}",
            "npc_id": name,
            "current_action": stage,
            "since_cluster": "cluster_001",
            "expected_complete_cluster": death_cluster if (is_dead and death_cluster) else None,
            "visible_to_protagonist": bool(is_dead),
            "outcome_if_complete": stage if is_dead else "",
            "_priority": 8 if is_dead else 5,
            "_seeded_by": _SEED_TAG,
        }
        threads.append(thread)
        n += 1
    return threads


# ---------- 主播种 ----------

def seed(project_root: Path, *, explicit_factions: list[str], force: bool,
         reset_ticks: bool, dry_run: bool) -> dict:
    db = project_root / "_数据库"
    if not db.exists():
        return {"error": f"_数据库 不存在: {db}"}

    rules_path = db / "涟漪规则.json"
    world_path = db / "世界状态.json"

    major = _load(db / "大势卡.json", {}) or {}
    arc_state = _load(db / "character_arc_state.json", {}) or {}
    ensemble = _load(db / "群像档.json", {}) or {}

    me_pairs = _me_ids_and_titles(major if isinstance(major, dict) else {})
    faction_names = derive_factions(
        arc_state if isinstance(arc_state, dict) else {},
        ensemble if isinstance(ensemble, dict) else {},
        explicit_factions)
    prot_name, prot_info = _protagonist(arc_state if isinstance(arc_state, dict) else {})

    report = {
        "me_count": len(me_pairs),
        "factions": faction_names,
        "protagonist": prot_name,
        "rules_added": [],
        "factions_seeded": [],
        "threads_seeded": [],
        "protagonist_seeded": False,
        "consequence_tracker_normalized": False,
        "ticks_reset": False,
        "dry_run": dry_run,
    }

    # ---- 涟漪规则（增量合并·按 id 去重）----
    rules_doc = _load(rules_path, None)
    if not isinstance(rules_doc, dict):
        rules_doc = {"_schema": "ripple_rules_v20_1", "schema_version": "v27",
                     "ripple_rules": []}
    existing = rules_doc.get("ripple_rules")
    if not isinstance(existing, list):
        existing = []
    if force:
        existing = [r for r in existing if not (isinstance(r, dict) and r.get("_seeded_by") == _SEED_TAG)]
    existing_ids = {r.get("id") for r in existing if isinstance(r, dict)}
    seed_rules = build_seed_rules(me_pairs, faction_names)
    for r in seed_rules:
        if r["id"] not in existing_ids:
            existing.append(r)
            existing_ids.add(r["id"])
            report["rules_added"].append(r["id"])
    rules_doc["ripple_rules"] = existing

    # ---- 世界状态（factions_state / protagonist_state / threads / consequence_tracker）----
    world = _load(world_path, None)
    if not isinstance(world, dict):
        world = {"_schema": "world_state_v20_1", "schema_version": "v27"}
    world.setdefault("current_world_time", {"ch": 0, "cluster": "cluster_001", "day": 1})
    if "day" not in world["current_world_time"]:
        world["current_world_time"]["day"] = 1

    # consequence_tracker 必须是 dict（engine setdefault + 字符串键·list 会 TypeError）
    ct = world.get("consequence_tracker")
    if not isinstance(ct, dict):
        if isinstance(ct, list) and ct:
            # 非空 list（异常）：转 dict 保留数据，避免引擎崩
            world["consequence_tracker"] = {f"_legacy_{i}": v for i, v in enumerate(ct)}
        else:
            world["consequence_tracker"] = {}
        report["consequence_tracker_normalized"] = True

    # factions_state（非空不覆盖·--force 清 seeded）
    fs = world.get("factions_state")
    if not isinstance(fs, dict):
        fs = {}
    if force:
        fs = {k: v for k, v in fs.items()
              if not (isinstance(v, dict) and v.get("_seeded_by") == _SEED_TAG)}
    for fn in faction_names:
        if fn not in fs:
            fs[fn] = {"name": fn, "power": 50, "stability": 50, "wealth": 50,
                      "current_focus": "", "_seeded_by": _SEED_TAG}
            report["factions_seeded"].append(fn)
    world["factions_state"] = fs

    # protagonist_state（仅当为空）
    ps = world.get("protagonist_state")
    if not isinstance(ps, dict) or not ps or force:
        if prot_name:
            world["protagonist_state"] = {
                "name": prot_name,
                "arc_stage": prot_info.get("current_stage", ""),
                "status": prot_info.get("status", "alive"),
                "current_focus": "",
                "_seeded_by": _SEED_TAG,
            }
            report["protagonist_seeded"] = True

    # active_npc_threads（按 npc_id 增量·--force 清 seeded）
    threads = world.get("active_npc_threads")
    if not isinstance(threads, list):
        threads = []
    if force:
        threads = [t for t in threads
                   if not (isinstance(t, dict) and t.get("_seeded_by") == _SEED_TAG)]
    existing_npcs = {t.get("npc_id") for t in threads if isinstance(t, dict)}
    # 计算下一个 NT_ 序号
    max_nt = 0
    for t in threads:
        if isinstance(t, dict):
            m = re.match(r"NT_(\d+)", str(t.get("thread_id", "")))
            if m:
                max_nt = max(max_nt, int(m.group(1)))
    new_threads = build_npc_threads(
        arc_state if isinstance(arc_state, dict) else {}, prot_name, max_nt + 1)
    for t in new_threads:
        if t["npc_id"] not in existing_npcs:
            threads.append(t)
            existing_npcs.add(t["npc_id"])
            report["threads_seeded"].append(t["npc_id"])
    world["active_npc_threads"] = threads
    world.setdefault("emergent_opportunities", [])

    # ---- reset ticks（一次性重放数据重建·new book 默认 off）----
    if reset_ticks:
        world["applied_ticks"] = []
        world["world_ticks_log"] = []
        # consequence_tracker 清空重建（逐章重放会重新落沉淀）
        world["consequence_tracker"] = {}
        report["ticks_reset"] = True

    if dry_run:
        report["_dry_run_note"] = "未写盘"
        return report

    _save(rules_path, rules_doc)
    _save(world_path, world)
    return report


def main() -> int:
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="世界演化初始条件幂等播种器（SYS-1/C02）")
    ap.add_argument("project")
    ap.add_argument("--factions", default="", help="显式核心阵营名（逗号分隔·≤3·覆盖派生）")
    ap.add_argument("--force", action="store_true", help="清掉本器播过的 _seeded_by 项重播")
    ap.add_argument("--reset-ticks", action="store_true",
                    help="清 applied_ticks/world_ticks_log/consequence_tracker（一次性逐章重放前用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不写盘")
    args = ap.parse_args()

    explicit = [s.strip() for s in args.factions.split(",") if s.strip()]
    r = seed(Path(args.project), explicit_factions=explicit, force=args.force,
             reset_ticks=args.reset_ticks, dry_run=args.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("error"):
        print(f"[FATAL] {r['error']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
