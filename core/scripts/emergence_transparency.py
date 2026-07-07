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
4. goal_stagnation —— 卷核心任务停滞检测（A10 · Magnet arXiv:2607.00918 · 2026-07-07）：
   当前卷 volume_core_conflict 相关 ME 连续 N 个 cluster（默认 3 · env
   RUOYU_GOAL_STAGNATION_WINDOW 可调）零推进、且本轮候选卡也无推进项 → advisory 信号。
   与 Magnet「15 步无进展自动换目标」的刻意差异：我们**只提示人**——绝不改打分、
   绝不换目标、绝不剔候选（北极星③软牵引 ⑤不裁决）。
"""
from __future__ import annotations

import os
import re

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


# ───────────────── goal_stagnation（卷核心任务停滞·advisory 只提示不干涉） ─────────────────

_STAGNATION_WINDOW_ENV = "RUOYU_GOAL_STAGNATION_WINDOW"
_STAGNATION_WINDOW_DEFAULT = 3
# 「核心任务相关」判定阈：ME 文本与 volume_core_conflict/volume_thread 关键词重叠 ≥2 个
# （关键词是中文 2-gram，单个重叠噪声高——收敛打分维度用 ≥1 因为只影响排序软分，
#   停滞是明面 advisory 断言，判定门槛更保守）
_CORE_OVERLAP_MIN = 2

# 「已落章」cluster 状态词表（与 cluster_emergence_engine._LANDED_STATUSES 语义对齐，
# candidate 是未选中的涌现候选，绝不算写作进度）
_STAGNATION_LANDED = {"done", "已完成", "in_progress", "进行中", "writer_done",
                      "splitter_done", "writer_v2_rewriting", "done_writer_drafted"}


def stagnation_window() -> int:
    """连续零推进的判定窗口（cluster 个数）。env 非法/缺失 → 默认 3；下限 1。"""
    raw = os.environ.get(_STAGNATION_WINDOW_ENV)
    try:
        v = int(str(raw).strip())
        return v if v >= 1 else _STAGNATION_WINDOW_DEFAULT
    except (TypeError, ValueError):
        return _STAGNATION_WINDOW_DEFAULT


def goal_stagnation(shijianji: dict, dashishi: dict, current_volume, candidate_mes: list,
                    *, get_me_id, me_text, keyword_set, me_volume, window: int | None = None) -> dict:
    """卷核心任务停滞检测（确定性只读·advisory）。

    detected=True 需同时满足：
      ① 当前卷已落章 cluster ≥ window 个（不足 = 证据不够，不妄断）；
      ② 窗口内（最近 window 个已落章 cluster）推进的 ME 里**没有一个**与本卷
         volume_core_conflict/volume_thread 核心关键词重叠 ≥ _CORE_OVERLAP_MIN；
      ③ 本轮候选卡的 parent ME 也全部不相关（有推进项在候选里 = 用户下一步就能选，不算滞）。
    数据缺失（无卷标记 / 本卷缺 volume_core_conflict / 关键词为空）→ 诚实 skip。
    get_me_id/me_text/keyword_set/me_volume 由 cluster_emergence_engine 注入（口径同打分层）。
    北极星铁律：本函数只产报告字段——绝不改打分、绝不换目标、绝不增删候选。
    """
    win = window if isinstance(window, int) and window >= 1 else stagnation_window()
    out = {
        "detected": False,
        "window": win,
        "volume": current_volume,
        "_doc": ("A10 Magnet 目标停滞检测的若渝形态：只提示人·绝不自动换目标"
                 "（北极星③软牵引 ⑤不裁决）·env RUOYU_GOAL_STAGNATION_WINDOW 调窗口"),
    }
    if current_volume is None:
        out["skipped"] = "ME 无卷标记（volume）·无法定位卷核心任务·跳过"
        return out

    vol_entry = None
    for v in (dashishi.get("volumes") or []):
        if isinstance(v, dict) and v.get("vol") == current_volume:
            vol_entry = v
            break
    core_text = " ".join(str(vol_entry.get(k) or "") for k in
                         ("volume_core_conflict", "volume_thread")) if vol_entry else ""
    if not core_text.strip():
        out["skipped"] = f"卷{current_volume} 缺 volume_core_conflict/volume_thread（outline 合约字段）·跳过"
        return out
    core_kw = keyword_set(core_text)
    if not core_kw:
        out["skipped"] = "卷核心任务文本无可比对关键词·跳过"
        return out

    pool = [m for m in (dashishi.get("major_events_pool") or dashishi.get("major_events") or [])
            if isinstance(m, dict)]
    me_by_id = {get_me_id(m): m for m in pool if get_me_id(m)}

    def _core_related(me: dict) -> bool:
        return len(keyword_set(me_text(me)) & core_kw) >= _CORE_OVERLAP_MIN

    # 当前卷已落章 cluster（按 cluster 号时间序）
    landed = []  # (num, cluster_id, [advanced core-related me ids])
    for c in (shijianji.get("clusters") or []):
        if not isinstance(c, dict) or str(c.get("status") or "") not in _STAGNATION_LANDED:
            continue
        m = re.search(r"(\d+)", str(c.get("cluster_id") or ""))
        if not m:
            continue
        num = int(m.group(1))
        # 卷归属：显式 vol 字段，否则经 parent_me / ME_to_advance 的 ME volume 解析
        vol = c.get("vol")
        if vol is None:
            parent = me_by_id.get(str(c.get("parent_me") or ""))
            vol = me_volume(parent) if parent else None
        if vol is None:
            advanced_vols = [me_volume(me_by_id[mid]) for mid in (c.get("ME_to_advance") or [])
                             if mid in me_by_id]
            advanced_vols = [v for v in advanced_vols if v is not None]
            vol = advanced_vols[0] if advanced_vols else None
        if vol != current_volume:
            continue
        core_hits = [mid for mid in (c.get("ME_to_advance") or [])
                     if mid in me_by_id and _core_related(me_by_id[mid])]
        landed.append((num, c.get("cluster_id"), core_hits))
    landed.sort(key=lambda t: t[0])

    recent = landed[-win:]
    out["volume_landed_clusters"] = len(landed)
    out["checked_clusters"] = [cid for _, cid, _ in recent]
    if len(recent) < win:
        out["skipped"] = (f"卷{current_volume} 已落章 cluster 仅 {len(landed)} 个 < 窗口 {win}"
                          f"·证据不足不妄断")
        return out

    core_progress = {cid: hits for _, cid, hits in recent if hits}
    out["core_progress_in_window"] = core_progress
    candidates_core = [get_me_id(me) for me in (candidate_mes or [])
                       if isinstance(me, dict) and _core_related(me)]
    out["candidates_core_related"] = candidates_core
    if core_progress or candidates_core:
        return out

    out["detected"] = True
    out["advisory"] = (
        f"⚠️ 卷{current_volume} 核心任务疑似停滞：与 volume_core_conflict 相关的 ME 已连续 "
        f"{win} 个 cluster 零推进，且本轮候选卡也无推进项。建议候选卡向核心任务倾斜，"
        f"或在走向卡显式声明蓄势（铺垫期是合法节奏）。本信号仅提示——"
        f"绝不改打分、绝不换目标（北极星③软牵引 ⑤不裁决）。")
    return out
