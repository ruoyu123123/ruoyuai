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


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _get_me_id(me: dict) -> str:
    """v26 字段兼容: ME 可能用 'id' 或 'me_id' 字段。"""
    return me.get("id") or me.get("me_id") or ""


def find_remaining_mes(dashishi: dict, completed_mes: set) -> list:
    """从大势卡 ME 池中找剩余未完成的 ME。

    v26 修复:
    1. 字段名兼容 - 同时读 'major_events_pool' 和 'major_events'
    2. ME id 字段兼容 - 同时读 'id' 和 'me_id'
    3. ME status 识别 - status='completed' 的 ME 自动算 completed
    """
    pool = dashishi.get("major_events_pool") or dashishi.get("major_events") or []
    remaining = []
    for me in pool:
        # 2026-05-29 复审修复（L8）：ME 池可能混入非 dict（字符串/None），裸调 .get() 直接崩。
        if not isinstance(me, dict):
            continue
        me_id = _get_me_id(me)
        # v26: 跳过已 completed 的 ME（按 status 字段判定 · 不只是 completed_mes 集合）
        if me.get("status") == "completed":
            continue
        # 跳过已在 completed_mes (来自 事件簇.clusters[].ME_to_advance) 的 ME
        if me_id in completed_mes:
            continue
        # 规范化 me dict: 确保 id 字段存在（向下游 me_to_cluster_brief 传递）
        if "id" not in me and "me_id" in me:
            me = {**me, "id": me["me_id"]}
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
    """从 ME 推断所属卷号。优先显式 vol 字段，否则从 me_id 前缀 'V1_ME_002' / 'V2.ME01' 解析。"""
    if isinstance(me, dict):
        v = me.get("vol")
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    mid = _get_me_id(me)
    m = _re_mod.match(r"[Vv](\d+)", str(mid))
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0
    return 0


def _me_text(me: dict) -> str:
    """聚合一个 ME 的可比对文本（description + name/title + triggers + trigger_condition）。"""
    if not isinstance(me, dict):
        return ""
    parts = [
        str(me.get("description", "")),
        str(me.get("name", "")),
        str(me.get("title", "")),
    ]
    trig = me.get("triggers")
    if isinstance(trig, list):
        parts.extend(str(t) for t in trig)
    elif trig:
        parts.append(str(trig))
    tc = me.get("trigger_condition")
    if isinstance(tc, dict):
        parts.extend(str(v) for v in tc.values())
    elif tc:
        parts.append(str(tc))
    return " ".join(parts)


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
    """兼容两种世界状态形态：显式 factions_state，或退回 global_state。"""
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
    """收集所有角色的 current_stage（小写），主角通常排第一。"""
    out = []
    if isinstance(character_arc, dict):
        for c in character_arc.get("characters", []) or []:
            if isinstance(c, dict) and c.get("current_stage"):
                out.append(str(c["current_stage"]).lower())
    return out


def _stage_matches(stages: list, tokens) -> bool:
    return any(any(tok in s for tok in tokens) for s in stages)


def _score_one_me(
    me: dict,
    cur_vol: int,
    completed_me_ids: set,
    stages: list,
    consequence_kw: set,
    extreme_factions: list,
) -> tuple:
    """给单个 ME 打分。返回 (score, reasons)。"""
    score = 0
    reasons = []
    me_id = _get_me_id(me)
    me_text = _me_text(me)
    me_kw = _keyword_set(me_text)

    # 1. vol 连续性
    if cur_vol:
        if _vol_of_me(me) == cur_vol:
            score += 30
            reasons.append(f"vol{cur_vol} 连续（与当前推进卷一致）")

    # 2. parent_me 链：ME 的 parent_me 指向已完成 ME → 推进主线
    parent = me.get("parent_me") if isinstance(me, dict) else None
    if parent and completed_me_ids and parent in completed_me_ids:
        score += 25
        reasons.append(f"承接已完成主线 ME「{parent}」（parent_me 链下游）")

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
    #    real-data 兼容：global_state 键常是「上级审查组_关注度」这类「主体_指标」复合名，
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

    return score, reasons


