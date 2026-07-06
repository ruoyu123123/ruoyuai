#!/usr/bin/env python3
"""emergence_transparency.py — 走向卡候选打分透明化 + ME 依赖图健康校验（P2 · 2026-07-06）

借鉴 PlotPilot 的 storyline DAG / candidate scoring（出处 research/open_source_writing_systems.md）：
候选排序把用到的信号完整亮给用户看（北极星⑤ reason 不黑箱），最终分支仍由用户走向卡唯一确定。
本模块只做只读解释——绝不改变选择逻辑/排序、绝不写任何库文件、绝不自动替用户选。

三块能力（cluster_emergence_engine 消费·该文件已逼近 1000 行软上限，判断力不下沉进它）：
1. convergence_subscore —— 「大势收敛」子分唯一实现：_score_one_me 维度6 加分与
   decision_basis.convergence_score 共用本函数，数值绝不双口径。
2. state_delta_preview —— 候选被选中后会触发的涟漪规则确定性预演。复用
   world_evolution_engine 的纯函数 _normalize_rule/_match_rule（_apply_ripple 耦合世界状态
   写库，不复用）；数值只转述规则声明的 delta/advance/set，不硬造。
3. me_dag_health —— ME prerequisites 依赖图环检测 + 悬挂引用检测（环 = 环上 ME 互为前置
   永不可触发；悬挂 = 前置指向池中不存在的 ME id → 被打分层 -100 硬剔成死链）。
   advisory：只报告不裁决——大势卡是用户/outline 的创作产物。
"""
from __future__ import annotations

import world_evolution_engine as _wee

# dim-6 收敛公式常量（与 _score_one_me 历史口径一致：+8/重叠·封顶 35，与涟漪呼应同量级）
_CONV_CAP = 35
_CONV_PER_OVERLAP = 8


def convergence_subscore(me_kw: set, milestone_kw: set) -> tuple:
    """dim-6 大势收敛子分：ME 关键词与本卷「未达成里程碑/收敛」关键词重叠。

    返回 (score, hits)；hits 排序输出，保证跨进程确定性（str hash 随机化不影响展示）。
    任一侧关键词缺失 → (0, [])：advisory 维度缺数据时静默让位，绝不阻断涌现。
    """
    if not me_kw or not milestone_kw:
        return 0, []
    overlap = sorted(me_kw & milestone_kw)
    if not overlap:
        return 0, []
    return min(_CONV_CAP, _CONV_PER_OVERLAP * len(overlap)), overlap


# ───────────────────── state_delta_preview（只读预演·绝不写库） ─────────────────────

def _ripple_effect_summary(ripple: dict) -> dict:
    """把单条 ripple 声明转述成用户可读的影响面摘要。

    与 world_evolution_engine._apply_ripple 的操作分支一一对应，但只转述声明、
    不解析路径、不算 old/new（那需要读写世界状态 → 属于 save-state 落库阶段）。
    """
    target = ripple.get("target") or ""
    if "narrative" in ripple and not target:
        return {"op": "narrative", "summary": str(ripple.get("narrative", ""))[:120]}
    if "delta" in ripple:
        return {"op": "delta", "target": target, "delta": ripple.get("delta")}
    if "advance" in ripple:
        return {"op": "advance", "target": target, "advance": ripple.get("advance")}
    if "set" in ripple:
        return {"op": "set", "target": target, "value": ripple.get("set")}
    if ripple.get("set_to_current_ch"):
        return {"op": "set_to_current_ch", "target": target}
    if "add_thread" in ripple:
        td = ripple.get("add_thread") or {}
        return {"op": "add_thread", "npc": td.get("npc_id", "?"),
                "action": str(td.get("action", ""))[:80]}
    if ripple.get("evaluate_completion"):
        return {"op": "evaluate_completion", "target": target}
    if "spawn" in ripple:
        sp = ripple.get("spawn") or {}
        return {"op": "spawn_opportunity", "type": sp.get("type", ""),
                "description": str(sp.get("description", ""))[:80]}
    if "add" in ripple:
        ad = ripple.get("add") or {}
        return {"op": "add_consequence", "event": str(ad.get("event", ""))[:80]}
    return {"op": "unknown", "target": target}


