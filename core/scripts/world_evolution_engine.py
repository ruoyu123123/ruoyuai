"""world_evolution_engine.py — 鬼谷八荒式世界自转引擎（v20.1 W3 新增）

核心理念：
- 世界状态.json 是世界活体快照（factions/NPC threads/emergent opportunities/consequences）
- 涟漪规则.json 定「事件 → 世界变化」的因果映射
- /cluster-save-state 按 cluster 章范围自动 tick：NPC threads 推进、超时 thread 触发完成事件、time roll
- 用户选定走向卡 → apply_minor_event：匹配 ripple_rule → 数值变化 + 新 thread spawn
- 大事件触发 → apply_fate_event：与 fate_engine 协同，触发更大涟漪

四个操作：
1. tick <ch>                      - 每章自动推进世界一格（auto_tick）
2. apply_minor_event <ch> --event <匹配字符串>  - 用户选定走向卡后触发
3. apply_fate_event <ch> --event <ME_id>        - fate_engine update 后触发
4. spawn_emergent <ch>            - 检查 emergent_opportunities 是否到期可用
5. dashboard                      - 世界全景

ripple 操作支持：
- target = "factions_state.X.Y"     + delta/set    -> 数值增减或设值
- target = "active_npc_threads"     + add_thread    -> 追加新 NPC 行动
- target = "active_npc_threads"     + evaluate_completion=true -> 评估超期 thread
- target = "emergent_opportunities" + spawn         -> 生成新机缘
- target = "consequence_tracker"    + add           -> 追加因果记录
- target = "current_world_time.ch"  + set_to_current_ch=true -> roll 时间

用法：
    python world_evolution_engine.py <project> tick <ch>
    python world_evolution_engine.py <project> apply_minor_event <ch> --event "B_鼻子先觉|铁锈味重锤埋设"
    python world_evolution_engine.py <project> apply_fate_event <ch> --event ME_003
    python world_evolution_engine.py <project> spawn_emergent <ch>
    python world_evolution_engine.py <project> dashboard

退出码: 0 成功 / 1 无规则匹配 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 2026-05-29 修：原子写 + 章号→cluster 正确反查共享工具
import cluster_lookup
from atomic_json import atomic_write_json


# ---------- IO ----------

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：改用原子写（写临时文件 + os.replace），防写一半崩溃损坏世界状态
    atomic_write_json(p, data)


def load_world(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "世界状态.json", None)


def load_rules(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "涟漪规则.json", None)


def save_world(project_root: Path, world: dict):
    save_json(project_root / "_数据库" / "世界状态.json", world)


# ---------- ripple ops ----------

def _resolve_path(world: dict, dotted: str):
    """world 内点号路径解析，返回 (parent_dict, last_key)。不存在路径返回 (None, None)。"""
    parts = dotted.split(".")
    cur = world
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return (None, None)
        cur = cur[p]
    return (cur, parts[-1]) if isinstance(cur, dict) else (None, None)


def _apply_ripple(world: dict, ripple: dict, ch: int, applied_log: list, project_root: Path | None = None) -> bool:
    """单条 ripple 落地。返回是否成功应用。

    2026-05-29 修：新增 project_root，供 add_thread/evaluate_completion 把章号
    反查成正确 cluster_id（cluster_lookup），不再机械拼 cluster_{ch}。
    """
    target = ripple.get("target")
    reason = ripple.get("reason", "")

    # ---- narrative（混合式 · 2026-05-29 北极星 P1）：叙事/主观因果，无 target，最先处理 ----
    # 散文型涟漪后果（心理/关系/叙事）引擎不机械算数值（守原则5「不干涉模型判断」+「不预设因果」），
    # 只收集进 narrative_consequences，由 build_manifest 注入 writer / cluster_emergence，让模型自行解读。
    # 2026-05-29 复审 W3：① 加 not target 守卫——混合 ripple({narrative,target,delta})落到下方结构化
    #   处理算 delta，不被 narrative 提前 return 静默丢数值；② (ch,text) 去重——防断点重跑/重复走向词
    #   使 narrative_consequences 单调膨胀（注入端不截断，靠此处去重保持唯一因果）。
    if "narrative" in ripple and not target:
        nc = world.setdefault("narrative_consequences", [])
        _txt = ripple.get("narrative", "")
        if not any(isinstance(e, dict) and e.get("ch") == ch and e.get("text") == _txt for e in nc):
            nc.append({"ch": ch, "text": _txt, "reason": reason, "_kind": "narrative"})
            applied_log.append({"target": "narrative_consequences", "op": "narrative", "text": _txt[:60]})
        return True

    if not target:
        return False

    # ---- 数值 delta（factions_state.X.Y） ----
    if "delta" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "delta", "result": "skip_path_missing"})
            return False
        old = parent.get(key, 0)
        if not isinstance(old, (int, float)):
            applied_log.append({"target": target, "op": "delta", "result": "skip_not_numeric"})
            return False
        delta = ripple["delta"]
        if not isinstance(delta, (int, float)) or isinstance(delta, bool):
            # 2026-05-30 北极星复审：涟漪规则若手写/LLM 生成 delta 为 "+5" 字符串 → old+delta TypeError。
            # 守卫（与 old 同级）：非数值 delta 跳过不崩（世界数值是大势核心，崩会丢牵引）。
            applied_log.append({"target": target, "op": "delta", "result": "skip_delta_not_numeric"})
            return False
        new = max(0, min(100, old + delta))  # 数值钳制 0-100
        parent[key] = new
        applied_log.append({"target": target, "op": "delta", "old": old, "new": new, "delta": delta, "reason": reason})
        return True

    # ---- advance（无界数值增量）🔴 2026-06-27 SYS-1 ----
    # delta 钳 0-100 不适合时间/累计计数（current_world_time.day 等）；set 又只硬写覆盖。
    # advance = 无界 += inc，让基线 auto_tick 能让 day 按章自然推进（北极星③：机械时间推进
    # 非创作判断·不钳制不硬锁）。与 consequence_tracker 的 "add"(dict 追加) 互不冲突（键不同）。
    if "advance" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "advance", "result": "skip_path_missing"})
            return False
        inc = ripple["advance"]
        if not isinstance(inc, (int, float)) or isinstance(inc, bool):
            applied_log.append({"target": target, "op": "advance", "result": "skip_inc_not_numeric"})
            return False
        old = parent.get(key, 0)
        if not isinstance(old, (int, float)) or isinstance(old, bool):
            old = 0  # 旧值脏（None/str）→ 从 0 起推进，不崩
        new = old + inc  # 无界（不 clamp）
        parent[key] = new
        applied_log.append({"target": target, "op": "advance", "old": old, "new": new, "inc": inc, "reason": reason})
        return True

    # ---- set 值 ----
    if "set" in ripple:
        parent, key = _resolve_path(world, target)
        if parent is None:
            applied_log.append({"target": target, "op": "set", "result": "skip_path_missing"})
            return False
        old = parent.get(key)
        parent[key] = ripple["set"]
        applied_log.append({"target": target, "op": "set", "old": old, "new": ripple["set"], "reason": reason})
        return True

    # ---- set_to_current_ch ----
    if ripple.get("set_to_current_ch"):
        parent, key = _resolve_path(world, target)
        if parent is None:
            return False
        parent[key] = ch
        applied_log.append({"target": target, "op": "set_ch", "new": ch})
        return True

    # ---- add_thread (active_npc_threads) ----
    if "add_thread" in ripple:
        threads = world.setdefault("active_npc_threads", [])
        td = ripple["add_thread"]
        # 自动分配 thread_id
        existing_ids = [t.get("thread_id", "") for t in threads]
        nums = []
        for tid in existing_ids:
            m = re.match(r"NT_(\d+)", tid)
            if m:
                nums.append(int(m.group(1)))
        next_num = (max(nums) + 1) if nums else 1
        # v2 cluster 化（2026-05-28）：纯 cluster 模式
        # 2026-05-29 修：since_cluster 由 ch 经 cluster_lookup 反查，反查失败按章号近似 + 标 inferred
        since_cid = cluster_lookup.ch_to_cluster_id(project_root, ch) if project_root is not None else None
        inferred = False
        if not since_cid:
            since_cid = cluster_lookup.normalize_cluster_id(ch)
            inferred = True
        new_thread = {
            "thread_id": f"NT_{next_num:03d}",
            "npc_id": td.get("npc_id", "?"),
            "current_action": td.get("action", ""),
            "since_cluster": since_cid,
            "expected_complete_cluster": td.get("expected_complete_cluster"),
            "visible_to_protagonist": td.get("visible_to_protagonist", False),
            "outcome_if_complete": td.get("outcome_if_complete", ""),
            "_priority": td.get("_priority", 5),
            "_spawned_by_ripple": True,
        }
        if inferred:
            new_thread["_cluster_inferred"] = True
        threads.append(new_thread)
        applied_log.append({"target": "active_npc_threads", "op": "add_thread", "thread_id": new_thread["thread_id"], "npc": new_thread["npc_id"]})
        return True

    # ---- evaluate_completion ----
    # 2026-05-29 修：字段名统一为 expected_complete_cluster（add_thread 写的就是它）。
    # 原读 expected_complete_ch 永远读不到 → thread 永不到期。
    # tick 传入的 ch 是章号 → 先反查成当前 cluster 序号，再与 thread 的目标 cluster 序号比较。
    if ripple.get("evaluate_completion"):
        threads = world.get("active_npc_threads", [])
        completed_log = world.setdefault("world_ticks_log", [])
        # 2026-05-29 复审修复 [L13]（SC-5）：当前章号 → 当前 cluster 序号。
        # 反查失败时**跳过本次完成评估**，不再用章号顶替 cluster 序号
        #（章号 ≠ cluster 序号：第 7 章可能属 cluster_002，用 7 当 cluster 序号会
        #  把所有 expected_complete_cluster ≤ 7 的 thread 全部误判到期完成 → 正典污染）。
        cur_cid = cluster_lookup.ch_to_cluster_id(project_root, ch) if project_root is not None else None
        cur_cluster_num = cluster_lookup.cluster_num(cur_cid) if cur_cid else None
        if cur_cluster_num is None:
            applied_log.append({
                "target": "active_npc_threads",
                "op": "evaluate_completion",
                "result": "skip_cluster_lookup_failed",
                "ch": ch,
                "note": "ch→cluster 反查失败（chapter_range 未回填）→ 跳过到期评估，不用章号顶替",
            })
            return True
        completed_threads = []
        remaining = []
        for t in threads:
            ec_num = cluster_lookup.cluster_num(t.get("expected_complete_cluster"))
            if ec_num is not None and cur_cluster_num is not None and cur_cluster_num >= ec_num:
                completed_threads.append(t)
                # 自动 spawn 一条 consequence
                consequence_tracker = world.setdefault("consequence_tracker", {})
                key = f"ch{ch}_thread_complete_{t.get('thread_id','?')}"
                consequence_tracker[key] = {
                    "trigger": f"NT thread {t.get('thread_id')} 到期完成",
                    "npc": t.get("npc_id"),
                    "outcome": t.get("outcome_if_complete", ""),
                    "world_changes": [t.get("outcome_if_complete", "")],
                }
            else:
                remaining.append(t)
        world["active_npc_threads"] = remaining
        if completed_threads:
            applied_log.append({
                "target": "active_npc_threads",
                "op": "evaluate_completion",
                "completed_count": len(completed_threads),
                "completed_thread_ids": [t.get("thread_id") for t in completed_threads],
            })
        return True

    # ---- spawn (emergent_opportunities) ----
    if "spawn" in ripple and target == "emergent_opportunities":
        opps = world.setdefault("emergent_opportunities", [])
        sp = ripple["spawn"]
        existing_ids = [o.get("id", "") for o in opps]
        nums = []
        for oid in existing_ids:
            m = re.match(r"EO_(\d+)", oid)
            if m:
                nums.append(int(m.group(1)))
        next_num = (max(nums) + 1) if nums else 1
        expires_in = sp.get("expires_chapters", 5)
        new_opp = {
            "id": f"EO_{next_num:03d}",
            "trigger_ch": ch + 1,  # 下一章可用
            "type": sp.get("type", "副线机缘"),
            "description": sp.get("description", ""),
            "consumed_by_writer": False,
            "expires_at_ch": ch + expires_in,
            "_spawned_by_ripple": True,
        }
        opps.append(new_opp)
        applied_log.append({"target": "emergent_opportunities", "op": "spawn", "id": new_opp["id"]})
        return True

    # ---- add (consequence_tracker) ----
    if "add" in ripple and target == "consequence_tracker":
        consequences = world.setdefault("consequence_tracker", {})
        ad = ripple["add"]
        key = f"ch{ch}_{ad.get('event', 'consequence')[:30]}"
        consequences[key] = {
            "trigger": ad.get("event", ""),
            "world_changes": ad.get("world_changes", []),
            "added_at_ch": ch,
        }
        applied_log.append({"target": "consequence_tracker", "op": "add", "key": key})
        return True

    applied_log.append({"target": target, "op": "unknown", "ripple": ripple})
    return False


def _normalize_rule(rule: dict) -> dict:
    """混合式归一（2026-05-29 北极星 P1）：把两套历史格式统一成引擎可消费的 canonical 形态。

    canonical: {id, trigger_type, trigger_match, ripples:[结构化{target,delta/add_thread} | 叙事{narrative}]}
    - 规范格式（范例）：已是 ripple_rules[{id,trigger_type,trigger_match,ripples}]，原样返回。
    - 历史散文格式（真实项目 rules[{(rule_)id, trigger, effect}]）：trigger(散文)→trigger_match，
      effect(散文 str/list)→**叙事 ripple**（不机械解析成数值 delta，守原则5），trigger_type=None(通配)。
    """
    if not isinstance(rule, dict):
        return {}
    rid = rule.get("id") or rule.get("rule_id") or ""
    # 已是规范格式
    if "ripples" in rule and ("trigger_match" in rule or "trigger_type" in rule):
        out = dict(rule)
        out["id"] = rid
        return out
    # 历史散文格式（effect: str/list 或 propagate: [{path,op,value}]）
    trigger_match = rule.get("trigger_match") or rule.get("trigger") or ""
    ripples = []
    eff = rule.get("effect")
    if isinstance(eff, str) and eff.strip():
        ripples.append({"narrative": eff.strip(), "reason": rule.get("description", "")})
    elif isinstance(eff, list):
        for e in eff:
            if isinstance(e, str) and e.strip():
                ripples.append({"narrative": e.strip()})
            elif isinstance(e, dict):
                ripples.append(e)  # 已结构化的条目原样
    # propagate 格式（纵尸司：[{path, op, value}]）：path 用「世界状态.X」自由路径，不匹配引擎
    # factions_state 结构，op 含主观操作(deepen)。按原则5 不机械解析成 delta，转叙事 ripple 保留
    # path/op/value 文本给模型解读（objective 数值后果交模型在写作时落实，不由脚本预设）。
    prop = rule.get("propagate")
    if isinstance(prop, list):
        for pe in prop:
            if isinstance(pe, dict) and pe.get("path"):
                _txt = f"{pe.get('path')} {pe.get('op','')}".strip()
                if pe.get("value"):
                    _txt += f" = {pe.get('value')}"
                ripples.append({"narrative": _txt})
            elif isinstance(pe, str) and pe.strip():
                ripples.append({"narrative": pe.strip()})
    if not rid:
        import hashlib as _hl  # 散文规则常无 id，给确定性兜底（hash() 跨进程不稳定）
        rid = "rip_" + _hl.md5(trigger_match.encode("utf-8")).hexdigest()[:6]
    return {
        "id": rid,
        "trigger_type": rule.get("trigger_type"),  # None = 通配（非 auto_tick 时按关键词匹配）
        "trigger_match": trigger_match,
        "ripples": ripples,
        "_from_prose": True,
    }


def _match_rule(rule: dict, trigger_type: str, trigger_value: str) -> bool:
    """rule 是否匹配本次触发。trigger_match 用 | 分隔多个候选关键词。

    2026-05-29 北极星 P1 混合式：rule.trigger_type 为 None（散文规则未声明类型）时，
    非 auto_tick 触发按关键词匹配（通配 type）；auto_tick 仍要求显式 trigger_type==auto_tick。
    """
    rtype = rule.get("trigger_type")
    if trigger_type == "auto_tick":
        return rtype == "auto_tick" and rule.get("trigger_match", "") == "every_chapter"
    # 非 auto_tick：rtype 须匹配或为 None(通配)
    if rtype is not None and rtype != trigger_type:
        return False
    match_pattern = rule.get("trigger_match", "")
    if not match_pattern:
        return False
    candidates = [c.strip() for c in match_pattern.split("|") if c.strip()]
    return any(c in trigger_value or trigger_value in c for c in candidates)


def _apply_rules(world: dict, rules_json: dict, trigger_type: str, trigger_value: str, ch: int, project_root: Path | None = None) -> dict:
    """匹配 + 应用所有命中规则。

    2026-05-29 修：透传 project_root 给 _apply_ripple 做 ch→cluster 反查。
    """
    # 2026-05-29 北极星 P1 混合式：双键兼容（范例写 ripple_rules，真实项目写 rules）
    # → 归一每条规则成 canonical（散文格式的 effect 转叙事 ripple），根治「引擎读 ripple_rules
    # 但项目写 rules → 恒拿空 → 涟漪从未触发」。
    raw_rules = rules_json.get("ripple_rules")
    if not raw_rules:
        raw_rules = rules_json.get("rules", [])
    rules = [_normalize_rule(r) for r in (raw_rules or []) if isinstance(r, dict)]
    applied_log: list = []
    matched_rules = []
    for rule in rules:
        if not _match_rule(rule, trigger_type, trigger_value):
            continue
        matched_rules.append(rule.get("id"))
        for ripple in rule.get("ripples", []):
            _apply_ripple(world, ripple, ch, applied_log, project_root)
    return {"matched_rules": matched_rules, "applied_log": applied_log}


# ---------- 公共操作 ----------

def tick(project_root: Path, ch: int) -> dict:
    """每章 auto_tick：触发 RR_AUTO_TICK 涟漪 + 推进 NPC threads + roll time。"""
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}
    if rules is None:
        return {"error": "涟漪规则.json 不存在"}

    # 1. roll current_world_time.ch
    cwt = world.setdefault("current_world_time", {})
    cwt["ch"] = ch

    # 2. 跑 auto_tick 规则
    # 2026-05-30 北极星复审：tick 幂等——cluster-save-state 支持 WAL/断点重跑，会逐章 tick；原实现
    # 无条件 _apply_rules（累加 delta/spawn/consequence）只对日志去重 → 重跑使 faction power/stress
    # 等数值重复落地、世界状态漂移。仿 applied_fate_events 加 applied_ticks 幂等账本。
    applied_ticks = world.setdefault("applied_ticks", [])
    if ch in applied_ticks:
        save_world(project_root, world)  # 仅更新 cwt.ch，不重复应用 auto_tick
        return {
            "ch": ch, "action": "tick", "skipped": "already_ticked",
            "matched_rules": [], "applied_count": 0,
            "active_threads": len(world.get("active_npc_threads", [])),
            "active_opps": sum(1 for o in world.get("emergent_opportunities", []) if not o.get("consumed_by_writer")),
        }
    result = _apply_rules(world, rules, "auto_tick", "every_chapter", ch, project_root)
    applied_ticks.append(ch)

    # 3. 追加 world_ticks_log（去重只匹配 auto_tick 类型，避免与 minor_event/fate_event 同 ch 日志串台）
    log = world.setdefault("world_ticks_log", [])
    if not any(e.get("ch") == ch and e.get("trigger_type") == "auto_tick" for e in log):
        log.append({
            "ch": ch,
            "trigger_type": "auto_tick",
            "tick_summary": f"auto_tick: {len(result['matched_rules'])} 规则触发, {len(result['applied_log'])} 涟漪落地",
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "tick",
        "matched_rules": result["matched_rules"],
        "applied_count": len(result["applied_log"]),
        "active_threads": len(world.get("active_npc_threads", [])),
        "active_opps": sum(1 for o in world.get("emergent_opportunities", []) if not o.get("consumed_by_writer")),
    }


def apply_minor_event(project_root: Path, ch: int, event_value: str) -> dict:
    """用户选定走向卡 → 触发 minor_event 类型涟漪。"""
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None or rules is None:
        return {"error": "世界状态.json/涟漪规则.json 不存在"}

    result = _apply_rules(world, rules, "minor_event", event_value, ch, project_root)

    # 写入 world_ticks_log
    log = world.setdefault("world_ticks_log", [])
    log.append({
        "ch": ch,
        "tick_summary": f"minor_event: {event_value} → {len(result['matched_rules'])} 规则",
        "trigger_type": "minor_event",
        "trigger_value": event_value,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "apply_minor_event",
        "trigger": event_value,
        "matched_rules": result["matched_rules"],
        "applied_log": result["applied_log"],
    }


def apply_fate_event(project_root: Path, ch: int, event_id: str) -> dict:
    """fate_engine update 完成后 → 触发 fate_event 类型涟漪。

    2026-05-29 复审修复 [H14]：world_evolution_apply_chapter --cluster 逐章重放同一份
    fate_events_triggered（split_cluster_changes 把整 cluster 的 factual 平铺进每章
    _changes.json，每章都含同一批 fate_events_triggered），导致同一 ME 事件的涟漪
    （faction delta / spawn thread / consequence）被应用 N 倍（实测 6 倍）。
    修：用 world.applied_fate_events 记录已应用的 event_id（按事件粒度幂等去重）。
    同一 event_id 第二次进来直接跳过涟漪应用，只记一条 skipped 日志。
    （注：fate 涟漪是「事件级一次性世界影响」，不是「每章累加」，故按 event_id 去重
    语义正确；首次应用记录触发章 ch，供审计。）
    """
    world = load_world(project_root)
    rules = load_rules(project_root)
    if world is None or rules is None:
        return {"error": "世界状态.json/涟漪规则.json 不存在"}

    # 幂等去重：已应用过的 fate event 不再重放涟漪
    applied_ledger = world.setdefault("applied_fate_events", {})
    if event_id in applied_ledger:
        # 已在 first_applied_at_ch 应用过 → 跳过，避免 delta/thread/consequence 被乘倍
        save_world(project_root, world)  # 仅持久化（applied_fate_events 已存在，无副作用）
        return {
            "ch": ch,
            "action": "apply_fate_event",
            "event_id": event_id,
            "matched_rules": [],
            "applied_log": [],
            "skipped_idempotent": True,
            "first_applied_at_ch": applied_ledger[event_id].get("ch"),
        }

    result = _apply_rules(world, rules, "fate_event", event_id, ch, project_root)

    # 记录已应用（幂等键），供下次重放跳过
    applied_ledger[event_id] = {
        "ch": ch,
        "matched_rules": result["matched_rules"],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }

    log = world.setdefault("world_ticks_log", [])
    log.append({
        "ch": ch,
        "tick_summary": f"fate_event: {event_id} → {len(result['matched_rules'])} 规则",
        "trigger_type": "fate_event",
        "trigger_value": event_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
    })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "apply_fate_event",
        "event_id": event_id,
        "matched_rules": result["matched_rules"],
        "applied_log": result["applied_log"],
    }


def spawn_emergent(project_root: Path, ch: int) -> dict:
    """检查 emergent_opportunities：到期未消费 → 标 expired，可用未消费 → 列出。"""
    world = load_world(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}

    opps = world.get("emergent_opportunities", [])
    available = []
    expired_ids = []
    for o in opps:
        if o.get("consumed_by_writer"):
            continue
        trigger_ch = o.get("trigger_ch", 0)
        expires_at = o.get("expires_at_ch", 9999)
        if ch > expires_at:
            o["status"] = "expired"
            expired_ids.append(o.get("id"))
        elif ch >= trigger_ch:
            available.append({
                "id": o.get("id"),
                "type": o.get("type"),
                "description": o.get("description"),
                "expires_at_ch": expires_at,
                "remaining_chapters": expires_at - ch,
            })

    save_world(project_root, world)
    return {
        "ch": ch,
        "action": "spawn_emergent",
        "available": available,
        "expired_this_ch": expired_ids,
    }


def dashboard(project_root: Path) -> dict:
    """世界全景。"""
    world = load_world(project_root)
    if world is None:
        return {"error": "世界状态.json 不存在"}

    factions = world.get("factions_state", {})
    factions_summary = {}
    for name, f in factions.items():
        factions_summary[name] = {
            "power": f.get("power"),
            "stability": f.get("stability"),
            "wealth": f.get("wealth"),
            "focus": (f.get("current_focus", "") or "")[:40],
        }

    threads = world.get("active_npc_threads", [])
    threads_summary = [
        {
            "id": t.get("thread_id"),
            "npc": t.get("npc_id"),
            "action": (t.get("current_action") or "")[:40],
            "since_cluster": t.get("since_cluster"),
            "expected_complete_cluster": t.get("expected_complete_cluster"),
            "priority": t.get("_priority"),
        }
        for t in sorted(threads, key=lambda x: -(x.get("_priority", 0)))[:10]
    ]

    opps = world.get("emergent_opportunities", [])
    opps_active = [o for o in opps if not o.get("consumed_by_writer") and o.get("status") != "expired"]

    return {
        "current_ch": world.get("current_world_time", {}).get("ch"),
        "factions": factions_summary,
        "active_npc_threads_top10": threads_summary,
        "active_threads_total": len(threads),
        "active_emergent_opportunities": len(opps_active),
        "consequence_records": len(world.get("consequence_tracker", {})),
        "world_ticks_logged": len(world.get("world_ticks_log", [])),
    }


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["tick", "apply_minor_event", "apply_fate_event", "spawn_emergent", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    ap.add_argument("--event", type=str, default=None, help="apply_minor_event/apply_fate_event 触发值")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print(f"[ERROR] {args.action} 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    if args.action == "tick":
        r = tick(project_root, args.ch)
    elif args.action == "spawn_emergent":
        r = spawn_emergent(project_root, args.ch)
    elif args.action == "apply_minor_event":
        if not args.event:
            print("[ERROR] apply_minor_event 需要 --event <匹配字符串>", file=sys.stderr)
            sys.exit(2)
        r = apply_minor_event(project_root, args.ch, args.event)
    elif args.action == "apply_fate_event":
        if not args.event:
            print("[ERROR] apply_fate_event 需要 --event <ME_id>", file=sys.stderr)
            sys.exit(2)
        r = apply_fate_event(project_root, args.ch, args.event)
    else:
        sys.exit(2)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    if "error" in r:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
