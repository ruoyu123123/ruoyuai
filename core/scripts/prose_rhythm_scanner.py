#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prose_rhythm_scanner.py — 句法节奏 / 「流水账作文感」检测（cluster 视野 · 作者基线感知 · advisory）

2026-06-03 新增。根因（《无脸者守则》实证 + 惊悚乐园真作者金标准对比）：
  用户直觉「不像网文像作文·谁做什么做什么做什么」= 主语+裸动作 play-by-play 流水账。
  量化：本书主语开头句 27.8% vs 真作者 15.1%；均句长 16.8 vs 33.0；最长「主语+动作」streak
  3.8(峰值6) vs 2.1。根因三层——蒸馏✅对(作者档 sentence_length.mean=31)，writer 注入了但
  弱模型守不住 + C3 只管段首管不到句子级流水账，**scanner 层 validate_style 故意「绝不查句长」
  → 句长偏短全程无人报警**。本 scanner 补这个检测闭环。

探针（全 advisory · 以作者风格档基线为第一权威 · 北极星⑤）：
  1. sentence_too_short  作者内 z-band（self-ECDF·2026-06-16 升级）：有 std → z=(cluster_mean-μ)/σ，
     z<-1.0 minor / z<-1.8 major，**只报偏短下尾**（长句永不报·北极星③不干涉创作）；
     **绝对地板取或**：mean < μ×0.62 也触发（防高 σ 作者下尾阈值太松漏报碎句·实证：惊悚 σ=23
     时碎句 mean16.8 仅 z≈-0.61 触不到 -1.0，靠 floor62=19.2 兜住）。无 std 退通用 ×0.70。
  2. subject_action_streak  连续句首是「主语(人物卡角色名/代词)」的流水账 streak ≥4 minor/≥6 major
  3. subject_start_ratio_high  主语开头叙述句占比 > 24%（**通用兜底·非作者锚**：作者档暂无主语
     占比分布·补它需 style_analyzer 加一维抽取+回灌全部作者档·中成本 defer·见 design D 件）
  4. inverted_modifier_mold  段首「前置长定语+的+主语后置」倒装模具复用（同语法骨架）
  5. intensity_adverb_inflation  强度副词通胀（极其/死死/毫无/猛地…）
  6. paragraph_too_short  cluster 段长均值 < 作者 paragraph_length_chars.p5（明显比作者最短的
     章还碎）→ minor·**单边下尾**（长段不报·长不是流水账问题）·无作者段长分位 → 跳过

🔴 self-ECDF 不是跨作者群体锚：population=「该作者历史章节」而非 WebNovelBench 那种跨 4000 部语料。
   跨作者百分位会把 cluster 往「通用网文均值」拽，反噬北极星（惊悚乐园句长 31 离群点教训：拿群体
   百分位会把它判「太长」往均值拉）。这里只用作者自身 μ/σ/分位，高 σ 作者自动获宽容带、低 σ 收紧。

不做「单句独行率上限」探针：cluster_001(优秀样本)独行率最高(77-84%)但质量好(吐槽短句)，
独行率高本身不是问题(爽文要独行)，问题是独行的是不是裸动作——靠句长+streak 精准抓，独行率会误伤。

输出：JSON {scanner, violations:[{kind,severity,...}], verdict, gate_level, metrics, author_baseline}
用法：python prose_rhythm_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

