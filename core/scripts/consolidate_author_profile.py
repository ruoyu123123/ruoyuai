#!/usr/bin/env python3
"""
consolidate_author_profile.py — 作者风格档 consumer 字段确定性规整器

北极星：①贴合作者风格 ⑤ advisory（不黑箱）⑥ 别过度复杂（纯 stdlib·复用 style_analyzer）。

【为什么】（2026-06-01 · 照顾弱模型驱动系统）
  蒸馏综合 agent 自由写 作者风格.json 的 quantitative/narrative_* schema，与系统多个 consumer
  （build_manifest D1/D3/D5 · validate_style 段长/对话 band · skill_contract_table）期望的标准
  键格式不符 → 契约债成簇（强模型尚且乱，弱模型必崩）。本模块把 consumer 关键**数值字段**改为
  **确定性脚本聚合**（从单章 metrics + dim），不靠 agent 自由写——agent 只负责创意（golden /
  风格标签 / core_style_signature），数值 schema 由脚本保证标准。

【做什么】
  从 蒸馏进度/ch{N}_metrics.json（style_analyzer profile）+ ch{N}.json（单章 dim）确定性聚合：
    quantitative：sentence_length{mean,std,intra_chapter_std_mean,p5/p50/p95} /
      dialogue_ratio{mean} + dialogue_ratio_pct{mean} / chapter_words{mean} /
      single_sentence_para_ratio + paragraph_length{single_sentence_para_ratio_mean,mean_chars} /
      paragraph_length_chars{p5,p50,p95,mean} / punctuation_density_per_1000 /
      function_word_fingerprint_per_1000 / inner_monologue_ratio{mean} /
      vocab_richness{ttr_mean,hapax_mean} + vocabulary_richness{type_token_ratio,hapax_ratio}
    narrative_craft.narrative_distance_distribution（dim34）
    narrative_fingerprint.character_behavior_loops（dim43）+ character_depth_grade_distribution（dim47）
    cross_chapter_diversity.opening_type_distribution + ending_type_distribution（dim16/18）
  读现有 作者风格.json（保留 agent 创意字段）→ 覆盖/补全上述 consumer 数值字段 → 写回标准 schema。

【边界】
  · 只规整 consumer **数值/分布**字段（确定性可聚合）；不动 golden_passages / core_style_signature /
    author / 5 大风格指纹等创意字段（agent 第一权威）。
  · 纯 stdlib + style_analyzer（aggregate_chapter_quantiles 算段长分位数）；零新依赖。
  · 缺数据的字段不写（不编造），让 consumer 走 fallback。

用法:
  python consolidate_author_profile.py --project workspace/styles/<书名>
  python consolidate_author_profile.py --project ... --total-chapters 250
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402  复用 aggregate_chapter_quantiles 算段长分位数

# dim47 人物丰满度 A/B/C/D → 语义等级（含"高"→ D5 band 识别为高深度放宽带）
_DEPTH_MAP = {"A": "高", "B": "中", "C": "低", "D": "很低"}


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def _detect_total(dist_dir: Path) -> int:
    """从 蒸馏进度 检测最大章号。"""
    mx = 0
    for f in dist_dir.glob("ch*_metrics.json"):
        try:
            mx = max(mx, int(f.stem.replace("ch", "").replace("_metrics", "")))
        except ValueError:
            continue
    return mx


def aggregate_quantitative(project: Path, total: int) -> dict:
    """从单章 metrics profile + 原文段长分位数聚合 quantitative 标准 schema。"""
    dist = project / "蒸馏进度"
    orig = project / "原文"
    sent_means, sent_stds, dia, ssr, para_means, ccs, imr = [], [], [], [], [], [], []
    ttr, hapax = [], []
    punct: dict[str, list[float]] = defaultdict(list)
    fw: dict[str, list[float]] = defaultdict(list)

    for n in range(1, total + 1):
        mp = dist / f"ch{n}_metrics.json"
        if not mp.exists():
            continue
        try:
            prof = json.loads(mp.read_text(encoding="utf-8")).get("profile", {})
        except (json.JSONDecodeError, OSError):
            continue
        ss = prof.get("sentence_stats", {}) or {}
        if _num(ss.get("mean")) is not None:
            sent_means.append(float(ss["mean"]))
        if _num(ss.get("std")) is not None:
            sent_stds.append(float(ss["std"]))
        ps = prof.get("paragraph_stats", {}) or {}
        if _num(ps.get("mean")) is not None:
            para_means.append(float(ps["mean"]))
        if _num(prof.get("dialogue_ratio")) is not None:
            dia.append(float(prof["dialogue_ratio"]))
        if _num(prof.get("single_sentence_para_ratio")) is not None:
            ssr.append(float(prof["single_sentence_para_ratio"]))
        if _num(prof.get("total_chinese_chars")) is not None:
            ccs.append(float(prof["total_chinese_chars"]))
        if _num(prof.get("inner_monologue_ratio")) is not None:
            imr.append(float(prof["inner_monologue_ratio"]))
        for k, v in (prof.get("punctuation_density_per_1000", {}) or {}).items():
            if _num(v) is not None:
                punct[k].append(float(v))
        for k, v in (prof.get("function_word_fingerprint_per_1000", {}) or {}).items():
            if _num(v) is not None:
                fw[k].append(float(v))
        vr = prof.get("vocabulary_richness", {}) or {}
        if _num(vr.get("type_token_ratio")) is not None:
            ttr.append(float(vr["type_token_ratio"]))
        if _num(vr.get("hapax_ratio")) is not None:
            hapax.append(float(vr["hapax_ratio"]))

    # 段长 band：章段均(para_means)**分窗 p95 取 max**·与 validate_style 段均 band 同量纲。
    # 2026-06-01 增量修(L4.14 卷型差异化·真作者保护)：全局 p95 会被多 batch 增量的后期短段卷型
    # 拉低(惊悚 ch1-250 段均 p95=72 / ch251-500=56 / 全局=66)→ band 上界缩小→误伤前期长段章。
    # 分窗(每~1/4 章)取各窗 p95 的 max→覆盖最长卷型段长(前期长段不被后期抹平)·p5 取各窗 min(短段保护)。
    para_q = None
    if len(para_means) >= 2:
        def _pctl(L, p):
            s = sorted(L); k = (len(s) - 1) * p; f = int(k); c = min(f + 1, len(s) - 1)
            return s[f] + (s[c] - s[f]) * (k - f)
        W = max(50, len(para_means) // 4)
        wins = [w for w in (para_means[i:i + W] for i in range(0, len(para_means), W)) if len(w) >= 2]
        if wins:
            para_q = {"p5": round(min(_pctl(w, 0.05) for w in wins), 2),
                      "p50": round(_pctl(para_means, 0.5), 2),
                      "p95": round(max(_pctl(w, 0.95) for w in wins), 2)}

    para_mean = _mean(para_means)
    plc_out = dict(para_q) if para_q else {}
    if para_mean is not None:
        plc_out["mean"] = para_mean
        plc_out.setdefault("p25", round((plc_out.get("p5", para_mean) + plc_out.get("p50", para_mean)) / 2, 2))
        plc_out.setdefault("p75", round((plc_out.get("p50", para_mean) + plc_out.get("p95", para_mean)) / 2, 2))

    dia_mean = _mean(dia)
    q: dict = {}
    sl = {}
    if _mean(sent_means) is not None:
        sl["mean"] = _mean(sent_means)
    if _mean(sent_stds) is not None:
        sl["std"] = _mean(sent_stds)
        sl["intra_chapter_std_mean"] = _mean(sent_stds)  # skill_contract_table 期望键
    if sl:
        q["sentence_length"] = sl
    if dia_mean is not None:
        q["dialogue_ratio"] = {"mean": dia_mean}
        q["dialogue_ratio_pct"] = {"mean": round(dia_mean * 100, 2)}  # skill_contract_table 期望键
    if _mean(ccs) is not None:
        q["chapter_words"] = {"mean": _mean(ccs)}  # validate_style 章字数 band + skill_contract_table
    ssr_mean = _mean(ssr)
    if ssr_mean is not None:
        q["single_sentence_para_ratio"] = ssr_mean
        q["paragraph_length"] = {"single_sentence_para_ratio_mean": ssr_mean}  # skill_contract_table 期望键
        if para_mean is not None:
            q["paragraph_length"]["mean_chars"] = para_mean
    if plc_out:
        q["paragraph_length_chars"] = plc_out
    if punct:
        q["punctuation_density_per_1000"] = {k: {"mean": _mean(v)} for k, v in punct.items()}
    if fw:
        q["function_word_fingerprint_per_1000"] = {k: {"mean": _mean(v)} for k, v in fw.items()}
    if _mean(imr) is not None:
        q["inner_monologue_ratio"] = {"mean": _mean(imr)}  # build_manifest D1 基线
    ttr_m, hapax_m = _mean(ttr), _mean(hapax)
    if ttr_m is not None or hapax_m is not None:
        q["vocab_richness"] = {"ttr_mean": ttr_m, "hapax_mean": hapax_m}
        q["vocabulary_richness"] = {"type_token_ratio": ttr_m, "hapax_ratio": hapax_m}
    return q


def aggregate_narrative(project: Path, total: int) -> tuple[dict, dict, dict]:
    """从单章 dim 聚合 narrative_craft / narrative_fingerprint / cross_chapter_diversity。"""
    dist = project / "蒸馏进度"
    ndist: Counter = Counter()
    loops: Counter = Counter()
    depth: Counter = Counter()
    opening: Counter = Counter()
    ending: Counter = Counter()
    # 阶段0 新增：补聚合 dim33/42/46/30（此前漏聚合·骨①骨②的确定性底座）
    tech: Counter = Counter()          # dim42 叙事技巧指纹（技巧类型分布）
    scene_q: Counter = Counter()       # dim46 场景结构质量（A/B/C/D 分布·仿 dim47）
    subtext_count = 0                  # dim30 留白潜台词（实例总数→密度）
    emotion_patterns: list = []        # dim33 情绪节拍图（原始串·阶段1 精聚合）
    for n in range(1, total + 1):
        jp = dist / f"ch{n}.json"
        if not jp.exists():
            continue
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        cc = d.get("cross_chapter", {}) or {}
        nc = d.get("narrative_craft", {}) or {}
        nf = d.get("narrative_fingerprint", {}) or {}
        op = cc.get("dim16_开头类型")
        if isinstance(op, str) and op.strip():
            opening[op.strip()] += 1
        ed = cc.get("dim18_章末类型")
        if isinstance(ed, str) and ed.strip():
            ending[ed.strip()] += 1
        nd = nc.get("dim34_叙事距离变化", "")
        if isinstance(nd, str):
            if "贴近" in nd or "近" in nd:
                ndist["近"] += 1
            if "拉远" in nd or "远" in nd:
                ndist["远"] += 1
            if "中" in nd:
                ndist["中"] += 1
        g = nf.get("dim47_人物丰满度") or ""
        if isinstance(g, str) and g.strip():
            depth[_DEPTH_MAP.get(g.strip()[:1], g.strip()[:1])] += 1
        bl = nf.get("dim43_角色行为循环")
        if isinstance(bl, list):
            for it in bl:
                desc = (it.get("行为") or it.get("pattern") or it.get("行为模式") or "") if isinstance(it, dict) else str(it)
                if desc and desc not in ("null", "None"):
                    loops[desc[:24]] += 1
        # 阶段0：dim42 叙事技巧指纹（技巧类型计数）
        tk = nf.get("dim42_叙事技巧指纹")
        if isinstance(tk, list):
            for it in tk:
                t = (it.get("技巧") or it.get("technique") or "") if isinstance(it, dict) else str(it)
                if t and t not in ("null", "None"):
                    tech[t[:24]] += 1
        # 阶段0：dim46 场景结构质量（A/B/C/D 首字母·仿 dim47 _DEPTH_MAP）
        sq = nf.get("dim46_场景结构质量") or ""
        if isinstance(sq, str) and sq.strip():
            g = sq.strip()[:1]
            if g in ("A", "B", "C", "D"):
                scene_q[_DEPTH_MAP[g]] += 1
        # 阶段0：dim30 留白潜台词（实例计数）
        sb = nc.get("dim30_留白潜台词")
        if isinstance(sb, list):
            subtext_count += sum(1 for x in sb if x and str(x) not in ("null", "None"))
        # 阶段0：dim33 情绪节拍图（原始串收集·阶段1 narrative_rhythm 精聚合）
        eb = nc.get("dim33_情绪节拍图")
        if isinstance(eb, str) and eb.strip() and "参见" not in eb:
            emotion_patterns.append(eb.strip())

    nc_out = {"narrative_distance_distribution": dict(ndist)} if ndist else {}
    if subtext_count:
        nc_out["subtext_instance_count"] = subtext_count
    if emotion_patterns:
        nc_out["emotion_beat_patterns"] = emotion_patterns[:20]
    nf_out = {}
    if loops:
        nf_out["character_behavior_loops"] = dict(loops.most_common(10))
    if depth:
        nf_out["character_depth_grade_distribution"] = dict(depth)
    if tech:
        nf_out["narrative_technique_distribution"] = dict(tech.most_common(10))
    if scene_q:
        nf_out["scene_structure_grade_distribution"] = dict(scene_q)

    def _dist(counter: Counter) -> dict:
        tot = sum(counter.values()) or 1
        return {k: {"count": v, "pct": round(v / tot, 3)} for k, v in counter.most_common()}

    ccd = {}
    if opening:
        ccd["opening_type_distribution"] = _dist(opening)
    if ending:
        ccd["ending_type_distribution"] = _dist(ending)
    return nc_out, nf_out, ccd


def consolidate(project: Path, total: int) -> dict:
    """聚合 + 规整 作者风格.json（保留创意字段，覆盖/补全 consumer 数值字段）。"""
    q = aggregate_quantitative(project, total)
    nc_out, nf_out, ccd = aggregate_narrative(project, total)

    targets = [project / "作者风格.json", project / "作者风格_FINAL.json"]
    written = []
    for sp in targets:
        if not sp.exists():
            continue
        style = json.loads(sp.read_text(encoding="utf-8"))
        # 合并 quantitative（脚本聚合的数值覆盖 agent 自由写的，保留 agent 额外键如 directives/_doc）
        sq = style.setdefault("quantitative", {})
        for k, v in q.items():
            if isinstance(v, dict) and isinstance(sq.get(k), dict):
                sq[k].update(v)  # 保留 agent 的 _doc 等，覆盖数值
            else:
                sq[k] = v
        # narrative_craft / narrative_fingerprint：补 consumer 字段（保留 agent 其他 dim 描述）
        if nc_out:
            style.setdefault("narrative_craft", {}).update(nc_out)
        if nf_out:
            style.setdefault("narrative_fingerprint", {}).update(nf_out)
        if ccd:
            scd = style.setdefault("cross_chapter_diversity", {})
            for k, v in ccd.items():
                scd.setdefault(k, v)  # 不覆盖 agent 已有的分布描述，仅补缺
        style.setdefault("_meta", {})["consumer_fields_consolidated"] = {
            "by": "consolidate_author_profile.py",
            "note": "consumer 数值/分布字段由确定性脚本聚合·照顾弱模型驱动·不靠 agent 自由 schema",
        }
        sp.write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(sp.name)
    return {"quantitative_keys": list(q.keys()), "narrative_craft": list(nc_out.keys()),
            "narrative_fingerprint": list(nf_out.keys()), "cross_chapter_diversity": list(ccd.keys()),
            "written": written}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="作者风格档 consumer 字段确定性规整器")
    ap.add_argument("--project", required=True, type=Path, help="风格库项目目录 workspace/styles/<书名>")
    ap.add_argument("--total-chapters", type=int, default=None, help="总章数（默认自动检测）")
    args = ap.parse_args(argv)
    project = args.project
    if not project.exists():
        print(f"[error] project 不存在: {project}", file=sys.stderr)
        return 2
    total = args.total_chapters or _detect_total(project / "蒸馏进度")
    if total <= 0:
        print("[error] 检测到总章数 = 0（蒸馏进度/ 无 metrics）", file=sys.stderr)
        return 2
    result = consolidate(project, total)
    print(f"[OK] consumer 字段规整完成（{total} 章聚合）→ {result['written']}")
    print(f"     quantitative: {result['quantitative_keys']}")
    print(f"     narrative_craft: {result['narrative_craft']} | narrative_fingerprint: {result['narrative_fingerprint']}")
    print(f"     cross_chapter_diversity: {result['cross_chapter_diversity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
