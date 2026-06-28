"""cluster_summary_builder.py — cluster 账本（故事块摘要.json）生产者

v2 cluster 化「摘要驱动」架构（2026-05-29）：
cluster-save-state 完成时调本模块，把一个 cluster 的每章正文 + changes + manifest +
WAL summary + audit + judge 富摘要预算成 ChapterRecord，组装成 ClusterRecord，
经唯一原子写入器 `cluster_summary_store.upsert_cluster` 落库。22 个 cross_cluster_*
aggregator 在 CLUSTER_MODE=1 时改从本账本取预计算字段（不再逐章 glob 重扫正文）。

字段契约见 cluster_summary_reader.py 顶部 docstring（单一来源）。

设计纪律：
- 全防御性读：任何源缺失/损坏/字段缺都跳过该字段，绝不崩。
- 优先填高价值「正文派生」字段（builder 一次跑算好，aggregator 复用）。
- 复用现成 aggregator 的逐章 metric 函数（防御性 import，失败则跳字段，不改对方文件）。

CLI:
    python cluster_summary_builder.py <project> --cluster <id>
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chapter_io as cio  # noqa: E402
import cluster_lookup  # noqa: E402
import cluster_summary_store  # noqa: E402

# ---- 防御性 import 的 aggregator metric 函数（失败则跳对应字段）----
try:
    import cross_cluster_pattern_aggregate as _pat  # noqa: E402
except Exception:  # pragma: no cover
    _pat = None
try:
    from cross_cluster_emotion_pattern_aggregate import detect_emotions_for_char as _detect_emotions
except Exception:  # pragma: no cover
    _detect_emotions = None
# 2026-05-29 复审修复：补字段所需的工艺函数（失败则对应字段降级，不崩）
try:
    import cross_cluster_structure_compliance_aggregate as _struct  # noqa: E402  beat 关键词
except Exception:  # pragma: no cover
    _struct = None
# 🔴 2026-06-27 SYS-5 ①：cliffhanger_resonance_next 改由 builder 确定性预算（wiring_gap 补 producer）。
# extract_keywords 复用 continuity scanner 同源逻辑（continuity_keywords 单一来源·两边分可比）。
try:
    from continuity_keywords import extract_keywords as _cliff_keywords  # noqa: E402
except Exception:  # pragma: no cover
    _cliff_keywords = None


# ============================================================
# 通用防御性读
# ============================================================

def _db_dir(project_root) -> Path:
    root = Path(project_root)
    return root if root.name == "_数据库" else root / "_数据库"


def _load_json(p: Path, default=None):
    try:
        if Path(p).is_file():
            return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _read_body(project_root, ch: int) -> str | None:
    try:
        return cio.read_body(project_root, ch)
    except Exception:
        return None


def _read_changes(project_root, ch: int) -> dict:
    try:
        c = cio.read_changes(project_root, ch)
        if isinstance(c, dict):
            return c
    except Exception:
        pass
    return {"factual": {}, "self_eval": {}}


def _first(d: dict, *keys, default=None):
    """返回 d 中第一个存在且非空的 key 的值（多别名探测）。"""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return default


def _ids_of(items, *id_keys) -> list:
    """把混合列表（str / {aspect_id:..} / {clock_id:..} / {id:..}）展平成 id 字符串列表。

    2026-05-29 复审修复 [M7]：aggregator 账本分支按 `aid in addressed_ids` 比对裸 id 字符串，
    故 builder 须把 changes 里 dict 形态 [{aspect_id:..}] 展平成 ["aspect_id_value", ...]。
    """
    out = []
    for v in (items or []):
        if isinstance(v, str):
            if v:
                out.append(v)
        elif isinstance(v, dict):
            for k in id_keys:
                if v.get(k):
                    out.append(v[k])
                    break
    return out


# ============================================================
# 数据源定位（cluster 级 + per-chapter 级双探测）
# ============================================================

def _load_manifest(db: Path, ch: int) -> dict:
    return (_load_json(db / ".manifest" / f"ch_{ch:03d}.json")
            or _load_json(db / ".manifest" / f"ch_{ch}.json")
            or {})


def _load_wal_summary(db: Path, ch: int, cluster_id: str) -> dict:
    """优先 per-chapter WAL summary，缺则回退 cluster 级 summary。"""
    per = (_load_json(db / ".wal" / f"第{ch:03d}章_summary.json")
           or _load_json(db / ".wal" / f"第{ch}章_summary.json"))
    if isinstance(per, dict):
        return per
    norm = cluster_lookup.normalize_cluster_id(cluster_id) or str(cluster_id)
    cl = _load_json(db / ".wal" / f"{norm}_summary.json")
    return cl if isinstance(cl, dict) else {}


def _load_audit(db: Path, ch: int, cluster_id: str) -> dict:
    per = (_load_json(db / ".audit" / f"ch_{ch:03d}_audit.json")
           or _load_json(db / ".audit" / f"ch_{ch}_audit.json"))
    if isinstance(per, dict):
        return per
    norm = cluster_lookup.normalize_cluster_id(cluster_id) or str(cluster_id)
    cl = _load_json(db / ".audit" / f"{norm}_audit.json")
    return cl if isinstance(cl, dict) else {}


def _load_judge_reports(db: Path, ch: int) -> list[dict]:
    """收集 ch 的所有 judge agent 报告（ch_NNN_<agent>.json）。"""
    out = []
    jd = db / ".judge_reports"
    if not jd.is_dir():
        return out
    for pat in (f"ch_{ch:03d}_*.json", f"ch_{ch}_*.json"):
        for f in sorted(jd.glob(pat)):
            j = _load_json(f)
            if isinstance(j, dict):
                out.append(j)
    return out


def _load_blueprint_scene(db: Path, cluster_id: str, ch: int) -> dict:
    """从 进度.cluster_blueprint[cid].scene_storyboard 找匹配 ch 的 scene 卡。"""
    prog = _load_json(db / "进度.json", {}) or {}
    # 2026-05-29 复审复修 [P0/SC-1]：cluster_blueprint 可能是 list（城南实测）→ 裸 .items() 崩。
    # 统一经 cluster_lookup.normalize_blueprint 归一成 dict（B2 遗漏的第三处 .items()）。
    cb = cluster_lookup.normalize_blueprint(prog)
    norm = cluster_lookup.normalize_cluster_id(cluster_id)
    for cid, cdata in cb.items():
        if cluster_lookup.normalize_cluster_id(cid) != norm:
            continue
        if not isinstance(cdata, dict):
            continue
        for sb in cdata.get("scene_storyboard", []) or []:
            if isinstance(sb, dict) and sb.get("ch") == ch:
                return sb
    return {}


def _location_names(db: Path) -> list[str]:
    m = _load_json(db / "地图.json", {}) or {}
    out = []
    for loc in m.get("locations", []) or []:
        if isinstance(loc, dict):
            n = loc.get("name") or loc.get("id")
            if n:
                out.append(str(n))
    return out


def _character_names(db: Path) -> list[str]:
    cards = _load_json(db / "人物卡.json", {}) or {}
    out = []
    for c in cards.get("characters", []) or []:
        if isinstance(c, dict):
            n = c.get("name") or c.get("id")
            if n:
                out.append(str(n))
    return out


# ============================================================
# 2026-05-29 复审修复：canonical 子系统派生（stress / coping / arc / aspect）
# 这些字段在 changes.json 恒缺 → 必须从权威子系统 JSON + 正文派生，与各 aggregator
# 磁盘版同源（零回归）。
# ============================================================

def _build_stress_index(db: Path) -> tuple[dict, dict, dict]:
    """[L3] 从 主角压力档.json.stress_log 建三索引（与 scan_stress_trend 同源）：
      stress_by_ch: {ch: new_total}（跳过 mental_break_triggered 条目，同 ch 取最新）
      break_by_ch:  {ch: card_id}（mental_break_triggered 条目）
      trigger_by_ch:{ch: trigger 描述字符串}
    无 stress_log（或空）→ 三空 dict（与磁盘版 return [] 行为对齐：什么也不产）。
    """
    stress = _load_json(db / "主角压力档.json", {}) or {}
    log = stress.get("stress_log", []) or []
    stress_by_ch, break_by_ch, trigger_by_ch = {}, {}, {}
    for e in log:
        if not isinstance(e, dict):
            continue
        ch = e.get("ch")
        if not isinstance(ch, int):
            continue
        if e.get("trigger_type") == "mental_break_triggered":
            card = e.get("card_id") or e.get("card")
            if card:
                break_by_ch[ch] = card
        else:
            nt = e.get("new_total")
            if isinstance(nt, (int, float)):
                stress_by_ch[ch] = nt
        trig = e.get("trigger") or e.get("stress_trigger") or e.get("reason")
        if trig:
            trigger_by_ch[ch] = trig
    return stress_by_ch, break_by_ch, trigger_by_ch


def _coping_keywords(db: Path) -> list[str]:
    """[L4] 从 主角压力档.json.coping_mechanisms.high_stress_behaviors 抽 2-4 字关键词
    （与 scan_stress_trend COPING_NEVER_TRIGGERED 的 coping_kws 同源）。"""
    stress = _load_json(db / "主角压力档.json", {}) or {}
    cm = stress.get("coping_mechanisms", {})
    behaviors = cm.get("high_stress_behaviors", []) if isinstance(cm, dict) else []
    kws = []
    for c in (behaviors or []):
        if isinstance(c, str):
            kws.extend(re.findall(r"[一-鿿]{2,4}", c)[:2])
    return [k for k in kws if len(k) >= 2]


def _arc_stage_by_cluster(db: Path, norm_cid: str) -> dict:
    """[L4] 从 character_arc_state.json 取本 cluster 各角色的 arc 阶段 {char: stage_id}。

    cluster-native：每个 stage 含 active_cluster 列表，命中本 cluster → 该角色当前阶段。
    兼容两种 schema：v2 `arcs.{char}.stages[].active_cluster`；
    旧 `characters.{char}.stages_by_chapter`（此处用 current_stage 兜底）。
    """
    data = _load_json(db / "character_arc_state.json", {}) or {}
    out = {}
    arcs = data.get("arcs")
    if isinstance(arcs, dict):
        for char, cdata in arcs.items():
            if not isinstance(cdata, dict):
                continue
            chosen = None
            for st in cdata.get("stages", []) or []:
                if not isinstance(st, dict):
                    continue
                acs = st.get("active_cluster") or []
                if any(cluster_lookup.normalize_cluster_id(a) == norm_cid for a in acs):
                    chosen = st.get("id") or st.get("name")
                    break
            if chosen is None:
                chosen = cdata.get("current_stage")
            if chosen:
                out[str(char)] = chosen
    return out


def _aspect_keyword_sets(db: Path) -> list[tuple[str, list]]:
    """[L4] 从 角色烙印.json.characters[].active_aspects 抽 (aspect_id, keywords)。

    keywords 抽法与 data_consumption.scan_aspect_continuity 文本命中分支同源：
    前 3 条 narrative_constraints 各取 3 个 2-4 字词 + 前 3 条 emotional_triggers 各取 2 个。
    """
    data = _load_json(db / "角色烙印.json", {}) or {}
    out = []
    for _char, cdata in (data.get("characters") or {}).items():
        if not isinstance(cdata, dict):
            continue
        for aspect in cdata.get("active_aspects") or []:
            if not isinstance(aspect, dict):
                continue
            aid = aspect.get("aspect_id")
            if not aid:
                continue
            kws = []
            for c_str in (aspect.get("narrative_constraints") or [])[:3]:
                if isinstance(c_str, str):
                    kws.extend(re.findall(r"[一-鿿]{2,4}", c_str)[:3])
            for t_str in (aspect.get("emotional_triggers") or [])[:3]:
                if isinstance(t_str, str):
                    kws.extend(re.findall(r"[一-鿿]{2,4}", t_str)[:2])
            kws = [k for k in kws if len(k) >= 2]
            if kws:
                out.append((aid, kws))
    return out


# ============================================================
# 正文派生轻量提取
# ============================================================

_TRANSITION_WORDS = ["翌日", "第二天", "次日", "三天后", "几天后", "一周后", "数日后",
                     "当晚", "深夜", "傍晚", "清晨", "早上", "下午", "片刻后", "不久",
                     "与此同时", "几小时后", "转眼", "良久"]


def _extract_text_keywords(text: str, top: int = 4) -> list[str]:
    """正文 2-4 字关键词指纹（停用词 + 高频 2gram/名词近似）。"""
    if not text:
        return []
    cleaned = re.sub(r"[^一-鿿㐀-䶿]", "", text)
    if len(cleaned) < 4:
        return []
    stop = set("的了在是我你他她它们这那有和就都不也要会着说道一个不是什么没有")
    grams = Counter()
    for i in range(len(cleaned) - 1):
        g = cleaned[i:i + 2]
        if g[0] in stop or g[1] in stop:
            continue
        grams[g] += 1
    common = [g for g, c in grams.most_common(top * 3) if c >= 2]
    return common[:top]


def _detect_time_transition(text: str) -> bool:
    if not text:
        return False
    head = text[:1000]
    return any(w in head for w in _TRANSITION_WORDS)


def _locations_mentioned(text: str, all_locs: list[str]) -> list[str]:
    if not text or not all_locs:
        return []
    return [loc for loc in all_locs if loc and loc in text]


def _char_mention_counts(text: str, names: list[str]) -> dict:
    if not text or not names:
        return {}
    return {n: c for n in names if (c := text.count(n)) > 0}


def _ending_line(text: str) -> str:
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:120] if lines else ""


# ============================================================
# pattern_metrics（复用 cross_cluster_pattern_aggregate 逐章 metric 函数）
# ============================================================

def _build_pattern_metrics(text: str, protagonist: str, catchphrases, cliche_dict) -> dict:
    """复用 _pat 模块的 scan_* 函数算出 20 维 metric。import 失败则返回 {}。"""
    if _pat is None or not text:
        return {}
    m = {}
    # 2026-05-29 复审修复 [C3]：补 catchphrase / cliche_hits / wc 三键。
    # cross_cluster_pattern_aggregate 在 cluster 模式直接把本 dict 当 per_chapter[ch]，
    # 其 Finding 1 裸读 d["catchphrase"].items()、Finding 13 裸读 d["cliche_hits"]、
    # 磁盘版 wc=len(text)，三键缺失 → aggregator KeyError → Traceback（SC-2 真崩溃）。
    # wc 语义对齐磁盘版 = 全字符数 len(text)（非纯 CJK 数）。
    try:
        m["wc"] = len(text)
    except Exception:
        pass
    try:
        m["catchphrase"] = _pat.scan_catchphrase(text, catchphrases or [])
    except Exception:
        m["catchphrase"] = {}
    try:
        m["cliche_hits"] = _pat.scan_cliche_ai_cn(text, cliche_dict)
    except Exception:
        m["cliche_hits"] = {}
    try:
        m["para_protagonist_start"] = _pat.scan_paragraph_starts_with_protagonist(text, protagonist)
    except Exception:
        pass
    try:
        m["dialogue_tag"] = _pat.scan_dialogue_tags(text)
    except Exception:
        pass
    try:
        m["body_reaction"] = _pat.scan_body_reactions(text)
    except Exception:
        pass
    try:
        m["negation_desc"] = _pat.scan_negation_descriptions(text)
    except Exception:
        pass
    try:
        m["para_first_word_top1_pct"] = round(_pat.scan_para_first_word_concentration(text), 3)
    except Exception:
        pass
    try:
        tc, tr = _pat.scan_tell_overuse(text)
        m["tell_count"], m["tell_per_1k"] = tc, round(tr, 2)
    except Exception:
        pass
    try:
        m["particle_dist"] = _pat.scan_sentence_particle_distribution(text)
    except Exception:
        pass
    try:
        m["pronoun_action"] = _pat.scan_pronoun_action(text)
    except Exception:
        pass
    try:
        m["punctuation"] = _pat.scan_punctuation_halfwidth(text)
    except Exception:
        pass
    try:
        mc, mr = _pat.scan_metaphor_overuse(text)
        m["metaphor_count"], m["metaphor_per_1k"] = mc, round(mr, 2)
    except Exception:
        pass
    try:
        m["name_density_per_100"] = round(_pat.scan_protagonist_name_density(text, protagonist), 2)
    except Exception:
        pass
    try:
        m["warmup_hits"] = _pat.scan_opening_warmup(text)
    except Exception:
        pass
    try:
        cc, cr = _pat.scan_causal_heuristic(text)
        m["causal_count"], m["causal_per_1k"] = cc, round(cr, 2)
    except Exception:
        pass
    try:
        m["time_anchor_drops"] = _pat.scan_time_anchor_drops(text)
    except Exception:
        pass
    try:
        m["modifier_stack_sentences"] = _pat.scan_modifier_stack(text)
    except Exception:
        pass
    try:
        m["sensory_dist"] = {k: round(v, 3) for k, v in _pat.scan_sensory_distribution(text).items()}
    except Exception:
        pass
    try:
        m["dialogue_stream_max"] = _pat.scan_dialogue_stream_flat(text)
    except Exception:
        pass
    return m


def _build_idiom_hits(text: str) -> dict:
    if _pat is None or not text:
        return {}
    try:
        return {idiom: text.count(idiom) for idiom in _pat.IDIOM_COOLDOWN_DICT if text.count(idiom) > 0}
    except Exception:
        return {}


def _build_char_emotion_counts(text: str, names: list[str]) -> dict:
    if _detect_emotions is None or not text or not names:
        return {}
    out = {}
    for n in names:
        try:
            counts = _detect_emotions(text, n)
            if counts:
                out[n] = dict(counts)
        except Exception:
            continue
    return out


# ============================================================
# changes 派生
# ============================================================

def _build_changes_derived(factual: dict, self_eval: dict) -> dict:
    """从 changes.factual / self_eval 提取无需正文的派生字段（多别名防御探测）。"""
    rec = {}

    rel = _first(factual, "relationships", "relationship_changes", "relationship_updates")
    if isinstance(rel, list):
        rec["relationships"] = rel

    tp = _first(factual, "throughline_progress", "throughlines_addressed", "throughlines")
    if isinstance(tp, dict):
        rec["throughline_progress"] = tp

    items = _first(factual, "item_changes", "items_changed", "items", "props")
    if isinstance(items, list):
        rec["item_changes"] = items

    beats = _first(factual, "beats_addressed", "beats", "save_the_cat_beats")
    if isinstance(beats, list):
        rec["beats_addressed"] = beats

    # 2026-05-29 复审修复 [M9]：ending_type/ending_line 权威来源 = self_eval.applied_style
    #（continuity aggregator 磁盘版读 self_eval.applied_style.ending_type/ending_line，
    # 见 cross_cluster_continuity_aggregate.scan_cliffhanger_resonance）。factual 仅兜底。
    applied = self_eval.get("applied_style", {}) if isinstance(self_eval, dict) else {}
    if not isinstance(applied, dict):
        applied = {}
    et = _first(applied, "ending_type") or _first(factual, "ending_type", "chapter_ending_type")
    if et:
        rec["ending_type"] = et
    el = _first(applied, "ending_line", "last_line") or _first(factual, "ending_line", "last_line")
    if el:
        rec["ending_line"] = el

    ta = _first(factual, "time_advance", "time_progression")
    if isinstance(ta, dict):
        rec["time_advance"] = ta
    tanchor = _first(factual, "time_anchor", "time")
    if tanchor:
        rec["time_anchor"] = tanchor

    pn = _first(factual, "plot_nodes", "plot_progress", "plot_points")
    if isinstance(pn, list):
        rec["plot_nodes"] = pn

    # 2026-05-29 复审修复 [M7]：aspects_addressed/clocks_addressed 在账本里应是「id 字符串列表」
    #（data_consumption aggregator 账本分支裸做 `aid in addressed_ids` / `cid in clocks_addressed`），
    # 但 changes.factual 里常是 dict 列表 [{aspect_id:..},..]。这里统一展平成 id 字符串列表。
    aspects = _first(factual, "aspects_addressed", "active_aspects")
    if isinstance(aspects, list):
        rec["aspects_addressed"] = _ids_of(aspects, "aspect_id", "id")

    clocks = _first(factual, "clocks_addressed", "clocks")
    if isinstance(clocks, list):
        rec["clocks_addressed"] = _ids_of(clocks, "clock_id", "id")

    dice = _first(factual, "fate_dice_consumed", "fate_dice", "dice_consumed")
    if isinstance(dice, list):
        rec["fate_dice_consumed"] = dice

    # 2026-05-29 复审修复 [M8]：moves_used/position_effect_evals 权威来源 = self_eval
    #（character_dynamics aggregator 磁盘版读 self_eval.moves_used / self_eval.position_effect_evals，
    # 见 scan_moves_usage / scan_position_effect）。factual 仅兜底。
    moves = _first(self_eval, "moves_used", "character_moves") or _first(factual, "moves_used", "character_moves")
    if isinstance(moves, list):
        rec["moves_used"] = moves

    pee = (_first(self_eval, "position_effect_evals", "position_effects")
           or _first(factual, "position_effect_evals", "position_effects"))
    if isinstance(pee, list):
        rec["position_effect_evals"] = pee

    # 2026-05-29 复审修复 [L3]：stress_total/stress_trigger/mental_break_card 的权威来源
    # 是 主角压力档.json.stress_log（changes.factual 恒缺，见 character_dynamics 磁盘版
    # scan_stress_trend 只读 stress_log）。此处 factual 读法保留为兜底；真实派生在
    # _build_chapter_record 里用 ctx["stress_by_ch"] 注入。
    stress = _first(factual, "stress_total", "protagonist_stress_total")
    if isinstance(stress, (int, float)):
        rec["stress_total"] = stress
    strig = _first(factual, "stress_trigger")
    if strig:
        rec["stress_trigger"] = strig
    mbc = _first(factual, "mental_break_card", "mental_break")
    if mbc:
        rec["mental_break_card"] = mbc

    return rec


def _foreshadow_actions(factual: dict) -> tuple[list, list, list]:
    """从 changes.factual 取 (planted, paid, reinforced)，元素归一为字符串 id/描述。"""
    def _ids(val):
        out = []
        for v in (val or []):
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, dict):
                out.append(v.get("id") or v.get("fid") or v.get("description") or v.get("desc") or str(v))
        return out

    fa = factual.get("foreshadowing_actions")
    if isinstance(fa, dict):
        planted = _ids(fa.get("planted"))
        paid = _ids(fa.get("paid"))
        reinforced = _ids(fa.get("reinforced"))
    else:
        planted = _ids(_first(factual, "foreshadowing_planted", "foreshadow_planted", default=[]))
        paid = _ids(_first(factual, "foreshadowing_paid", "foreshadow_paid", default=[]))
        reinforced = _ids(_first(factual, "foreshadowing_reinforced", "foreshadow_reinforced", default=[]))
    return planted, paid, reinforced


def _secrets_revealed(factual: dict) -> list:
    val = _first(factual, "secrets_revealed", "secret_revealed", default=[])
    out = []
    for v in (val or []):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            out.append(v.get("id") or v.get("sid") or str(v))
    return out


# ============================================================
# 单章 ChapterRecord 组装
# ============================================================

def _build_chapter_record(project_root, db, cluster_id, ch, ctx) -> dict:
    rec: dict = {}

    body = _read_body(project_root, ch)
    changes = _read_changes(project_root, ch)
    factual = changes.get("factual", {}) if isinstance(changes, dict) else {}
    self_eval = changes.get("self_eval", {}) if isinstance(changes, dict) else {}

    # ---- 正文派生（高价值）----
    if body:
        rec["cjk_count"] = cio.count_cjk(body)
        kw = _extract_text_keywords(body)
        if kw:
            rec["text_keyword_set"] = kw
        pm = _build_pattern_metrics(body, ctx["protagonist"], ctx["catchphrases"], ctx["cliche_dict"])
        if pm:
            rec["pattern_metrics"] = pm
        ih = _build_idiom_hits(body)
        if ih:
            rec["idiom_hits"] = ih
        cmc = _char_mention_counts(body, ctx["char_names"])
        if cmc:
            rec["char_mention_counts"] = cmc
            rec["char_appearance_chs_flag"] = list(cmc.keys())
        cec = _build_char_emotion_counts(body, ctx["char_names"])
        if cec:
            rec["char_emotion_counts"] = cec
        locs = _locations_mentioned(body, ctx["locations"])
        if locs:
            rec["locations_mentioned"] = locs
        rec["time_transition_present"] = _detect_time_transition(body)
        eline = _ending_line(body)
        if eline and "ending_line" not in factual:
            rec["ending_line"] = eline
    else:
        # 无正文时退回 changes 里的 cjk 计数
        wc = _first(factual, "word_count_cjk", "cjk_count", "word_count")
        if isinstance(wc, (int, float)):
            rec["cjk_count"] = int(wc)

    # ---- changes 派生 ----
    rec.update(_build_changes_derived(factual, self_eval))

    # ---- blueprint / storyboard 派生 ----
    sb = _load_blueprint_scene(db, cluster_id, ch)
    if sb:
        for src, dst in (("scene_type", "scene_type"), ("pov", "pov"), ("beat", "beat"),
                         ("user_choice", "user_choice"), ("choice_leads_to", "choice_leads_to"),
                         ("turning_point", "turning_point")):
            v = sb.get(src)
            if v not in (None, "", [], {}):
                rec[dst] = v
        chars = sb.get("characters")
        if isinstance(chars, list) and chars:
            rec["characters"] = chars

    # ---- manifest 派生（characters 兜底）----
    mf = _load_manifest(db, ch)
    if mf and "characters" not in rec:
        ac = mf.get("active_characters")
        if isinstance(ac, list) and ac:
            names = [a.get("name") or a.get("id") if isinstance(a, dict) else a for a in ac]
            names = [n for n in names if n]
            if names:
                rec["characters"] = names

    # ---- WAL summary 派生 ----
    wal = _load_wal_summary(db, ch, cluster_id)
    if wal:
        emo = wal.get("emotion")
        if isinstance(emo, dict) and isinstance(emo.get("value"), (int, float)):
            rec["emotion_value"] = emo["value"]
        elif isinstance(emo, (int, float)):
            rec["emotion_value"] = emo
        summ = wal.get("summary")
        if isinstance(summ, str) and len(summ) >= 50 and "summary" not in rec:
            rec["summary"] = summ

    # ---- audit 派生 ----
    audit = _load_audit(db, ch, cluster_id)
    if audit:
        ss = audit.get("scanner_status")
        if isinstance(ss, list):
            for s in ss:
                if not isinstance(s, dict):
                    continue
                name = s.get("scanner", "")
                if "hook_strength" in name and isinstance(s.get("score"), (int, float)):
                    rec["hook_score"] = s["score"]
                if "golden_three" in name and isinstance(s.get("scores"), dict):
                    rec["golden_scores"] = s["scores"]
        if "hook_score" not in rec and isinstance(audit.get("hook_score"), (int, float)):
            rec["hook_score"] = audit["hook_score"]
        if "golden_scores" not in rec and isinstance(audit.get("golden_scores"), dict):
            rec["golden_scores"] = audit["golden_scores"]

    # ---- judge 派生 ----
    judges = _load_judge_reports(db, ch)
    if judges:
        grades, waivers = [], []
        prev_consumed = False
        grade_map = {"A": 4, "B": 3, "C": 2, "D": 1}
        for j in judges:
            g = j.get("overall_grade")
            if g in grade_map:
                grades.append(grade_map[g])
            for w in (j.get("waivers") or []):
                if isinstance(w, dict):
                    waivers.append(w)
            if j.get("prev_findings_consumed") or j.get("prev_consumed"):
                prev_consumed = True
        if grades:
            rec["judge_score"] = round(mean(grades), 2)
        if waivers:
            rec["waivers"] = waivers
        rec["prev_findings_consumed"] = prev_consumed

    # ---- pre_opening 存在性 ----
    cd = cio.find_chapter_dir(project_root, ch)
    if cd:
        for cand in (cd / ".pre_opening.txt", cd / f"第{ch:03d}章.pre_opening.txt"):
            if cand.is_file():
                rec["has_pre_opening"] = True
                break

    # ---- [L3] stress 派生（权威源 主角压力档.json.stress_log，非 factual）----
    st = ctx["stress_by_ch"].get(ch)
    if isinstance(st, (int, float)):
        rec["stress_total"] = st
    bcard = ctx["break_by_ch"].get(ch)
    if bcard and "mental_break_card" not in rec:
        rec["mental_break_card"] = bcard
    strig = ctx["trigger_by_ch"].get(ch)
    if strig and "stress_trigger" not in rec:
        rec["stress_trigger"] = strig

    # ---- [L4] coping_hit（高 stress 章正文是否带 coping 行为关键词）----
    # 与 character_dynamics 磁盘版 COPING_NEVER_TRIGGERED 同源；coping_kws 来自压力档。
    if body and ctx["coping_keywords"]:
        rec["coping_hit"] = any(kw in body for kw in ctx["coping_keywords"])

    # ---- [L4] arc_stage（本 cluster 各角色 arc 阶段 · character_arc_state.json）----
    if ctx["arc_stage"]:
        rec["arc_stage"] = dict(ctx["arc_stage"])

    # ---- [L4] aspect_text_hit（正文命中 aspect 约束/触发关键词的 aspect_id 列表）----
    if body and ctx["aspect_kw_sets"]:
        hits = [aid for aid, kws in ctx["aspect_kw_sets"] if any(k in body for k in kws)]
        if hits:
            rec["aspect_text_hit"] = hits

    # ---- [M10] beat_signal_hit（真产出：声明 beat 时扫正文/changes 是否命中信号）----
    # 与 structure_compliance 磁盘版 scan_beat_progression 同源（explicit_hit 或正文关键词命中）。
    # 仅在声明了 beat 时产出 bool（否则保持缺省，aggregator 跳过该章）。
    beat_str = rec.get("beat")
    if beat_str and _struct is not None:
        try:
            kws = _struct.beat_keywords_for(str(beat_str))
            ba = rec.get("beats_addressed") or []
            explicit_hit = any(str(beat_str).lower() in str(b).lower() for b in ba)
            kw_hit = bool(body) and any(k in body for k in kws)
            rec["beat_signal_hit"] = bool(explicit_hit or kw_hit)
        except Exception:
            pass

    # 🔴 2026-06-27 SYS-5 ①：把正文回传给 cluster 级 rollup（cliffhanger 第二遍需要下一章 head）。
    return rec, factual, self_eval, (body or "")


# ============================================================
# 🔴 2026-06-27 SYS-5 ①：cliffhanger_resonance_next 确定性预算（零 LLM）
# ============================================================

def _compute_cliffhanger_resonance(chapters: dict, bodies: dict, lo: int, hi: int,
                                   protagonist: str | None) -> None:
    """逐章预算「前章 ending 关键词 ∩ 下一章 head 600 字关键词」重叠分，写 rec['cliffhanger_resonance_next']。

    与 continuity scanner.scan_cliffhanger_resonance 同款逻辑（continuity_keywords 单一来源）。
    · 本 cluster 末章（ch==hi）或下一章正文缺失 → 安全返回 -1（no-signal skip，非 0% 误报）。
    · ending_line 取 rec 已派生字段（applied_style/factual/正文末行兜底），缺关键词 → -1。
    悬念断章 exemption 由 scanner 读 rec['ending_type'] 时叠加，此处只产 raw 重叠分。原地改 chapters。
    """
    if _cliff_keywords is None:
        return
    for ch in range(lo, hi + 1):
        rec = chapters.get(str(ch))
        if not isinstance(rec, dict):
            continue
        next_body = bodies.get(ch + 1)
        if ch >= hi or not next_body:
            rec["cliffhanger_resonance_next"] = -1.0
            continue
        ending_line = rec.get("ending_line") or ""
        ending_type = rec.get("ending_type") or ""
        try:
            ending_kw = _cliff_keywords(ending_line + " " + ending_type, protagonist=protagonist)
            if not ending_kw:
                rec["cliffhanger_resonance_next"] = -1.0
                continue
            head_kw = _cliff_keywords(next_body[:600], protagonist=protagonist)
            overlap = ending_kw & head_kw
            score = len(overlap) / max(len(ending_kw), 1)
            rec["cliffhanger_resonance_next"] = round(score, 2)
        except Exception:
            rec["cliffhanger_resonance_next"] = -1.0


# ============================================================
# cluster 级 rollup + 主入口
# ============================================================

def build_cluster_summary(project_root, cluster_id) -> dict:
    """构建并原子写入一个 cluster 的富摘要。返回 {ok, cluster_id, ...stats}。"""
    project_root = Path(project_root)
    db = _db_dir(project_root)
    norm_cid = cluster_lookup.normalize_cluster_id(cluster_id) or str(cluster_id)

    rng = cluster_lookup.cluster_id_to_range(project_root, cluster_id)
    if not rng or len(rng) != 2:
        return {"ok": False, "cluster_id": norm_cid,
                "error": f"取不到 {norm_cid} 的章范围（进度.cluster_blueprint / 事件簇 均无 chapter_range）"}
    lo, hi = int(rng[0]), int(rng[1])

    # ---- 共享上下文（一次算好，逐章复用）----
    protagonist = "主角"
    catchphrases, cliche_dict = [], None
    if _pat is not None:
        try:
            protagonist = _pat.get_protagonist(project_root, None)
            catchphrases = _pat.get_catchphrases(project_root, protagonist)
            cliche_dict = _pat._load_cliche_dict(project_root)
        except Exception:
            pass
    # 2026-05-29 复审修复 [B2 P0]：接线 4 个 canonical 子系统派生 helper（原定义了但从未调用
    # → ctx 缺 6 键 → _build_chapter_record 第一章即 KeyError('stress_by_ch') 崩、整条账本管线挂）。
    # 全防御性：任一子系统 JSON 缺失/损坏 → 退化空容器，builder 不崩（与各 aggregator 磁盘版同源）。
    try:
        _stress_by_ch, _break_by_ch, _trigger_by_ch = _build_stress_index(db)
    except Exception:
        _stress_by_ch, _break_by_ch, _trigger_by_ch = {}, {}, {}
    try:
        _coping_kw = _coping_keywords(db)
    except Exception:
        _coping_kw = []
    try:
        _arc_stage = _arc_stage_by_cluster(db, norm_cid)
    except Exception:
        _arc_stage = {}
    try:
        _aspect_kw = _aspect_keyword_sets(db)
    except Exception:
        _aspect_kw = []
    ctx = {
        "protagonist": protagonist,
        "catchphrases": catchphrases,
        "cliche_dict": cliche_dict,
        "char_names": _character_names(db),
        "locations": _location_names(db),
        "stress_by_ch": _stress_by_ch,
        "break_by_ch": _break_by_ch,
        "trigger_by_ch": _trigger_by_ch,
        "coping_keywords": _coping_kw,
        "arc_stage": _arc_stage,
        "aspect_kw_sets": _aspect_kw,
    }

    chapters: dict[str, dict] = {}
    bodies: dict[int, str] = {}            # 🔴 SYS-5 ①：cliffhanger 第二遍需要下一章 head
    cjk_list: list[int] = []
    throughline_tally = Counter()
    fs_planted, fs_paid, fs_reinforced = [], [], {}
    secrets, rel_changes = [], []
    field_fill_counter: Counter = Counter()

    for ch in range(lo, hi + 1):
        rec, factual, self_eval, body = _build_chapter_record(project_root, db, cluster_id, ch, ctx)
        if body:
            bodies[ch] = body
        if not rec:
            continue
        chapters[str(ch)] = rec
        for k in rec:
            field_fill_counter[k] += 1

        if isinstance(rec.get("cjk_count"), int):
            cjk_list.append(rec["cjk_count"])

        tp = rec.get("throughline_progress")
        if isinstance(tp, dict):
            for line, hit in tp.items():
                if hit:
                    throughline_tally[line] += 1

        p, pd, rf = _foreshadow_actions(factual)
        fs_planted += p
        fs_paid += pd
        for fid in rf:
            fs_reinforced.setdefault(fid, []).append(ch)
        secrets += _secrets_revealed(factual)
        for r in rec.get("relationships", []) or []:
            if isinstance(r, dict):
                rel_changes.append({"from": r.get("from"), "to": r.get("to"), "ch": ch})

    # 🔴 2026-06-27 SYS-5 ①：第二遍预算 cliffhanger_resonance_next（需相邻章正文，故在逐章 rec 建好后跑）。
    _compute_cliffhanger_resonance(chapters, bodies, lo, hi, protagonist)
    for _ch_key, _rec in chapters.items():
        if "cliffhanger_resonance_next" in _rec:
            field_fill_counter["cliffhanger_resonance_next"] += 1

    # ---- cluster 级 rollup ----
    word_count = sum(cjk_list)
    length_stats = {}
    if cjk_list:
        m = mean(cjk_list)
        sd = pstdev(cjk_list) if len(cjk_list) > 1 else 0.0
        length_stats = {
            "median": int(median(cjk_list)),
            "mean": round(m, 1),
            "cv": round(sd / m, 3) if m else 0.0,
        }
    total_tl = sum(throughline_tally.values())
    throughline_distribution = (
        {line: round(throughline_tally[line] / total_tl, 3) for line in throughline_tally}
        if total_tl else {}
    )

    # title 取自 事件簇.json（其次 进度.cluster_blueprint）
    title = ""
    ec = _load_json(db / "事件簇.json", {}) or {}
    for c in ec.get("clusters", []) or []:
        if isinstance(c, dict) and cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == norm_cid:
            title = c.get("title") or ""
            break
    if not title:
        prog = _load_json(db / "进度.json", {}) or {}
        # 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测），
        # 裸 .items() 会 AttributeError 崩。先 normalize_blueprint 归一成 dict 再迭代。
        for cid, cdata in cluster_lookup.normalize_blueprint(prog).items():
            if cluster_lookup.normalize_cluster_id(cid) == norm_cid and isinstance(cdata, dict):
                title = cdata.get("title") or ""
                break

    patch: dict = {
        "title": title,
        "chapter_range": [lo, hi],
        "cluster_end_ch": hi,
        "word_count": word_count,
        "chapters": chapters,
    }
    if length_stats:
        patch["length_stats"] = length_stats
    if throughline_distribution:
        patch["throughline_distribution"] = throughline_distribution
    if fs_planted:
        patch["foreshadow_planted"] = sorted(set(fs_planted))
    if fs_paid:
        patch["foreshadow_paid"] = sorted(set(fs_paid))
    if fs_reinforced:
        patch["foreshadow_reinforced"] = fs_reinforced
    if secrets:
        patch["secrets_revealed"] = sorted(set(secrets))
    if rel_changes:
        patch["relationship_changes"] = rel_changes

    cluster_field_count = sum(1 for k in patch if k not in ("chapters",) and patch[k] not in (None, "", [], {}))

    cluster_summary_store.upsert_cluster(project_root, cluster_id, patch)

    return {
        "ok": True,
        "cluster_id": norm_cid,
        "title": title,
        "chapter_range": [lo, hi],
        "chapters_filled": len(chapters),
        "word_count": word_count,
        "cluster_rollup_fields": cluster_field_count,
        "chapter_field_fill": dict(field_fill_counter.most_common()),
        "pattern_aggregator": "ok" if _pat else "import_failed(skipped pattern_metrics/idiom_hits)",
        "emotion_aggregator": "ok" if _detect_emotions else "import_failed(skipped char_emotion_counts)",
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="cluster_summary_builder.py · 账本生产者")
    ap.add_argument("project", help="项目路径")
    ap.add_argument("--cluster", required=True, metavar="CLUSTER_ID", help="cluster_id（6 / cluster_006 均可）")
    args = ap.parse_args()

    root = Path(args.project)
    if not root.exists():
        print(f"[FATAL] 项目路径不存在: {root}", file=sys.stderr)
        sys.exit(2)

    res = build_cluster_summary(root, args.cluster)
    if not res.get("ok"):
        print(f"[FAIL] {res.get('cluster_id')} :: {res.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] 账本已写入 {res['cluster_id']} 「{res['title']}」 range={res['chapter_range']}")
    print(f"  填章数: {res['chapters_filled']} · cluster 总 CJK: {res['word_count']}")
    print(f"  cluster 级 rollup 字段: {res['cluster_rollup_fields']}")
    print(f"  pattern aggregator: {res['pattern_aggregator']}")
    print(f"  emotion aggregator: {res['emotion_aggregator']}")
    print(f"  per-chapter 字段填充统计 (字段: 命中章数):")
    for field, n in res["chapter_field_fill"].items():
        print(f"    {field}: {n}")


if __name__ == "__main__":
    main()