def state_delta_preview(me_id: str, me_text: str, ripple_rules_json: dict) -> dict:
    """候选被选中后世界状态的确定性 delta 预览（preview-only）。

    匹配语义对齐真实落库入口（世界演化在 save-state 阶段才写库）：
    - apply_fate_event 按 ME id 触发 → 这里按 fate_event + me_id 预演；
    - apply_minor_event 按用户选定走向文本触发 → 这里按 minor_event + 候选 ME 文本近似预演。
    规则归一/匹配复用 world_evolution_engine._normalize_rule/_match_rule（纯函数）；
    auto_tick 规则与具体候选无关，不进预览。
    """
    raw = ripple_rules_json if isinstance(ripple_rules_json, dict) else {}
    raw_rules = raw.get("ripple_rules") or raw.get("rules") or []
    rules = [_wee._normalize_rule(r) for r in raw_rules if isinstance(r, dict)]
    matched = []
    for rule in rules:
        matched_via = []
        if me_id and _wee._match_rule(rule, "fate_event", str(me_id)):
            matched_via.append("fate_event")
        if me_text and _wee._match_rule(rule, "minor_event", me_text):
            matched_via.append("minor_event")
        if not matched_via:
            continue
        matched.append({
            "rule_id": rule.get("id"),
            "matched_via": matched_via,
            "trigger_match": str(rule.get("trigger_match", ""))[:80],
            "effects": [_ripple_effect_summary(rp) for rp in rule.get("ripples", [])
                        if isinstance(rp, dict)],
        })
    out = {
        "_doc": ("preview-only：候选选中后 save-state 阶段会触发的涟漪规则只读预演"
                 "（fate_event 按 ME id / minor_event 按候选文本匹配）·数值转述规则声明不硬造·绝不写库"),
        "matched_rules": matched,
    }
    if not rules:
        out["note"] = "无涟漪规则（涟漪规则.json 缺失或为空）"
    elif not matched:
        out["note"] = "无规则命中该候选（选中后叙事层涟漪仍由模型解读）"
    return out


# ───────────────────── me_dag_health（环 + 悬挂·确定性·advisory） ─────────────────────

def me_dag_health(pool: list, *, get_id, get_parents) -> dict:
    """ME prerequisites 依赖图健康校验。

    - cycles: 每个环表示为 ME id 列表（旋转到最小 id 开头的规范形·按字典序输出去重）
      —— 环上 ME 互为前置 → prerequisites 永不满足 → 永不可触发。
    - dangling: [{"me": id, "missing": [不存在的前置 id...]}] 按 me id 排序
      —— 前置指向池中不存在的 ME → 打分层 -100 硬剔成死链。
    get_id/get_parents 由调用方（cluster_emergence_engine 的 _get_me_id/_resolve_parents）
    注入，保证 id/前置链解析口径与打分层完全一致（避免双口径）。
    确定性：同输入同输出；只读不修复不裁决。
    """
    graph: dict = {}
    order: list = []
    for me in pool or []:
        if not isinstance(me, dict):
            continue
        mid = get_id(me)
        if not mid:
            continue
        if mid not in graph:
            graph[mid] = []
            order.append(mid)
        for p in get_parents(me):
            if p and p not in graph[mid]:
                graph[mid].append(p)
    id_set = set(graph)

    dangling = []
    for mid in sorted(order):
        missing = sorted({p for p in graph[mid] if p not in id_set})
        if missing:
            dangling.append({"me": mid, "missing": missing})

    cycles: list = []
    seen: set = set()
    color: dict = {}  # 0=未访问 1=在栈 2=已完成
    stack: list = []

    def _dfs(u):
        color[u] = 1
        stack.append(u)
        for v in graph.get(u, []):
            if v not in id_set:
                continue
            c = color.get(v, 0)
            if c == 0:
                _dfs(v)
            elif c == 1:
                i = stack.index(v)
                cyc = stack[i:]
                k = cyc.index(min(cyc))
                canon = tuple(cyc[k:] + cyc[:k])
                if canon not in seen:
                    seen.add(canon)
                    cycles.append(list(canon))
        stack.pop()
        color[u] = 2

    for mid in order:
        if color.get(mid, 0) == 0:
            _dfs(mid)
    cycles.sort()
    return {"cycles": cycles, "dangling": dangling}