PRONOUNS = ['他们', '她们', '它们', '两人', '二人', '众人', '他', '她', '它']
# 句长偏短双档系数（相对作者基线 mean·**仅无 std 老档兜底路径**用）
SHORT_MINOR, SHORT_MAJOR = 0.70, 0.55
# 作者内 z-band（self-ECDF·有 std 时用·只报偏短下尾·北极星③长句永不报）
# 实证：诡秘 σ=27 高方差作者下尾自动获宽容带，主神 σ=16 低方差才收紧——WebNovelBench
# z-score 归一精髓，只是 population 换成「该作者历史章节」而非跨 4000 部语料。
SHORT_Z_MINOR, SHORT_Z_MAJOR = -1.0, -1.8   # z<-1.0 minor（≈作者自身第16百分位下方）/ z<-1.8 major（≈第4百分位）
# 绝对地板取或：高 σ 作者 z 阈值太松会漏报碎句（惊悚 σ=23 时 mean16.8 仅 z≈-0.61 触不到 -1.0），
# 靠 mean<μ×0.62 兜住（floor62=19.2·实证真作者 cluster_mean 全在 floor 之上不误伤）。
SHORT_ABS_FLOOR = 0.62
STREAK_MINOR, STREAK_MAJOR = 4, 6
DEFAULT_AUTHOR_SENT_MEAN = 26.0   # 无作者档时的通用兜底句长基线（偏保守·网文中位）
# 探针9（R20 W9 Batch-CC P2 2026-06-21 · SEO id 13）300字 payload 段长占比
# 移动端竖屏阅读经验(番茄/起点/七猫 2024-2025)：单段 300CJK±50 是手机一屏 payload
# 甜区(滑两下读完不滑屏)。低于占比下限 = 段落太碎(读者频繁滑屏疲劳)，高于上限 =
# 段落太长(滑很久才到下一段)。作者档 paragraph_300cjk_share_baseline {target, std}
# 第一权威·无 baseline 兜底 target=0.20 std=0.10（保守范围）。advisory shadow·北极星⑤
# 写作工艺非格式硬约束·绝不 hard_gate·env PROSE_300CJK_PAYLOAD_MODE 默认 shadow。
PARA_300_CJK_LOW = 250    # 段长 ≥ 250 才计入 300CJK 段
PARA_300_CJK_HIGH = 350   # 段长 ≤ 350 才计入 300CJK 段
PARA_300_SHARE_TARGET_DEFAULT = 0.20
PARA_300_SHARE_STD_DEFAULT = 0.10
PARA_300_SHARE_Z_THRESHOLD = 1.5  # |z|>1.5 报偏离

# 探针7 句长方差塌缩（burstiness·cluster std vs 作者 std·单边偏均匀·治 flash 匀速碎句·env PROSE_BURSTINESS_MODE 默认 shadow）
# 金标准校准（2026-06-16·6 作者各 10 cluster）：真作者 cluster_std/作者 std 最小 0.61（高 σ 作者偏低）→ 阈值
# 0.5/0.4 留余量（< 0.61 真作者绝不误报·critic 建议 0.6 余量仅 0.01 太险已下调·北极星⑤防矫枉过正）。
BURST_R_MINOR, BURST_R_MAJOR = 0.5, 0.4
BURST_ABS_STD_FLOOR = 5.0   # 无作者档兜底：句长 std < 5（几乎齐平·匀速）才报
# 探针8 标点距离 Weibull 形状指纹（2026-06-20 R13 W6 Batch-R P2·shadow）
# Dolina et al. 2025 Chaos 35:023155 中文散文标点 Weibull 多重分形 + CLSInfra + Bagnall 2016。
# 6 类标点(。, ！？—— ……)分别提取相邻同类符号 token 距离序列·scipy weibull_min.fit 估(k, λ)·
# 与作者档 slow_update.punctuation_distance_weibull[mark] KS 检验·Δ>0.15 advisory·
# SkillOpt 不许动 slow_update（已锁死段）。
PUNCT_WEIBULL_KS_THRESHOLD = 0.15
PUNCT_WEIBULL_MIN_SAMPLES = 8   # 单标点距离样本 <8 跳过（统计不可靠）
PUNCT_WEIBULL_MARKS = ("。", "，", "！", "？", "——", "……")
DEFAULT_SUBJ_PCT_CAP = 24.0       # 主语开头占比通用上限(%)
# 段首倒装模具（前置长定语+的+主语后置·补「同语法骨架复用」缺口·
# memory feedback_inverted_modifier_sentence_mold_overuse·cluster_001 实测 34 次/约 1/9 段）
INVERTED_MOLD_MINOR, INVERTED_MOLD_MAJOR = 0.12, 0.20   # 倒装段首占比阈值
INVERTED_STREAK_MINOR, INVERTED_STREAK_MAJOR = 3, 5      # 连续倒装段首 streak
INVERTED_MIN_COUNT = 3                                   # 至少 N 处才报（避免少量误报）
# 强度副词通胀（gen-model 写作伴生套路·memory feedback_inverted L19·与倒装同批一起治）
INTENSITY_ADVERBS = ['极其', '死死', '毫无', '猛地', '狠狠', '紧紧', '牢牢', '拼命', '疯狂']
INTENSITY_PER_1K_MINOR, INTENSITY_PER_1K_MAJOR = 3.0, 5.0   # 总强度副词密度 per 1000 CJK
INTENSITY_SINGLE_MAX = 12                                    # 单个强度副词频次上限（如极其28）
# 对话起始引号字符集（含中文弯引号 U+201C/U+201D=项目正典对话格式·补漏与姊妹 scanner
# cross_scene_voice_drift_scanner/validate_style 对齐·三处统一引用防再次漏改）
_DIALOG_OPEN_CHARS = '""“”\'「『（('


