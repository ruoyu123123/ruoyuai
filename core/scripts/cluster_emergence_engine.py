#!/usr/bin/env python3
"""cluster_emergence_engine.py — v24 事件簇 fluid 涌现引擎

每个 cluster 完成时调用，基于：
- 当前 cluster 完成后的 世界状态.json（factions_state + consequence_tracker + active_npc_threads）
- 用户走向卡选择 + 涟漪规则触发结果（world_evolution_apply 日志）
- 主角 character_arc_state（lie_breaking 等阶段变化）
- 大势卡剩余 ME 池

产出：下一 cluster 的 2-3 个 candidate brief，写入 `事件簇.json.clusters[N+1]`（status: "candidate"）

CLI:
    python cluster_emergence_engine.py <project> emerge --after-cluster <id>
    python cluster_emergence_engine.py <project> emerge --after-cluster cluster_001
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# P2 透明化（2026-07-06 · 借鉴 PlotPilot storyline DAG/candidate scoring）：
# 收敛子分唯一源 + 状态 delta 只读预演 + ME 依赖图健康校验。
# 本文件已逼近 1000 行软上限，判断力全部下沉在 emergence_transparency.py。
import emergence_transparency


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _event_id(me: dict) -> str:
    """读取大势事件的 canonical id。"""
    return str(me.get("id") or "") if isinstance(me, dict) else ""


def _me_volume(me: dict):
    """ME 所属卷/阶段号。优先显式 `volume` 字段，否则从 id (ME-V<N>-xx) 解析。"""
    import re as _re_v
    v = me.get("volume")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    m = _re_v.search(r"[Vv](\d+)", _event_id(me))
    return int(m.group(1)) if m else None


def find_remaining_mes(dashishi: dict, completed_mes: set) -> list:
    """从 canonical ME 池中找剩余未完成事件。"""
    pool = dashishi.get("major_events")
    if not isinstance(pool, list) or not all(isinstance(me, dict) for me in pool):
        raise ValueError("大势卡.major_events 必须是 object array")
    remaining = []
    for me in pool:
        event_id = _event_id(me)
        if not event_id:
            raise ValueError("大势卡 major_event 缺少 id")
        if me.get("status") not in {"pending", "completed"}:
            raise ValueError(f"ME {event_id}.status 必须是 pending 或 completed")
        if me.get("status") == "completed":
            continue
        if event_id in completed_mes:
            continue
        remaining.append(me)
    return remaining


# ───────────────────────── 启发式打分辅助 ─────────────────────────

# arc 阶段关键词 → 偏好的 ME 描述关键词
_STAGE_SETBACK_TOKENS = ("lie_crack", "谎言", "裂", "动摇", "怀疑", "危机", "stage_1")
_STAGE_RECOVERY_TOKENS = ("lie_broken", "觉醒", "recovery", "破局", "成长", "stage_3", "stage_4")
_SETBACK_DESC_TOKENS = ("危机", "失败", "打击", "反转", "暴露", "背叛", "重创", "崩", "挫", "setback", "陷阱", "审查")
_RECOVERY_DESC_TOKENS = ("突破", "成长", "反击", "胜", "夺回", "破局", "逆转", "翻盘", "联手", "win", "recovery")

# CJK 安全分词：抽取所有 2-gram 中文片段 + 英文/数字 token
import re as _re_mod


def _vol_of_me(me: dict) -> int:
    """返回 ME 所属卷号；无法确定时返回 0。"""
    return _me_volume(me) or 0


def _me_text(me: dict) -> str:
    """聚合 ME 的标题、描述与触发条件，供确定性关键词评分。"""
    if not isinstance(me, dict):
        return ""
    return " ".join(str(me.get(key) or "")
                    for key in ("title", "description", "trigger_when"))


def _keyword_set(text: str) -> set:
    """把任意文本拆成关键词集合：中文 2-gram + 英文单词/数字（≥2 字符）。"""
    if not text:
        return set()
    toks = set()
    # 英文 / 数字 token
    for w in _re_mod.findall(r"[A-Za-z0-9_]{2,}", text):
        toks.add(w.lower())
    # 中文连续段切 2-gram（涟漪呼应靠重叠 2-gram 命中具体名词）
    for seg in _re_mod.findall(r"[一-鿿]+", text):
        if len(seg) >= 2:
            for i in range(len(seg) - 1):
                toks.add(seg[i:i + 2])
        elif seg:
            toks.add(seg)
    return toks


def _consequence_texts(last_consequence: list) -> str:
    """把 last_consequence（dict / str 混合列表）拍平成一段文本。"""
    out = []
    for item in last_consequence or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            # 常见后果字段名兜底全捞
            for k in ("description", "effect", "consequence", "summary", "text", "note", "result"):
                if item.get(k):
                    out.append(str(item[k]))
            if not any(item.get(k) for k in ("description", "effect", "consequence", "summary", "text", "note", "result")):
                out.append(" ".join(str(v) for v in item.values() if isinstance(v, (str, int, float))))
    return " ".join(out)


def _faction_state_dict(world_state: dict) -> dict:
    """读取世界状态中的势力态；优先 factions_state，其次 global_state。"""
    if not isinstance(world_state, dict):
        return {}
    fs = world_state.get("factions_state")
    if isinstance(fs, dict) and fs:
        return fs
    gs = world_state.get("global_state")
    if isinstance(gs, dict):
        return gs
    return {}


def _extreme_factions(world_state: dict) -> list:
    """找出处于极值（power≤10 或 stress 高）的势力名，用于 faction 张力加分。"""
    extreme = []
    fs = _faction_state_dict(world_state)
    for name, val in fs.items():
        try:
            if isinstance(val, dict):
                power = val.get("power")
                stress = val.get("stress") or val.get("压力") or val.get("关注度")
                if isinstance(power, (int, float)) and power <= 10:
                    extreme.append(str(name))
                    continue
                if isinstance(stress, (int, float)) and stress >= 70:
                    extreme.append(str(name))
                    continue
            elif isinstance(val, (int, float)):
                # 标量极值：很低（濒危）或很高（高压关注）
                if val <= 10 or val >= 70:
                    extreme.append(str(name))
        except Exception:
            continue
    return extreme


def _current_advancing_vol(world_state: dict, character_arc: dict) -> int:
    """推断当前推进卷号：优先 world_state.current_cluster.vol / current_vol，否则 0（不可用）。"""
    if isinstance(world_state, dict):
        for key in ("current_vol", "advancing_vol", "vol"):
            v = world_state.get(key)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        cc = world_state.get("current_cluster")
        if isinstance(cc, dict):
            v = cc.get("vol")
            if isinstance(v, int):
                return v
    if isinstance(character_arc, dict):
        v = character_arc.get("current_vol")
        if isinstance(v, int):
            return v
    return 0


def _arc_stages(character_arc: dict) -> list:
    """收集所有角色的 current_stage_at_cluster（小写）。"""
    out = []
    if isinstance(character_arc, dict):
        for c in (character_arc.get("characters") or {}).values():
            marker = c.get("current_stage_at_cluster") if isinstance(c, dict) else None
            if marker:
                out.append(str(marker).split(":", 1)[-1].lower())
    return out


def _stage_matches(stages: list, tokens) -> bool:
    return any(any(tok in s for tok in tokens) for s in stages)


def _resolve_parents(me: dict) -> list:
    """读取 ME 前置链。

    正式字段为 prerequisites；parent_me 作为单值输入归一进前置链，避免生产端
    字段漂移导致断链 ME 被误选。
    """
    if not isinstance(me, dict):
        return []
    prereqs = me.get("prerequisites")
    if isinstance(prereqs, list) and prereqs:
        return [str(x) for x in prereqs if x]
    pm = me.get("parent_me")
    if pm:
        return [str(pm)]
    return []


def _resolve_cur_vol(world_state: dict, character_arc: dict, default_vol: int = 0) -> int:
    """解析当前推进卷号；缺显式状态时使用 ME 池推导出的默认卷。"""
    v = _current_advancing_vol(world_state, character_arc)
    return v if v else default_vol


def _dead_actor_names(project_root) -> set:
    """🔴 2026-06-27 P0-03 helper：读 character_arc_state.json 找已死/牺牲角色名集合。

    用途：emergence engine 在 find_remaining_mes 后剔除 ME 文本含死角色名的项（如李暴躁牺牲后
    ME-V1-02「李暴躁砸新手村神坛」应被剔除）。源数据缺失/字段不标准时返回空集合，
    不制造额外候选。
    """
    if not project_root:
        return set()
    try:
        from pathlib import Path as _P
        p = _P(project_root) / "_数据库" / "character_arc_state.json"
        if not p.exists():
            return set()
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    dead_keywords = {"dead", "died", "deceased", "牺牲", "死亡", "去世", "已死"}
    names = set()
    # 🔴 schema：character_arc_state.json 结构是 {"characters": {name: {...}}}·迭代 characters 子 dict
    chars = data.get("characters") if isinstance(data, dict) else None
    if isinstance(chars, dict):
        for cname, cdata in chars.items():
            if not isinstance(cdata, dict):
                continue
            status = str(cdata.get("status") or cdata.get("life_status") or "").lower()
            cs = str(cdata.get("current_stage") or "").lower()
            blob = status + " " + cs
            if any(k in blob for k in dead_keywords):
                names.add(str(cname))
    return names


def _open_questions_keywords(project_root, after_num: int) -> set:
    """🔴 2026-06-29 戏剧问题账本(PITQ/MDQ) 软牵引输入：累计到 after_num 的 open_questions
    (raised−answered) 的问题文本 → 关键词集合（喂 _score_one_me 维度7·让候选倾向推进悬置问题）。

    无 戏剧问题账本.json / 无 open → 空集合；该信号只参与候选软排序，不替代用户走向选择。
    """
    if not project_root or after_num is None:
        return set()
    try:
        import cluster_lookup  # cluster range / cluster_id 权威解析
    except Exception:
        return set()
    try:
        p = Path(project_root) / "_数据库" / "戏剧问题账本.json"
        if not p.exists():
            return set()
        ledger = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    clusters = ledger.get("clusters") if isinstance(ledger, dict) else None
    if not isinstance(clusters, dict):
        return set()
    raised_first: dict = {}
    answered_qids: set = set()
    for cid, payload in clusters.items():
        cnum = cluster_lookup.cluster_num(cid)
        if cnum is None or cnum > after_num or not isinstance(payload, dict):
            continue
        for r in payload.get("raised") or []:
            if isinstance(r, dict) and r.get("qid"):
                qid = str(r["qid"])
                if qid not in raised_first or cnum < raised_first[qid][0]:
                    raised_first[qid] = (cnum, str(r.get("question") or ""))
        for a in payload.get("answered") or []:
            if isinstance(a, dict) and a.get("qid"):
                answered_qids.add(str(a["qid"]))
    texts = " ".join(q for qid, (_, q) in raised_first.items() if qid not in answered_qids)
    return _keyword_set(texts)


def _score_one_me(
    me: dict,
    cur_vol: int,
    completed_me_ids: set,
    stages: list,
    consequence_kw: set,
    extreme_factions: list,
    milestone_kw: set = None,
    open_questions_kw: set = None,
) -> tuple:
    """给单个 ME 打分。返回 (score, reasons)。"""
    score = 0
    reasons = []
    event_id = _event_id(me)
    me_text = _me_text(me)
    me_kw = _keyword_set(me_text)

    # 1. vol 连续性
    if cur_vol:
        if _vol_of_me(me) == cur_vol:
            score += 30
            reasons.append(f"vol{cur_vol} 连续（与当前推进卷一致）")

    # 2. 前置链：ME 的 prerequisites/parent_me 全部指向已完成 ME → +25·有未完成 → 硬剔除
    # 🔴 2026-06-27 P0-01 修：原读 me.get("parent_me") 但 producer (gen_creative) 实写 prerequisites
    #   字段错配·+25 bonus dead code 永不触发·V1-02 等 ME 被错跳的根因之一。
    parents = _resolve_parents(me)
    if parents and completed_me_ids is not None:
        unmet = [p for p in parents if p not in completed_me_ids]
        if not unmet:
            score += 25
            reasons.append(f"承接已完成主线 ME「{','.join(parents)}」（前置链全部满足）")
        else:
            score -= 100  # 硬剔除·防断链跳子节点
            reasons.append(f"⚠️ 前置 ME「{','.join(unmet)}」未完成·已剔除（防断链）")

    # 3. arc 阶段匹配
    setback_hits = [t for t in _SETBACK_DESC_TOKENS if t in me_text]
    recovery_hits = [t for t in _RECOVERY_DESC_TOKENS if t in me_text]
    if _stage_matches(stages, _STAGE_SETBACK_TOKENS) and setback_hits:
        score += 35
        reasons.append(f"主角处于谎言裂痕期 → 制造 setback/冲突（命中：{','.join(setback_hits[:3])}）")
    if _stage_matches(stages, _STAGE_RECOVERY_TOKENS) and recovery_hits:
        score += 35
        reasons.append(f"主角处于觉醒/恢复期 → 推 recovery/win（命中：{','.join(recovery_hits[:3])}）")

    # 4. 涟漪呼应：ME 文本与最近后果文本关键词重叠
    if consequence_kw and me_kw:
        overlap = me_kw & consequence_kw
        if overlap:
            inc = min(40, 8 * len(overlap))
            score += inc
            sample = list(overlap)[:4]
            reasons.append(f"呼应最近涟漪后果（重叠：{','.join(sample)}）")

    # 5. faction 张力：ME 提到处于极值的势力名
    #    global_state 键常是「上级审查组_关注度」这类「主体_指标」复合名，
    #    直接全名 substring 多半命不中 → 同时拿下划线/常见指标后缀切出的主体片段去匹配。
    for fname in extreme_factions:
        if not fname:
            continue
        frags = {fname}
        # 切 '_' 分段
        for seg in str(fname).split("_"):
            if len(seg) >= 2:
                frags.add(seg)
        # 剥常见指标后缀，留主体名
        for suf in ("关注度", "存续状态", "活跃度", "暴露度", "压力", "状态", "度"):
            if fname.endswith(suf) and len(fname) > len(suf) + 1:
                frags.add(fname[: -len(suf)].rstrip("_"))
        hit = next((fr for fr in frags if fr and len(fr) >= 2 and fr in me_text), None)
        if hit:
            score += 20
            reasons.append(f"涉及极值势力「{hit}」（power/stress 处于临界 → 张力点）")
            break  # 单 ME 只加一次 faction 分，避免叠爆

    # 6. 大势收敛（2026-05-29 北极星 P2 [H2-trend]）：ME 推进本卷「未达成 key_milestone」→ 朝卷终点收敛。
    #    与涟漪/arc 同量级（≤35），**只影响 top-3 候选排序展示，绝不自动锁定**（守原则2+5：
    #    最终仍由用户走向卡选，不让收敛硬性盖过涟漪涌现）。
    if milestone_kw and me_kw:
        # 2026-05-29 复审 W2：per-overlap 12→8 与涟漪(8/重叠)同量级，使「不盖过涟漪」名副其实。
        # 2026-07-06 P2 透明化：公式唯一实现在 emergence_transparency.convergence_subscore
        # （decision_basis.convergence_score 与本维度加分同源同值·绝不双口径）。
        conv_score, conv_hits = emergence_transparency.convergence_subscore(me_kw, milestone_kw)
        if conv_score:
            score += conv_score
            reasons.append(f"大势收敛：推进未达成卷里程碑（重叠：{','.join(conv_hits[:4])}）")

    # 7. 🔴 2026-06-29 戏剧问题账本(PITQ/MDQ) 软牵引：ME 文本与「当前悬而未决核心问题」关键词重叠
    #    → 让候选倾向推进/回答悬置问题（读者想知道答案）。**软维度·不硬筛**（仿涟漪呼应·封顶 24·
    #    与涟漪/收敛同量级·绝不盖过其它信号·绝不 -100 硬剔除）·用户走向卡终裁（守北极星③大势已定）。
    if open_questions_kw and me_kw:
        q_overlap = me_kw & open_questions_kw
        if q_overlap:
            score += min(24, 8 * len(q_overlap))
            reasons.append(f"推进悬置核心问题（读者想知道答案·重叠：{','.join(list(q_overlap)[:4])}）")

    return score, reasons


def _decision_basis_for_me(
    me: dict,
    *,
    reasons: list,
    score,
    current_volume,
    last_consequence: list,
    milestone_kw: set,
    open_questions_kw: set,
    volume_transition_hint: str | None,
    ripple_rules_json: dict | None,
) -> dict:
    """Build an explainable basis for the next-cluster choice card.

    Clean-room synthesis from mature planning systems: candidate ranking should
    expose the signals it used, but the final branch remains the user's choice.
    This is a display/traceability contract for the single pause point, not a
    second selector.

    2026-07-06 P2 透明化扩展（借鉴 PlotPilot candidate scoring）：
    - convergence_score：dim-6 大势收敛子分独立数值（与 _score_one_me 内加分同源，
      公式唯一实现在 emergence_transparency）——用户能看到这张卡对卷收敛的贡献。
    - state_delta_preview：候选选中后会触发的涟漪规则只读预演（绝不写库）。
    两字段只解释不裁决：不改变选择逻辑/排序，不自动替用户选（北极星⑤）。
    """
    me_text = _me_text(me)
    me_kw = _keyword_set(me_text)
    convergence_score, _ = emergence_transparency.convergence_subscore(
        me_kw, milestone_kw or set())
    ripple_sources = []
    for item in last_consequence[-8:] if isinstance(last_consequence, list) else []:
        if isinstance(item, str):
            ripple_sources.append(item[:120])
        elif isinstance(item, dict):
            txt = _consequence_texts([item]).strip()
            if txt:
                ripple_sources.append(txt[:120])
    milestone_hits = sorted(me_kw & (milestone_kw or set()))[:6]
    question_hits = sorted(me_kw & (open_questions_kw or set()))[:6]
    basis = {
        "score": score,
        "rank_reasons": [str(r) for r in (reasons or [])],
        "source_me": {
            "id": _event_id(me),
            "title": me.get("title") or "",
            "volume": _me_volume(me),
            "is_volume_finale": bool(me.get("is_volume_finale")),
        },
        "continuity": {
            "current_volume": current_volume,
            "prerequisites": _resolve_parents(me),
            "volume_transition_hint": volume_transition_hint,
        },
        "ripple_evidence": ripple_sources,
        "open_question_hits": question_hits,
        "milestone_hits": milestone_hits,
        "convergence_score": convergence_score,
        "state_delta_preview": emergence_transparency.state_delta_preview(
            _event_id(me), me_text, ripple_rules_json or {}),
        "user_choice_policy": "排序只用于展示依据；下一故事块仍由走向卡选择唯一确定",
    }
    return basis


def select_candidate_mes(
    remaining_mes: list,
    world_state: dict,
    character_arc: dict,
    last_consequence: list,
    completed_me_ids: set = None,
    milestone_kw: set = None,
    default_vol: int = 0,
    open_questions_kw: set = None,
) -> list:
    """启发式：从剩余 ME 中给每个打分排序，取 top 3 个 candidate。

    打分维度（基于世界状态 + 涟漪 + arc 阶段 + 主线链）：
    1. vol 连续性 —— ME 所属卷 == 当前推进卷（+30）
    2. parent_me 链 —— ME 承接已完成 ME（+25）
    3. arc 阶段匹配 —— 谎言裂痕期偏 setback / 觉醒期偏 recovery（+35）
    4. 涟漪呼应 —— ME 文本与最近后果关键词重叠（+8/重叠，封顶 40）
    5. faction 张力 —— ME 涉及处于极值（power≤10 或 stress 高）的势力（+20）

    默认排序：所有信号缺失 / 打分全 0 → 按 ME 池设计顺序取 remaining_mes[:3]。
    返回的每个 ME（浅拷贝）附 `_emergence_score` + `_emergence_reasons`（list[str]）。
    """
    if not remaining_mes:
        return []

    completed_me_ids = completed_me_ids or set()
    # 用 _resolve_cur_vol 的 default_vol 补齐显式 current_vol 缺口，保证卷连续性信号可生效。
    cur_vol = _resolve_cur_vol(world_state, character_arc, default_vol)
    stages = _arc_stages(character_arc)
    consequence_kw = _keyword_set(_consequence_texts(last_consequence))
    extreme_factions = _extreme_factions(world_state)

    scored = []
    for idx, me in enumerate(remaining_mes):
        # 2026-05-29 复审修复（L8）：循环首行加 isinstance 守卫 · 非 dict ME 跳过不打分（防 _score_one_me 内裸 .get() 崩）。
        if not isinstance(me, dict):
            continue
        score, reasons = _score_one_me(
            me, cur_vol, completed_me_ids, stages, consequence_kw, extreme_factions, milestone_kw,
            open_questions_kw
        )
        scored.append((score, idx, me, reasons))

    # 2026-05-29 复审修复（L8）：scored 全空（remaining 全是非 dict 被过滤）→ 无可涌现 ME，返回 []。
    if not scored:
        return []

    # 所有打分为 0（信号全缺）→ 按设计顺序取前 3（只取已过滤的 dict ME）。
    if all(s == 0 for s, _, _, _ in scored):
        out = []
        for _, _, me, _ in scored[:3]:
            me2 = dict(me) if isinstance(me, dict) else me
            if isinstance(me2, dict):
                me2["_emergence_score"] = 0
                me2["_emergence_reasons"] = ["无世界状态/涟漪/arc 信号 → 按 ME 池设计顺序取前 3"]
            out.append(me2)
        return out

    # 按分降序；同分按原顺序（idx 升序）稳定排序 = 保留设计推进顺序
    scored.sort(key=lambda t: (-t[0], t[1]))

    # 🔴 2026-06-27 P0-01 配套：有正分候选时·剔除负分(prereq 未满足被 -100 硬剔/finale 降分)候选·
    # 防 prereq-blocked ME 被当合法走向卡选项展示给用户(实证 cluster_006 涌现 V1-08/-92 V1-06/-100)。
    # 全负分(罕见·链断)才退回展示前 3·保留可解释 reasons。
    positives = [t for t in scored if t[0] >= 0]
    display = positives if positives else scored

    out = []
    for score, _, me, reasons in display[:3]:
        me2 = dict(me) if isinstance(me, dict) else me
        if isinstance(me2, dict):
            me2["_emergence_score"] = score
            me2["_emergence_reasons"] = reasons or ["（基础分）按设计顺序保留"]
        out.append(me2)
    return out


def me_to_cluster_brief(me: dict, cluster_id: str, ord: int, world_state: dict, decision_basis: dict | None = None) -> dict:
    """把 ME 转成 cluster brief 候选 (scene_storyboard 仅雏形)。

    ME 没有 title/name 时，从 description 提取标题。
    """
    event_id = _event_id(me)
    title = me.get("title") or ""
    if not title:
        # description 前 30 字（截断在标点处）作为标题。
        desc = me.get("description", "")
        title = desc[:30].rstrip("，。！？、")
        if len(desc) > 30:
            title += "…"
    # v24 fluid: 把涌现打分理由透传进 scope_summary，让用户看到「为什么涌现这个」
    reasons = me.get("_emergence_reasons") or []
    score = me.get("_emergence_score")
    why = ""
    if reasons:
        why = f" 〔涌现理由(分{score}): " + "；".join(str(r) for r in reasons) + "〕"
    # 2026-05-29 流程贯通（断点 3b）：删 v27 禁止的章数/字数死锁字段
    # （expected_word_range / estimated_chapters / chapter_range）。
    # 依据 CLAUDE.md「📐 大纲章数 fluid」+ memory feedback_v27_writer_freestyle_splitter_word_cut：
    # 章数由 writer 自由发挥 + splitter 按字数切完自动回填，涌现阶段不得预设。
    # 保留 _emergence_score / _emergence_reasons（涌现可解释性，非章数死锁）。
    # 🆕 2026-06-03 卷=阶段触发点：把 ME 的卷级语义透传进 brief，让卷末 finale cluster 被标记，
    # build_manifest 据 is_volume_finale 给 writer 注入「卷末高烈度转折(禁平稳收束)」指令，
    # stakes_delta 让 writer 知道本小走向相对前块的强度增量(避免平铺重复)。
    is_finale = bool(me.get("is_volume_finale"))
    scope = f"[CANDIDATE {ord}] 围绕 ME「{title}」展开。{me.get('description', '')}{why}"
    if is_finale:
        scope += " 〔🔴卷末小走向(volume_finale)：本 cluster 收束本阶段·走高烈度转折(反派现身/真相揭露/主角阶段跃迁)·禁平稳收束〕"
    # 🆕 R20 W9 Batch-AA P1: 起承转结无冲突模式
    # me.intent ∈ {healing/contemplative/iyashikei/zen} → 默认 kishotenketsu_4act 模式
    # build_manifest 据此注入四段 directive · hook_strength 在此模式按 cluster 节奏调整参考阈值。
    # (防 v26 末段强钩误伤治愈结尾)。intent 显式 = "in_medias_res" 等不覆盖。
    intent_val = (me.get("intent") or "").strip().lower()
    _KISHO_INTENTS = {"healing", "contemplative", "iyashikei", "zen", "治愈", "禅"}
    if intent_val in _KISHO_INTENTS:
        narrative_mode = "kishotenketsu_4act"
    else:
        narrative_mode = me.get("narrative_mode") or ""
    return {
        "cluster_id": cluster_id,
        "parent_me": event_id,
        "scope_summary": scope,
        "_emergence_score": score,
        "_emergence_reasons": reasons,
        "decision_basis": decision_basis or {},
        "status": "candidate",
        "ME_to_advance": [event_id],
        "volume": _me_volume(me),
        "is_volume_finale": is_finale,
        "stakes_delta": me.get("stakes_delta", ""),
        "intent": intent_val or None,
        "narrative_mode": narrative_mode or None,
        # 🆕 R23 W11 Batch-HH P1 (2026-06-22): 信念更新意图 + LC-NE 边界锐度
        # belief_update_intent ∈ {preserve, update}：update=要求末段 PE 收束（强反转）·
        #   preserve=保留信念（红鲱鱼 / 推理悬念延后）·None=不主张（默认）
        # event_boundary_sharpness ∈ {sharp, dull, default}：volume_finale 强制 sharp·
        #   同 cluster 内 scene 切默认 dull·default=不指定（writer 默认走 dull）
        "belief_update_intent": ("update" if is_finale else None),
        "event_boundary_sharpness": ("sharp" if is_finale else "default"),
        "_doc": f"v24 fluid 涌现 · 等待用户从 {ord} 个 candidate 中选 1 个 → status 改 in_progress",
        "scene_storyboard": [],  # 雏形 · 用户选定后再让 outline-planner 详化
        "anchor_props": [],
        "foreshadowing_to_plant": [],
        # 🆕 R22 W10 Batch-FF: CBT premise_blend_card 空骨架·outline-planner 选定 candidate 后填
        # 〔确保候选 brief 至少 1-2 scene 体现 emergent_structure·writer manifest 注入下游〕
        "premise_blend_card": {
            "_doc": "Fauconnier&Turner CBT 概念整合卡·R22·candidate 空骨架·outline-planner 详化时填",
            "blend_type": "",
            "input_space_A": {"frame": "", "signature_lexemes": []},
            "input_space_B": {"frame": "", "signature_lexemes": []},
            "generic_space": "",
            "emergent_structure": [],
            "vital_relations_compressed": []
        },
        "research_ref": {
            "_doc": "v23.1 选定 candidate 后必须独立调研 cluster_brief"
        }
    }


# ───────────────────── 完本终态（P0-2 缺漏修复 · 2026-06-12） ─────────────────────
# 缺漏报告结论：ME 池耗尽时旧版走 [FAIL] exit 1——把「大势走完=完本」误当故障，
# 下游 save-state step11 被假失败卡住、GUI 还继续建议写下一块。根治：完本 ≠ 失败。
BOOK_COMPLETE_MARKER = ".book_complete.json"


def write_book_complete_marker(project_root: Path, last_cluster: str) -> Path:
    """ME 池真耗尽（无可用 ME 且无 finale 可涌现）= 完本，写 _数据库/.book_complete.json。

    标记供 GUI scan_project 识别（提示导出全文·不再建议写下一块）。
    幂等：标记已存在则不覆盖（保留首次完本时间，重跑 emerge 不刷新 completed_at）。
    """
    db = _db_dir_local(project_root)
    marker = db / BOOK_COMPLETE_MARKER
    if not marker.exists():
        payload = {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "reason": "ME 池耗尽·大势已走完",
            "last_cluster": str(last_cluster or ""),
        }
        marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return marker


def emerge_next_cluster(project_root: Path, after_cluster_id: str) -> dict:
    """主入口。"""
    db = project_root / "_数据库"
    shijianji_path = db / "事件簇.json"
    dashishi_path = db / "大势卡.json"
    world_state_path = db / "世界状态.json"
    arc_state_path = db / "character_arc_state.json"

    shijianji = load_json(shijianji_path, {"clusters": []})
    dashishi = load_json(dashishi_path, {})
    world_state = load_json(world_state_path, {})
    character_arc = load_json(arc_state_path, {})
    # P2 透明化（2026-07-06）：涟漪规则只读加载，供 decision_basis.state_delta_preview
    # 做「候选选中后会触发哪些涟漪」的确定性预演。本函数全程不写回该文件。
    ripple_rules_json = load_json(db / "涟漪规则.json", {}) or {}

    # P2（2026-07-06）ME prerequisites 依赖图健康校验：环（环上 ME 互为前置永不可触发）
    # + 悬挂引用（前置指向不存在的 ME id → 打分层 -100 硬剔成死链）。
    # advisory：stderr 显式报告 + 输出 JSON 带 dag_health 段，不硬失败不静默丢——
    # 大势卡是用户/outline 的创作产物，机器只报告不裁决（北极星⑤）。
    _pool_for_dag = dashishi.get("major_events") or []
    dag_health = emergence_transparency.me_dag_health(
        _pool_for_dag, get_id=_event_id, get_parents=_resolve_parents)
    if dag_health["cycles"] or dag_health["dangling"]:
        print(f"[emergence][dag_health] ⚠️ ME 依赖图异常（advisory·不阻断·请检查大势卡 prerequisites）: "
              f"cycles={dag_health['cycles']} dangling={dag_health['dangling']}", file=sys.stderr)

    # 收集已完成 cluster 的 ME_to_advance
    completed_mes = set()
    for c in shijianji.get("clusters", []):
        if c.get("status") in ("done", "in_progress", "writer_v2_rewriting", "done_writer_drafted"):
            for me in c.get("ME_to_advance", []) or []:
                completed_mes.add(me)

    # 找剩余 ME
    remaining = find_remaining_mes(dashishi, completed_mes)
    if remaining:
        # 复验修（完本单向门）：用户加了新 ME（续写新卷）→ 清陈旧完本标记·
        # 否则 GUI 永远显示已完本不再推荐动作。与幂等写对称。
        _stale = project_root / "_数据库" / BOOK_COMPLETE_MARKER
        if _stale.exists():
            try:
                _stale.unlink()
                print("[emergence] 检测到新 ME·已清除陈旧完本标记（重新开张）")
            except OSError:
                pass
    if not remaining:
        # 🎉 完本终态（P0-2 · 2026-06-12）：ME 真耗尽 = 大势已走完 = 完本，不是故障。
        # 写完本标记 + 返回 book_complete=True（main() 据此 exit 0 · 下游 step11 自然过 ·
        # 没有候选 → 走向卡不弹）。注意 ok 仍为 False——「没涌现出新 cluster」语义不变，
        # 既有 consumer 检查 ok 不会误以为有 candidate。
        marker = write_book_complete_marker(project_root, after_cluster_id)
        # 🔴 复验修（orchestrator 路径捏造走向卡）：完本也要写 step11 的 WAL 产物——
        # 否则 expected_outputs 缺失卡死 plan + judge 无输入被 required_keys 逼着
        # 编造 candidates → 弹捏造走向卡。空 candidates + book_complete 标志：
        # orchestrator 完本短路据此跳过 judge/pause（见 orchestrator step 头检测）。
        try:
            import re as _re
            _m = _re.search(r"(\d+)", after_cluster_id or "")
            _next_key = f"{int(_m.group(1)) + 1:03d}" if _m else "next"
            _wal = project_root / "_数据库" / ".wal"
            _wal.mkdir(parents=True, exist_ok=True)
            _payload = {"book_complete": True, "candidates": [],
                        "reason": "ME 池耗尽·大势已走完（完本非故障）",
                        "dag_health": dag_health}
            for _fn in (f"cluster_{_next_key}_emergence.json",
                        f"cluster_{_next_key}_brief_candidates.json"):
                (_wal / _fn).write_text(
                    json.dumps(_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        except Exception as _e:
            # 磁盘满/只读等极端故障——别静默吞：下游 step11 会因 expected_outputs
            # 缺失报 FileNotFoundError，这行警告是唯一可读的根因线索
            print(f"[WARN] 完本 WAL 写入失败（step11 可能因产物缺失停下）: {_e}")
        return {"ok": False, "book_complete": True,
                "error": "大势卡 ME 池已全部完成，无新 cluster 可涌现",
                "completed_count": len(completed_mes),
                "dag_health": dag_health,
                "marker_path": str(marker)}

    # 🆕 2026-06-03 卷=阶段触发点：硬过滤到「当前阶段(卷)」的剩余 ME——核心任务未解前
    # 只在本卷内涌现小走向，绝不跳到下一卷/新副本（根治「单 cluster 塌缩成整副本/整阶段」）。
    # current_volume = 剩余 ME 中最小卷号(最早未收束阶段)；无 volume 标记的 ME 不参与卷过滤。
    _tagged = [v for v in (_me_volume(me) for me in remaining) if v is not None]
    current_volume = min(_tagged) if _tagged else None
    volume_transition_hint = None
    if current_volume is not None:
        in_vol = [me for me in remaining if _me_volume(me) in (current_volume, None)]
        non_finale = [me for me in in_vol if not me.get("is_volume_finale")]
        if in_vol and not non_finale:
            # 本卷只剩 volume_finale → 阶段触发点临近：给出软提示，最终由用户走向卡确认。
            volume_transition_hint = (
                f"⚠️ 阶段触发点临近：卷{current_volume} 核心任务剩余 ME 仅余 volume_finale。建议下个 cluster "
                f"走卷末高烈度转折(反派现身/真相揭露/主角阶段跃迁)收束本阶段，之后换卷至卷{current_volume + 1}"
                f"(新副本/新阶段)。绝不硬切·由你确认换卷信号(核心任务解决+力量/舞台/反派跃迁任一)。")
        remaining = in_vol  # 硬过滤：本卷内涌现小走向

    # 🔴 2026-06-27 P0-03a finale progress gate：vol_progress < 70% + 还有 non_finale ME → 剔除 finale
    # 治 cluster_002(2/10) 就涌现 V1-10 finale 的 bug。阈值用大势卡 _metadata.cluster_count_per_volume(用户设)。
    _FINALE_PROGRESS_GATE = 0.7
    try:
        _cpv = int(dashishi.get("_metadata", {}).get("cluster_count_per_volume", 10))
        if _cpv > 0 and current_volume is not None:
            _done_in_vol = sum(1 for c in shijianji.get("clusters", [])
                               if isinstance(c, dict) and c.get("status") != "candidate"
                               and (c.get("vol") == current_volume or _me_volume(
                                   next((m for m in (dashishi.get("major_events") or [])
                                         if _event_id(m) == c.get("parent_me")), {})) == current_volume))
            _vol_progress = _done_in_vol / _cpv
            _non_finale = [m for m in remaining if not m.get("is_volume_finale")]
            if _vol_progress < _FINALE_PROGRESS_GATE and _non_finale:
                _before = len(remaining)
                remaining = _non_finale
                if _before > len(remaining):
                    print(f"[emergence] vol{current_volume} 进度 {_vol_progress:.0%} < {_FINALE_PROGRESS_GATE:.0%}·剔除 {_before - len(remaining)} 个 finale ME（防过早收束）")
    except Exception as _e:
        print(f"[emergence] finale progress gate 跳过: {_e}")

    # 🔴 2026-06-27 P0-03b 死角色 gate：ME 文本提及已死角色 → 剔除（V1-02 李暴躁砸神坛在李死后死锁）
    _dead = _dead_actor_names(project_root)
    if _dead:
        _before = len(remaining)
        remaining = [m for m in remaining if not any(name in _me_text(m) for name in _dead)]
        if _before > len(remaining):
            print(f"[emergence] 剔除 {_before - len(remaining)} 个 ME（提及已死角色: {','.join(sorted(_dead))}）")

    # 收集 last consequence（最后一个 cluster 的涟漪后果）
    last_consequence = []
    if isinstance(world_state.get("consequence_tracker"), dict):
        last_consequence = list(world_state["consequence_tracker"].values())[-5:]
    elif isinstance(world_state.get("consequence_tracker"), list):
        last_consequence = world_state["consequence_tracker"][-5:]
    # 2026-05-29 北极星 P1：并入涟漪叙事后果（混合式·叙事 ripple），让「下一 cluster 涌现」
    # 真正由【涟漪因果】驱动（_consequence_texts 认 dict 的 "text" 字段做关键词共鸣打分），
    # 而非仅 consequence_tracker。这是「涟漪为核心 + 因果触发事件」落到 emergence 的关键。
    narr_cons = world_state.get("narrative_consequences")
    if isinstance(narr_cons, list) and narr_cons:
        last_consequence = list(last_consequence) + narr_cons[-8:]

    # 2026-05-29 北极星 P2 [H2-trend]：取当前推进卷的「未达成 key_milestones」关键词 → 收敛打分维度。
    # 大势已定：让涌现候选朝本卷固定终点收敛；只影响排序，不锁定用户选择。
    milestone_kw = set()
    # cur_vol 兜底：世界状态/character_arc 无人写 current_vol(契约债)→ 用 emerge 已算出的
    # current_volume(=剩余 ME 最小卷号·line530·权威 _me_volume)·否则收敛维度全生产环境恒哑火。
    cur_vol = _current_advancing_vol(world_state, character_arc) or current_volume
    if cur_vol:
        for v in (dashishi.get("volumes") or []):
            if isinstance(v, dict) and v.get("vol") == cur_vol:
                # 收敛源统一读取：key_milestones/ending_state 以及 gen_creative 产出的卷级字段。
                # 实写 volume_core_conflict/volume_thread/volume_finale_signal → 全捞·避免维度恒空。
                kms = v.get("key_milestones") or []
                kms_text = " ".join(str(k) for k in kms) if isinstance(kms, list) else str(kms)
                conv_text = " ".join(str(v.get(k, "")) for k in (
                    "ending_state", "volume_core_conflict", "volume_thread", "volume_finale_signal"))
                milestone_kw = _keyword_set(kms_text + " " + conv_text)
                break

    # 🔴 2026-06-29 戏剧问题账本(PITQ/MDQ) 软牵引：累计 open_questions 关键词 → 让候选倾向推进悬置问题。
    # 无账本 → 空集合。该软维度不硬筛，用户走向卡终裁（守北极星③）。
    after_num_for_q = None
    if after_cluster_id:
        import re as _re_q
        _m = _re_q.search(r"(\d+)", str(after_cluster_id))
        if _m:
            try:
                after_num_for_q = int(_m.group(1))
            except ValueError:
                after_num_for_q = None
    open_questions_kw = _open_questions_keywords(project_root, after_num_for_q)

    # 选 candidate ME（传 completed_mes 供 parent_me 链打分 + milestone_kw 供收敛打分）
    # 🔴 2026-06-27 P0-02 修：传 default_vol=current_volume·治 +30 vol 连续 bonus dead code
    candidate_mes = select_candidate_mes(remaining, world_state, character_arc, last_consequence,
                                         completed_me_ids=completed_mes, milestone_kw=milestone_kw,
                                         default_vol=current_volume or 0,
                                         open_questions_kw=open_questions_kw)
    if not candidate_mes:
        return {"ok": False, "error": "无符合启发式条件的 candidate ME"}

    # 生成 cluster brief 候选
    # v26 修复: --after-cluster 可传 "cluster_002" 或纯数字 "002" / 2 · 之前 if "_" in id 漏识别裸数字
    after_num = 0
    if after_cluster_id:
        import re as _re
        m = _re.search(r"(\d+)", str(after_cluster_id))
        if m:
            try:
                after_num = int(m.group(1))
            except Exception:
                after_num = len([c for c in shijianji.get("clusters", []) if c.get("status") != "candidate"])
        else:
            after_num = len([c for c in shijianji.get("clusters", []) if c.get("status") != "candidate"])

    next_num = after_num + 1
    next_cluster_id = f"cluster_{next_num:03d}"

    candidates_briefs = []
    for i, me in enumerate(candidate_mes, 1):
        basis = _decision_basis_for_me(
            me,
            reasons=me.get("_emergence_reasons") or [],
            score=me.get("_emergence_score"),
            current_volume=current_volume,
            last_consequence=last_consequence,
            milestone_kw=milestone_kw,
            open_questions_kw=open_questions_kw,
            volume_transition_hint=volume_transition_hint,
            ripple_rules_json=ripple_rules_json,
        )
        brief = me_to_cluster_brief(me, f"{next_cluster_id}_candidate_{i}", i, world_state, decision_basis=basis)
        candidates_briefs.append(brief)

    # A4 pairwise 偏好排序(BPR·RUOYU_PREF_RANKER=1 才生效)：只给每个 candidate
    # 就地加 preference_score/preference_rank_hint 参考字段，candidates_briefs 的生成顺序/
    # 数量/其余内容不变——涌现排序仍由上面的启发式(_score_one_me)决定。门控关或无已训练
    # weights 时不写 preference 标注。逻辑全在 preference_ranker.py（cluster_emergence_engine.py 已逼近
    # 1000 行软上限，新增判断力不下沉进这里）。
    try:
        import preference_ranker as _pref_ranker
        _pref_ranker.annotate_candidates(project_root, candidates_briefs)
    except ImportError:
        pass

    # 🆕 A10 目标停滞检测（Magnet arXiv:2607.00918 · 2026-07-07）：卷核心任务相关 ME 连续
    # N 个 cluster（默认 3·env RUOYU_GOAL_STAGNATION_WINDOW）零推进且候选卡也无推进项 →
    # advisory 信号段。逻辑在 emergence_transparency.goal_stagnation（本文件超 1000 行软上限，
    # 判断力不下沉进这里）。只加输出字段——绝不改打分/绝不换目标/绝不增删候选（北极星③⑤）。
    goal_stagnation = emergence_transparency.goal_stagnation(
        shijianji, dashishi, current_volume, candidate_mes,
        get_event_id=_event_id, me_text=_me_text, keyword_set=_keyword_set, me_volume=_me_volume)
    if goal_stagnation.get("detected"):
        print(f"[emergence][goal_stagnation] {goal_stagnation['advisory']}")

    # 写入 emergence.json WAL
    emergence_path = db / ".wal" / f"{next_cluster_id}_emergence.json"
    emergence_path.parent.mkdir(parents=True, exist_ok=True)
    emergence_data = {
        "_schema": "cluster_emergence_v24",
        "after_cluster": after_cluster_id,
        "next_cluster_id": next_cluster_id,
        "emerged_at": datetime.now().isoformat(timespec="seconds"),
        "completed_mes_count": len(completed_mes),
        "remaining_mes_count": len(remaining),
        "current_volume": current_volume,
        "volume_transition_hint": volume_transition_hint,
        "dag_health": dag_health,
        "goal_stagnation": goal_stagnation,
        "candidates": candidates_briefs,
        "world_state_snapshot": {
            "factions_state": world_state.get("factions_state", {}),
            "last_consequences": last_consequence,
            "active_npc_threads_count": len(world_state.get("active_npc_threads", []))
        },
        "character_arc_snapshot": {
            "characters": [
                {"id": name, "current_stage_at_cluster": data.get("current_stage_at_cluster")}
                for name, data in (character_arc.get("characters") or {}).items()
                if isinstance(data, dict)
            ]
        },
        "_next_action": "主代理展示 candidates 给用户选 1 个 → 写入 事件簇.json.clusters[N+1] (status: in_progress)"
    }
    emergence_path.write_text(json.dumps(emergence_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "next_cluster_id": next_cluster_id,
        "candidates_count": len(candidates_briefs),
        "emergence_path": str(emergence_path),
        "dag_health": dag_health,
        "goal_stagnation": goal_stagnation,
        "summary": [f"{c['cluster_id']}: ME {c['parent_me']} → {c['scope_summary'][:60]}" for c in candidates_briefs]
    }


# 2026-05-29 复审修复（C1-a · SC-3/SC-6）：start-ch 必须算「上一个已落章 cluster 末章+1」，
# 不能反查目标 cluster 自身尚未回填的 chapter_range（否则空输出 exit2 → 下游 build_manifest int("") 崩）。
# 「已落章」状态词表（SC-6 中英文都认）：done / 已完成 / in_progress / 进行中，且 chapter_range 有值。
_LANDED_STATUSES = ("done", "已完成", "in_progress", "进行中", "writer_done",
                    "splitter_done", "writer_v2_rewriting", "done_writer_drafted")


def _scan_max_chapter_dir(project_root: Path) -> int:
    """SC-3 兜底：扫 章节/第NNN章 目录取最大章号。无目录返回 0。"""
    import re as _re
    chap_dir = project_root / "章节"
    if not chap_dir.exists():
        return 0
    mx = 0
    for p in chap_dir.iterdir():
        if not p.is_dir():
            continue
        m = _re.search(r"第\s*(\d+)\s*章", p.name)
        if m:
            try:
                mx = max(mx, int(m.group(1)))
            except Exception:
                continue
    return mx


def resolve_start_ch(project_root: Path, cluster_key) -> int:
    """SC-3：本 cluster 起首章 = 上一个已落章 cluster 末章 + 1；cluster_001 特判 = 1。

    来源优先级：
      1. 事件簇.json：取所有 num < 目标 num 且 status∈已落章词表 且 chapter_range 有值的
         cluster，其 chapter_range[1] 的最大值 + 1。
      2. 兜底：扫 章节/第NNN章 目录最大章号 + 1。
      3. 都没有 → 1（首块）。
    禁止反查目标 cluster 自身尚未回填的 range（C1-a 根因）。
    """
    import cluster_lookup as _cl
    target_num = _cl.cluster_num(cluster_key)
    if target_num is None or target_num <= 1:
        # cluster_001 / 无法解析 → 起首章特判为 1
        return 1

    db = _db_dir_local(project_root)
    ec = load_json(db / "事件簇.json", {}) or {}
    best_last = 0
    for c in ec.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        c_num = _cl.cluster_num(c.get("cluster_id"))
        if c_num is None or c_num >= target_num:
            continue
        status = c.get("status")
        cr = c.get("chapter_range") or []
        landed_range = isinstance(cr, list) and len(cr) == 2 and isinstance(cr[1], int)
        # SC-6：中英文 status 都算已落章；只要 chapter_range 有值也视为已落章（已完成|done|进行中且有range）
        if (status in _LANDED_STATUSES) and landed_range:
            best_last = max(best_last, cr[1])
        elif landed_range and status not in ("candidate", "未涌现", "pending", "已规划"):
            # range 有值但 status 非「未落章」语义 → 也并入（兜底，避免漏算）
            best_last = max(best_last, cr[1])

    if best_last > 0:
        return best_last + 1

    # 兜底：扫章节目录
    mx = _scan_max_chapter_dir(project_root)
    if mx > 0:
        return mx + 1
    return 1


def _db_dir_local(project_root: Path) -> Path:
    """本地 _数据库 定位（接受 项目根 或 直接传 _数据库 路径）。"""
    root = Path(project_root)
    if root.name == "_数据库":
        return root
    cand = root / "_数据库"
    return cand if cand.exists() else root


_ACTIONS = ("emerge", "start-ch")


def main():
    # cluster-only 辅助动作：
    # - emerge：完成当前 cluster 后，产出下一 cluster 的 WAL 候选产物。
    # - start-ch：根据已落地 cluster range 推导当前 cluster 的输出起点，供 splitter 使用。
    #
    # 支持两种位置参顺序：
    #   emerge:   <project> emerge --after-cluster <id>   （project 在前）
    #   start-ch: start-ch <project> --cluster <key>       （action 在前）
    # 故 project/action 不固定顺序 → 手动从位置参里按 _ACTIONS 集合识别 action。
    parser = argparse.ArgumentParser(description="v24 cluster fluid 涌现引擎")
    parser.add_argument("pos", nargs="+", help="<project> <action> 任意顺序（action ∈ %s）" % (_ACTIONS,))
    parser.add_argument("--after-cluster", help="[emerge] 当前已完成 cluster id (如 cluster_001)")
    parser.add_argument("--cluster", help="[start-ch] 目标 cluster id")
    args = parser.parse_args()

    action = None
    project_arg = None
    for p in args.pos:
        if p in _ACTIONS and action is None:
            action = p
        elif project_arg is None:
            project_arg = p
    if action is None or project_arg is None:
        print(f"[ERROR] 需要 <project> 和 action（{_ACTIONS}）两个位置参", file=sys.stderr)
        return 2

    project_root = Path(project_arg).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[ERROR] 项目路径不存在 _数据库 目录: {project_root}", file=sys.stderr)
        return 2

    if action == "start-ch":
        cluster_key = args.cluster or args.after_cluster
        if not cluster_key:
            print(f"[ERROR] {action} 需要 --cluster <key>", file=sys.stderr)
            return 2

        start_ch = resolve_start_ch(project_root, cluster_key)
        print(start_ch)
        return 0

    if action == "emerge":
        if not args.after_cluster:
            print("[ERROR] emerge 需要 --after-cluster <id>", file=sys.stderr)
            return 2
        result = emerge_next_cluster(project_root, args.after_cluster)
        if result.get("ok"):
            print(f"[OK] cluster {result['next_cluster_id']} 涌现 {result['candidates_count']} 个 candidate")
            print(f"     emergence 文件: {result['emergence_path']}")
            for line in result.get("summary", []):
                print(f"     - {line}")
            return 0
        if result.get("book_complete"):
            # 🎉 完本终态（P0-2 · 2026-06-12）：ME 池耗尽 = 完本不是失败 → exit 0。
            # 其他错误路径（启发式无候选/文件损坏等）保持下方 exit 1 不变。
            print("🎉 本书大势已走完（ME 池耗尽）——这是完本不是故障", file=sys.stderr)
            print(f"     完本标记: {result.get('marker_path', '')}", file=sys.stderr)
            return 0
        print(f"[FAIL] {result.get('error', '未知错误')}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
