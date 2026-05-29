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
    cb = prog.get("cluster_blueprint", {}) or {}
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

    et = _first(factual, "ending_type", "chapter_ending_type")
    if et:
        rec["ending_type"] = et
    el = _first(factual, "ending_line", "last_line")
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

    aspects = _first(factual, "aspects_addressed", "active_aspects")
    if isinstance(aspects, list):
        rec["aspects_addressed"] = aspects

    clocks = _first(factual, "clocks_addressed", "clocks")
    if isinstance(clocks, list):
        rec["clocks_addressed"] = clocks

    dice = _first(factual, "fate_dice_consumed", "fate_dice", "dice_consumed")
    if isinstance(dice, list):
        rec["fate_dice_consumed"] = dice

    moves = _first(factual, "moves_used", "character_moves")
    if isinstance(moves, list):
        rec["moves_used"] = moves

    pee = _first(factual, "position_effect_evals", "position_effects")
    if isinstance(pee, list):
        rec["position_effect_evals"] = pee

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

    return rec, factual, self_eval


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
    ctx = {
        "protagonist": protagonist,
        "catchphrases": catchphrases,
        "cliche_dict": cliche_dict,
        "char_names": _character_names(db),
        "locations": _location_names(db),
    }

    chapters: dict[str, dict] = {}
    cjk_list: list[int] = []
    throughline_tally = Counter()
    fs_planted, fs_paid, fs_reinforced = [], [], {}
    secrets, rel_changes = [], []
    field_fill_counter: Counter = Counter()

    for ch in range(lo, hi + 1):
        rec, factual, self_eval = _build_chapter_record(project_root, db, cluster_id, ch, ctx)
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
        for cid, cdata in (prog.get("cluster_blueprint", {}) or {}).items():
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