def cjk(s: str) -> int:
    return sum(1 for c in s if '一' <= c <= '鿿')


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _author_weibull_baseline(project: Path | None, style_path: Path | None) -> dict:
    """读作者档 slow_update.punctuation_distance_weibull[mark] = {k, lambda} 形状指纹。
    R13 P2 落地·SkillOpt 不许动 slow_update 段（北极星⑤锁死）。无作者档 → 返回 {}。"""
    data = None
    if style_path and style_path.exists():
        data = _load_json(style_path)
    if data is None and project:
        for cand in (project / "_数据库" / "作者风格.json",
                     project / "_数据库" / "作者风格_FINAL.json"):
            if cand.exists():
                data = _load_json(cand)
                if data:
                    break
    if not isinstance(data, dict):
        return {}
    su = data.get("slow_update")
    if not isinstance(su, dict):
        return {}
    pw = su.get("punctuation_distance_weibull")
    if not isinstance(pw, dict):
        return {}
    out = {}
    for mark, params in pw.items():
        if isinstance(params, dict):
            k = params.get("k")
            lam = params.get("lambda") or params.get("scale")
            if isinstance(k, (int, float)) and isinstance(lam, (int, float)):
                out[mark] = {"k": float(k), "lambda": float(lam)}
    return out


def _punctuation_distance_series(text: str, mark: str) -> list:
    """返回相邻同类标点之间的 token 距离序列（按 CJK + 字符近似 token 数）。"""
    positions = []
    i = 0
    while True:
        j = text.find(mark, i)
        if j < 0:
            break
        positions.append(j)
        i = j + len(mark)
    if len(positions) < 2:
        return []
    return [positions[k + 1] - positions[k] for k in range(len(positions) - 1)]


def _weibull_ks(distances: list, k_author: float, lam_author: float) -> float | None:
    """KS 检验：cluster 经验分布 vs 作者 Weibull(k, λ)。返回 KS 统计量。
    依赖 scipy（已可用·零环境额外）·失败 → None（探针自跳过）。"""
    try:
        from scipy.stats import kstest, weibull_min  # noqa: WPS433 (lazy import)
    except Exception:
        return None
    if not distances or len(distances) < PUNCT_WEIBULL_MIN_SAMPLES:
        return None
    try:
        stat, _p = kstest(distances, weibull_min.cdf, args=(k_author, 0.0, lam_author))
        return float(stat)
    except Exception:
        return None


def _author_baseline(project: Path | None, style_path: Path | None) -> dict:
    """从作者风格档读句法**分布矩**：sentence_length.{mean, std} + paragraph_length_chars.{p5,p50,p95}。
    北极星⑤第一权威·self-ECDF 数据源（population=作者自身历史章节·consolidate_author_profile
    已确定性产出·8 本作者档 std 全非空 14.7-27.0）。缺字段回退 None（探针自退通用兜底）。"""
    data = None
    if style_path and style_path.exists():
        data = _load_json(style_path)
    if data is None and project:
        for cand in (project / "_数据库" / "作者风格.json",
                     project / "_数据库" / "作者风格_FINAL.json"):
            if cand.exists():
                data = _load_json(cand)
                if data:
                    break
    sent_mean = sent_std = para_p5 = para_p50 = para_p95 = None
    para_300_target = para_300_std = None
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        sl = q.get("sentence_length") or {}
        if isinstance(sl, dict):
            if isinstance(sl.get("mean"), (int, float)):
                sent_mean = float(sl["mean"])
            # std 优先 sentence_length.std，退 intra_chapter_std_mean（同口径·章内句长离散）
            for k in ("std", "intra_chapter_std_mean"):
                v = sl.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    sent_std = float(v)
                    break
        pl = q.get("paragraph_length_chars") or {}
        if isinstance(pl, dict):
            if isinstance(pl.get("p5"), (int, float)):
                para_p5 = float(pl["p5"])
            if isinstance(pl.get("p50"), (int, float)):
                para_p50 = float(pl["p50"])
            if isinstance(pl.get("p95"), (int, float)):
                para_p95 = float(pl["p95"])
        # 探针9 300字 payload 作者档 baseline (R20 W9 Batch-CC P2)
        p300 = data.get("paragraph_300cjk_share_baseline")
        if isinstance(p300, dict):
            if isinstance(p300.get("target"), (int, float)):
                para_300_target = float(p300["target"])
            if isinstance(p300.get("std"), (int, float)):
                para_300_std = float(p300["std"])
    return {"sentence_mean": sent_mean, "sentence_std": sent_std,
            "para_p5": para_p5, "para_p50": para_p50, "para_p95": para_p95,
            "para_300_target": para_300_target, "para_300_std": para_300_std}