def select_candidate_mes(
    remaining_mes: list,
    world_state: dict,
    character_arc: dict,
    last_consequence: list,
    completed_me_ids: set = None,
) -> list:
    """启发式：从剩余 ME 中给每个打分排序，取 top 3 个 candidate。

    打分维度（基于世界状态 + 涟漪 + arc 阶段 + 主线链）：
    1. vol 连续性 —— ME 所属卷 == 当前推进卷（+30）
    2. parent_me 链 —— ME 承接已完成 ME（+25）
    3. arc 阶段匹配 —— 谎言裂痕期偏 setback / 觉醒期偏 recovery（+35）
    4. 涟漪呼应 —— ME 文本与最近后果关键词重叠（+8/重叠，封顶 40）
    5. faction 张力 —— ME 涉及处于极值（power≤10 或 stress 高）的势力（+20）

    保底：所有信号缺失 / 打分全 0 → 退回 remaining_mes[:3]（保持原行为，不破坏）。
    返回的每个 ME（浅拷贝）附 `_emergence_score` + `_emergence_reasons`（list[str]）。
    """
    if not remaining_mes:
        return []

    completed_me_ids = completed_me_ids or set()
    cur_vol = _current_advancing_vol(world_state, character_arc)
    stages = _arc_stages(character_arc)
    consequence_kw = _keyword_set(_consequence_texts(last_consequence))
    extreme_factions = _extreme_factions(world_state)

    scored = []
    for idx, me in enumerate(remaining_mes):
        # 2026-05-29 复审修复（L8）：循环首行加 isinstance 守卫 · 非 dict ME 跳过不打分（防 _score_one_me 内裸 .get() 崩）。
        if not isinstance(me, dict):
            continue
        score, reasons = _score_one_me(
            me, cur_vol, completed_me_ids, stages, consequence_kw, extreme_factions
        )
        scored.append((score, idx, me, reasons))

    # 2026-05-29 复审修复（L8）：scored 全空（remaining 全是非 dict 被过滤）→ 无可涌现 ME，返回 []。
    if not scored:
        return []

    # 保底：所有打分为 0（信号全缺）→ 退回设计顺序前 3（只取已过滤的 dict ME · 不漏非 dict）
    if all(s == 0 for s, _, _, _ in scored):
        out = []
        for _, _, me, _ in scored[:3]:
            me2 = dict(me) if isinstance(me, dict) else me
            if isinstance(me2, dict):
                me2["_emergence_score"] = 0
                me2["_emergence_reasons"] = ["保底：无世界状态/涟漪/arc 信号 → 按 ME 池设计顺序取前 3"]
            out.append(me2)
        return out

    # 按分降序；同分按原顺序（idx 升序）稳定排序 = 保留设计推进顺序
    scored.sort(key=lambda t: (-t[0], t[1]))

    out = []
    for score, _, me, reasons in scored[:3]:
        me2 = dict(me) if isinstance(me, dict) else me
        if isinstance(me2, dict):
            me2["_emergence_score"] = score
            me2["_emergence_reasons"] = reasons or ["（基础分）按设计顺序保留"]
        out.append(me2)
    return out