def _char_names(project: Path | None) -> list:
    """读人物卡 characters[].name/id 作主语名集（抓「池迟做X」流水账·非仅代词）。"""
    if not project:
        return []
    pc = _load_json(project / "_数据库" / "人物卡.json")
    names = []
    if isinstance(pc, dict):
        for c in pc.get("characters", []) or []:
            if isinstance(c, dict):
                for k in ("name", "id"):
                    v = c.get(k)
                    if isinstance(v, str) and cjk(v) >= 2:
                        names.append(v)
    return names


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    baseline = _author_baseline(project, style_path)
    sent_mean_base = baseline["sentence_mean"] or DEFAULT_AUTHOR_SENT_MEAN
    sent_std_base = baseline.get("sentence_std")   # None → 探针1 走通用 ×0.70 兜底
    subj_words = _char_names(project) + PRONOUNS

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    body = [l for l in lines if not re.match(r'^第\d+章', l) and not l.startswith('【')]
    # cluster 段长均值（CJK 字/段·与作者 paragraph_length_chars 同口径·供探针6）
    para_lens = [cjk(p) for p in body if cjk(p) >= 1]
    para_mean = round(statistics.mean(para_lens), 1) if para_lens else 0.0

    # 全文句长（含对话·对齐作者 sentence_length.mean 口径）
    all_lens = []
    # 叙述句（排除对话主导句）用于主语流水账判定
    narr_is_subj = []
    for p in body:
        for s in re.split(r'(?<=[。！？…])', p):
            s = s.strip()
            if cjk(s) < 2:
                continue
            all_lens.append(cjk(s))
            head_raw = s.lstrip('　 ')
            if head_raw[:1] in _DIALOG_OPEN_CHARS:   # 对话主导句不计主语流水账
                continue
            head = head_raw.lstrip(_DIALOG_OPEN_CHARS)
            narr_is_subj.append(any(head.startswith(w) for w in subj_words))

    if not all_lens:
        return {"scanner": "prose_rhythm", "violations": [], "verdict": "PASS",
                "gate_level": "advisory", "metrics": {}, "_doc": "空文本"}

    mean_len = round(statistics.mean(all_lens), 1)
    narr_n = len(narr_is_subj)
    subj_n = sum(1 for x in narr_is_subj if x)
    subj_pct = round(subj_n / narr_n * 100, 1) if narr_n else 0.0
    # 最长连续主语开头 streak
    streak = mx = 0
    for x in narr_is_subj:
        streak = streak + 1 if x else 0
        mx = max(mx, streak)

    violations = []

    # 探针 1：句长偏短（作者内 z-band / self-ECDF·只报偏短下尾·长句永不报·北极星③）
    ratio = round(mean_len / sent_mean_base, 2) if sent_mean_base else 1.0
    short_z = None
    if sent_std_base:
        # 有 std → 作者内 z-score（population=该作者历史章节·非跨作者群体锚）
        short_z = round((mean_len - sent_mean_base) / sent_std_base, 2)
        floor_abs = sent_mean_base * SHORT_ABS_FLOOR
        below_floor = mean_len < floor_abs
        # 触发 = z 越下尾 OR 跌破绝对地板（绝对地板兜高 σ 作者漏报·实证惊悚 σ=23 mean16.8 z≈-0.61 靠地板兜）
        if short_z < SHORT_Z_MINOR or below_floor:
            # 严重度纯 z 驱动（design 铁律：z<-1.8 → major / 否则 minor）·绝对地板只扩触发不升档，
            # 但极端碎句兜底升 major（mean < μ×0.45·半个地板区·灾难性碎·z 未触发也判 major）
            sev = 'major' if (short_z < SHORT_Z_MAJOR or mean_len < sent_mean_base * 0.45) else 'minor'
            pctl = '4%' if short_z < SHORT_Z_MAJOR else '16%'
            trig = (f'z={short_z}<{SHORT_Z_MINOR}(落作者自身分布下{pctl}尾部)' if short_z < SHORT_Z_MINOR
                    else f'mean<作者{round(floor_abs,1)}(绝对地板·μ×{SHORT_ABS_FLOOR})')
            violations.append({
                'kind': 'sentence_too_short', 'severity': sev,
                'cluster_mean': mean_len, 'author_mean': round(sent_mean_base, 1),
                'author_std': round(sent_std_base, 1), 'z': short_z, 'ratio': ratio,
                'hint': f'均句长 {mean_len}·作者基线 μ={round(sent_mean_base,1)}±σ={round(sent_std_base,1)}·{trig}'
                        f'→碎句流水账/作文感；多用逗号连缀的复合长句承载信息(因果/让步/比喻)，'
                        f'长短句交替而非匀速短句。(高 σ 作者本就长短摆动大→宽容；此处仍判偏短)',
            })
    elif ratio < SHORT_MINOR:
        # 无 std（老档）→ 退回通用比值兜底（与 2026-06-03 版一致·回归不破）
        sev = 'major' if ratio < SHORT_MAJOR else 'minor'
        violations.append({
            'kind': 'sentence_too_short', 'severity': sev,
            'cluster_mean': mean_len, 'author_mean': round(sent_mean_base, 1),
            'ratio': ratio,
            'hint': f'均句长 {mean_len} 仅作者基线 {round(sent_mean_base,1)} 的 {round(ratio*100)}%→碎句流水账/作文感；'
                    f'多用逗号连缀的复合长句承载信息(因果/让步/比喻)，长短句交替而非匀速短句',
        })

    # 探针 2：主语+动作流水账 streak
    if mx >= STREAK_MINOR:
        sev = 'major' if mx >= STREAK_MAJOR else 'minor'
        violations.append({
            'kind': 'subject_action_streak', 'severity': sev,
            'max_streak': mx, 'threshold': STREAK_MAJOR if sev == 'major' else STREAK_MINOR,
            'hint': f'连续 {mx} 句以「主语(人名/他/她)」开头=「谁做什么做什么」流水账；'
                    f'第3句起换起头(环境/动作中段/对话/心理/感官/省主语)，或把动作融进对话与评论',
        })

    # 探针 3：主语开头占比偏高（vs 作者基线·真作者约 15%）
    subj_cap = max(DEFAULT_SUBJ_PCT_CAP, 0.0)  # 作者档暂无主语占比基线→用通用上限 24%
    if subj_pct > subj_cap:
        sev = 'major' if subj_pct > subj_cap + 12 else 'minor'
        violations.append({
            'kind': 'subject_start_ratio_high', 'severity': sev,
            'subj_start_pct': subj_pct, 'cap': subj_cap,
            'hint': f'主语开头叙述句占比 {subj_pct}%(网文金标准约15%·阈值{subj_cap}%)→句首单一像作文；'
                    f'句首多样化:环境/状语/对话/感官/说书人评论起头',
        })

    # 探针 4：段首倒装句式模具（前置长定语+的+主语后置·补「同语法骨架复用」缺口）
    _subj_alt = '|'.join(re.escape(w) for w in subj_words) if subj_words else None
    inverted_flags = []
    if _subj_alt:
        _inv_re = re.compile(r'^(.{2,14})的(' + _subj_alt + r')(?:[，,。、！？]|[一-鿿])')
        for p in body:
            head = p.lstrip('　 ')
            if head[:1] in _DIALOG_OPEN_CHARS:       # 对话主导段不计
                inverted_flags.append(False)
                continue
            inverted_flags.append(bool(_inv_re.match(head)))
    inv_n = sum(inverted_flags)
    para_n = len(inverted_flags)
    inv_pct = round(inv_n / para_n, 3) if para_n else 0.0
    inv_streak = inv_mx = 0
    for x in inverted_flags:
        inv_streak = inv_streak + 1 if x else 0
        inv_mx = max(inv_mx, inv_streak)
    if inv_n >= INVERTED_MIN_COUNT and (inv_pct >= INVERTED_MOLD_MINOR or inv_mx >= INVERTED_STREAK_MINOR):
        sev = ('major' if (inv_pct >= INVERTED_MOLD_MAJOR or inv_mx >= INVERTED_STREAK_MAJOR)
               else 'minor')
        violations.append({
            'kind': 'inverted_modifier_mold', 'severity': sev,
            'inverted_pct': round(inv_pct * 100, 1), 'max_streak': inv_mx, 'count': inv_n,
            'hint': f'段首「前置长定语+的+主语后置」倒装模具 {inv_n} 处({round(inv_pct*100)}%段·最长连 {inv_mx})'
                    f'=同语法骨架复用(摸出手机的陆参/愣住的他)→塑料感；段首句法骨架多样化'
                    f'(直接主语/环境状语/动作中段/对话/心理起头·别让「X的[主语]」霸占段首)',
        })

    # 探针 5：强度副词通胀（极其/死死/毫无/猛地等·gen-model 伴生套路·与倒装同批一起治）
    total_cjk = sum(all_lens)
    intensity_counts = {}
    for adv in INTENSITY_ADVERBS:
        c = text.count(adv)
        if c:
            intensity_counts[adv] = c
    intensity_total = sum(intensity_counts.values())
    intensity_per_1k = round(intensity_total / total_cjk * 1000, 2) if total_cjk else 0.0
    single_overused = {a: c for a, c in intensity_counts.items() if c >= INTENSITY_SINGLE_MAX}
    if intensity_per_1k >= INTENSITY_PER_1K_MINOR or single_overused:
        sev = ('major' if (intensity_per_1k >= INTENSITY_PER_1K_MAJOR
                           or any(c >= INTENSITY_SINGLE_MAX * 2 for c in single_overused.values()))
               else 'minor')
        top = sorted(intensity_counts.items(), key=lambda x: -x[1])[:5]
        violations.append({
            'kind': 'intensity_adverb_inflation', 'severity': sev,
            'per_1k': intensity_per_1k, 'total': intensity_total,
            'top': top, 'single_overused': single_overused,
            'hint': f'强度副词通胀(极其/死死/毫无/猛地等) {intensity_total} 处({intensity_per_1k}/千字'
                    f'·top {top[:3]})=gen-model 强度通胀套路；删冗余强度词·用具体动作/细节传强度'
                    f'(死死抓住→指节发白·极其愤怒→把杯子摔了)',
        })

    # 探针 6：段长偏短（cluster 段长均值 < 作者 paragraph_length_chars.p5·单边下尾·长段不报）
    # self-ECDF 段长维：比作者「最短的章」还碎=明显偏离作者自身段落节奏。无作者段长分位→跳过。
    para_p5 = baseline.get("para_p5")
    if para_p5 and para_mean > 0 and para_mean < para_p5:
        # major 仅当远低于 p5（< p5×0.8·明显碎过作者最短章）
        sev = 'major' if para_mean < para_p5 * 0.8 else 'minor'
        violations.append({
            'kind': 'paragraph_too_short', 'severity': sev,
            'cluster_para_mean': para_mean, 'author_para_p5': round(para_p5, 1),
            'author_para_p50': round(baseline.get("para_p50") or 0, 1),
            'hint': f'段长均值 {para_mean} < 作者段长第5百分位 {round(para_p5,1)}(中位{round(baseline.get("para_p50") or 0,1)})'
                    f'=比作者最短的章还碎→过度切碎的塑料段落感；合并语义连贯的相邻短段，'
                    f'让段落承载完整的动作单元/对话回合而非一句一段切到底。(长段不是问题·只报偏碎)',
        })

    # 探针 7：句长方差塌缩（burstiness collapse·cluster 句长 std vs 作者 std·单边偏均匀·治 flash 匀速碎句）
    # env PROSE_BURSTINESS_MODE 默认 shadow（金标准校准真作者最小 r=0.61·阈值 0.5/0.4 留余量·检测力
    # [flash 匀速碎句真 r<0.5?]待 gen-model 草稿验证再 active）。与探针1解耦：探针1 已报 mean 偏短则不重复报。
    burst_mode = (os.environ.get("PROSE_BURSTINESS_MODE") or "shadow").strip().lower()
    cluster_sent_std = round(statistics.pstdev(all_lens), 1) if len(all_lens) >= 2 else None
    burst_ratio = (round(cluster_sent_std / sent_std_base, 2)
                   if (cluster_sent_std is not None and sent_std_base) else None)
    short_already = any(v['kind'] == 'sentence_too_short' for v in violations)  # 探针1解耦防双计数
    if burst_mode == "active" and cluster_sent_std is not None and not short_already:
        if sent_std_base and burst_ratio is not None and burst_ratio < BURST_R_MINOR:
            sev = 'major' if burst_ratio < BURST_R_MAJOR else 'minor'
            violations.append({
                'kind': 'burstiness_collapse', 'severity': sev,
                'cluster_std': cluster_sent_std, 'author_std': round(sent_std_base, 1),
                'burst_ratio': burst_ratio,
                'hint': f'句长方差 std={cluster_sent_std} 仅作者基线 σ={round(sent_std_base,1)} 的 {round(burst_ratio*100)}%'
                        f'→句长过度均匀(匀速碎句·mean 可能达标但缺长短摆动)；长短句交替(短句爆发力+长句'
                        f'承载因果/铺陈)·别让句长齐平像作文。(单边·只报偏均匀·高方差永不报·北极星③)',
            })
        elif not sent_std_base and cluster_sent_std < BURST_ABS_STD_FLOOR:
            violations.append({
                'kind': 'burstiness_collapse', 'severity': 'minor',
                'cluster_std': cluster_sent_std, 'author_std': None,
                'hint': f'句长方差 std={cluster_sent_std}<{BURST_ABS_STD_FLOOR}(无作者档·绝对地板)=句长几乎齐平'
                        f'→匀速碎句；长短句交替增节奏。(单边·只报偏均匀)',
            })

    # 探针 8：标点距离 Weibull 形状指纹（R13 W6 Batch-R P2·shadow·advisory）
    # env PROSE_PUNCT_WEIBULL_MODE 默认 shadow·依赖作者档 slow_update.punctuation_distance_weibull
    # 无作者档 → 静默 skip（北极星②）
    weibull_mode = (os.environ.get("PROSE_PUNCT_WEIBULL_MODE") or "shadow").strip().lower()
    weibull_baseline = _author_weibull_baseline(project, style_path)
    weibull_results = {}
    if weibull_mode != "off" and weibull_baseline:
        worst_mark = None
        worst_ks = 0.0
        for mark in PUNCT_WEIBULL_MARKS:
            params = weibull_baseline.get(mark)
            if not params:
                continue
            dist_series = _punctuation_distance_series(text, mark)
            ks = _weibull_ks(dist_series, params["k"], params["lambda"])
            if ks is None:
                weibull_results[mark] = {"samples": len(dist_series), "ks": None,
                                         "k_author": params["k"], "lambda_author": params["lambda"]}
                continue
            weibull_results[mark] = {"samples": len(dist_series), "ks": round(ks, 4),
                                     "k_author": params["k"], "lambda_author": params["lambda"]}
            if ks > worst_ks:
                worst_ks = ks
                worst_mark = mark
        if worst_mark and worst_ks > PUNCT_WEIBULL_KS_THRESHOLD:
            msg = (f"标点距离 Weibull 指纹偏离: '{worst_mark}' KS={round(worst_ks, 3)}>"
                   f"{PUNCT_WEIBULL_KS_THRESHOLD}·节奏分布形状与作者基线不同 "
                   f"(slow_update.punctuation_distance_weibull)")
            if weibull_mode == "active":
                violations.append({
                    'kind': 'punctuation_distance_weibull', 'severity': 'minor',
                    'mark': worst_mark, 'ks': round(worst_ks, 3),
                    'threshold': PUNCT_WEIBULL_KS_THRESHOLD,
                    'per_mark_results': weibull_results,
                    'hint': msg,
                })
            else:
                print(f"[SHADOW] prose_rhythm punct_weibull: {msg} — 不上报", file=sys.stderr)

    # 探针 9：300字 payload share (R20 W9 Batch-CC P2·SEO id 13·shadow advisory)
    # 段长 250-350 CJK 段占比 vs 作者档 paragraph_300cjk_share_baseline.{target, std}
    # 兜底 target=0.20·std=0.10·|z|>1.5 报 PARAGRAPH_300CJK_PAYLOAD_OFF_BAND·北极星⑤
    payload_mode = (os.environ.get("PROSE_300CJK_PAYLOAD_MODE") or "shadow").strip().lower()
    p300_count = sum(1 for pl in para_lens if PARA_300_CJK_LOW <= pl <= PARA_300_CJK_HIGH)
    para_300_share = round(p300_count / len(para_lens), 4) if para_lens else 0.0
    p300_target = baseline.get("para_300_target")
    p300_std = baseline.get("para_300_std")
    use_target = p300_target if p300_target is not None else PARA_300_SHARE_TARGET_DEFAULT
    use_std = p300_std if p300_std is not None else PARA_300_SHARE_STD_DEFAULT
    p300_z = round((para_300_share - use_target) / use_std, 3) if use_std > 0 else 0.0
    if payload_mode == "active" and len(para_lens) >= 5 and abs(p300_z) > PARA_300_SHARE_Z_THRESHOLD:
        direction = "偏多" if p300_z > 0 else "偏少"
        violations.append({
            'kind': 'paragraph_300cjk_payload_off_band', 'severity': 'minor',
            'code': 'PARAGRAPH_300CJK_PAYLOAD_OFF_BAND',
            'paragraph_300cjk_share': para_300_share,
            'author_target': use_target, 'author_std': use_std, 'z': p300_z,
            'hint': (f'300CJK±50 段占比 {round(para_300_share*100,1)}% (作者档 target={round(use_target*100,1)}%'
                    f'±{round(use_std*100,1)}%·z={p300_z}{direction})·移动端 payload 甜区偏离'
                    f'·SEO/竖屏阅读体验 advisory·绝不 hard_gate'),
        })

    has_major = any(v['severity'] == 'major' for v in violations)
    verdict = 'PASS' if not violations else ('FAIL_MAJOR' if has_major else 'FAIL_MINOR')
    return {
        'scanner': 'prose_rhythm',
        'violations_count': len(violations),
        'violations': violations,
        'verdict': verdict,
        'gate_level': 'advisory',
        'metrics': {
            'sentence_mean': mean_len,
            'sentence_z': short_z,                 # 作者内 z-score（None=无 std 走通用兜底）
            'paragraph_mean': para_mean,
            'subject_start_pct': subj_pct,
            'max_subject_streak': mx,
            'narrative_sentences': narr_n,
            'inverted_mold_count': inv_n,
            'inverted_mold_pct': round(inv_pct * 100, 1),
            'inverted_mold_max_streak': inv_mx,
            'intensity_adverb_total': intensity_total,
            'intensity_adverb_per_1k': intensity_per_1k,
            'sentence_std_cluster': cluster_sent_std,        # 探针7 cluster 句长 std
            'burstiness_ratio': burst_ratio,                 # 探针7 cluster_std/作者 std（<0.5 偏均匀报）
            'punct_weibull_per_mark': weibull_results,       # 探针8 标点距离 Weibull KS（R13 W6 Batch-R P2）
            'paragraph_300cjk_share': para_300_share,        # 探针9 300CJK±50 段占比（R20 W9 Batch-CC P2）
            'paragraph_300cjk_z': p300_z,                    # 探针9 z=(share-target)/std
        },
        'author_baseline': {
            'sentence_mean': sent_mean_base,
            'sentence_std': sent_std_base,
            'para_p5': baseline.get("para_p5"),
            'para_p50': baseline.get("para_p50"),
            'para_p95': baseline.get("para_p95"),
            'para_300_target': p300_target,                  # 探针9 作者档 300CJK 占比 target
            'para_300_std': p300_std,                        # 探针9 作者档 300CJK 占比 std
            'from_author_profile': baseline["sentence_mean"] is not None,
            'self_ecdf_active': sent_std_base is not None,   # True=走作者内 z-band·False=通用兜底
        },
        '_doc': '句法节奏/流水账作文感检测·作者分布矩第一权威(self-ECDF z-band)·advisory·补 validate_style「不查句长」缺口',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    p = Path(args.draft)
    if not p.exists():
        print(f"路径不存在: {p}")
        sys.exit(2)
    result = scan(p.read_text(encoding='utf-8'),
                  project=Path(args.project) if args.project else None,
                  style_path=Path(args.style) if args.style else None)
    result['file'] = str(p)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