def me_to_cluster_brief(me: dict, cluster_id: str, ord: int, world_state: dict) -> dict:
    """把 ME 转成 cluster brief 候选 (scene_storyboard 仅雏形)。

    v26: title fallback - 大势卡 ME 可能只有 description 没 title · 自动取 description 头 30 字。
    """
    me_id = _get_me_id(me)
    title = me.get("title") or me.get("name") or ""
    if not title:
        # fallback: description 前 30 字 (截断在标点处) 作为 title
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
    # （expected_word_range / scenes_estimated / estimated_chapters / chapter_range）。
    # 依据 CLAUDE.md「📐 大纲章数 fluid」+ memory feedback_v27_writer_freestyle_splitter_word_cut：
    # 章数由 writer 自由发挥 + splitter 按字数切完自动回填，涌现阶段不得预设。
    # 保留 _emergence_score / _emergence_reasons（涌现可解释性，非章数死锁）。
    return {
        "cluster_id": cluster_id,
        "parent_me": me_id,
        "scope_summary": f"[CANDIDATE {ord}] 围绕 ME「{title}」展开。{me.get('description', '')}{why}",
        "_emergence_score": score,
        "_emergence_reasons": reasons,
        "status": "candidate",
        "ME_to_advance": [me_id],
        "_doc": f"v24 fluid 涌现 · 等待用户从 {ord} 个 candidate 中选 1 个 → status 改 in_progress",
        "scene_storyboard": [],  # 雏形 · 用户选定后再让 outline-planner 详化
        "anchor_props": [],
        "foreshadowing_to_plant": [],
        "research_ref": {
            "_doc": "v23.1 选定 candidate 后必须独立调研 cluster_brief"
        }
    }


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

    # 收集已完成 cluster 的 ME_to_advance
    completed_mes = set()
    for c in shijianji.get("clusters", []):
        if c.get("status") in ("done", "in_progress", "writer_v2_rewriting", "done_writer_drafted"):
            for me in c.get("ME_to_advance", []) or []:
                completed_mes.add(me)

    # 找剩余 ME
    remaining = find_remaining_mes(dashishi, completed_mes)
    if not remaining:
        return {"ok": False, "error": "大势卡 ME 池已全部完成，无新 cluster 可涌现", "completed_count": len(completed_mes)}

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

    # 选 candidate ME（传 completed_mes 供 parent_me 链打分）
    candidate_mes = select_candidate_mes(remaining, world_state, character_arc, last_consequence, completed_me_ids=completed_mes)
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
        brief = me_to_cluster_brief(me, f"{next_cluster_id}_candidate_{i}", i, world_state)
        candidates_briefs.append(brief)

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
        "candidates": candidates_briefs,
        "world_state_snapshot": {
            "factions_state": world_state.get("factions_state", {}),
            "last_consequences": last_consequence,
            "active_npc_threads_count": len(world_state.get("active_npc_threads", []))
        },
        "character_arc_snapshot": {
            "characters": [
                {"id": c.get("id"), "current_stage": c.get("current_stage")}
                for c in (character_arc.get("characters", []) or [])
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


_ACTIONS = ("emerge", "last-ch", "start-ch")


def main():
    # 2026-05-29 流程贯通（断点 3a）：新增 last-ch / start-ch 动作。
    # cluster-write.md:98/117 调 `cluster_emergence_engine.py last-ch "<项目>" --cluster <key>`
    # 和 `start-ch ... --cluster <key>`，旧版 action choices 只有 ["emerge"] → argparse exit 2
    # （死调用）。这两个动作只打印章号（供 shell $(...) 命令替换），不产生副作用。
    #
    # 兼容两种位置参顺序：
    #   emerge:   <project> emerge --after-cluster <id>   （project 在前）
    #   last-ch:  last-ch <project> --cluster <key>        （action 在前，见命令文档）
    # 故 project/action 不固定顺序 → 手动从位置参里按 _ACTIONS 集合识别 action。
    parser = argparse.ArgumentParser(description="v24 cluster fluid 涌现引擎")
    parser.add_argument("pos", nargs="+", help="<project> <action> 任意顺序（action ∈ %s）" % (_ACTIONS,))
    parser.add_argument("--after-cluster", help="[emerge] 当前已完成 cluster id (如 cluster_001)")
    parser.add_argument("--cluster", help="[last-ch/start-ch] 目标 cluster id")
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

    if action in ("last-ch", "start-ch"):
        cluster_key = args.cluster or args.after_cluster
        if not cluster_key:
            print(f"[ERROR] {action} 需要 --cluster <key>", file=sys.stderr)
            return 2

        # 2026-05-29 复审修复（C1-a · SC-3）：start-ch 与 last-ch 语义分离。
        # start-ch = 上一个已落章 cluster 末章 + 1（cluster_001 = 1），不反查目标自身未回填的 range。
        # last-ch  = 取「指定 cluster」自身末章（保持原行为，供 world_evolution_apply_card 用上一 cluster 末章）。
        if action == "start-ch":
            start_ch = resolve_start_ch(project_root, cluster_key)
            print(start_ch)
            return 0

        # last-ch：仍取指定 cluster 末章
        import cluster_lookup
        rng = cluster_lookup.cluster_id_to_range(project_root, cluster_key)
        if not rng or len(rng) != 2:
            print(f"[ERROR] cluster {cluster_key} 的 chapter_range 未找到（splitter 切完才回填）",
                  file=sys.stderr)
            return 2
        # 只打印纯数字 → 供 cluster-write.md 里 LAST_CH=$(...) 命令替换
        print(rng[1])
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
        else:
            print(f"[FAIL] {result.get('error', '未知错误')}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
