#!/usr/bin/env python3
"""
validate_style.py — 中文小说风格合规校验器

用法：
  python validate_style.py <章节txt路径> [--style <风格JSON路径>] [--strict]

校验 15 项（PASS/WARN/FAIL）：对话占比、段落均长、极短段占比、单句成段率、
拟声格式、极长句、禁用词、AI对话标签、配额词、方括号设定、逗句比、章节字数、
**单段超长（hard_gate · v23.12 新增）**、**单句独行占比（v23.12）**、**长段计数（v23.12）**

退出码：0 = 全部 PASS, 1 = 有 FAIL, 2 = 致命错误

v23.12（2026-05-21）新增 3 项段长检查（基于网文段长调研 12 来源互证）：
  - 单段超长：> 120 CJK 字单段（每章 ≤1 例外）= hard_gate
  - 单句独行占比：≥ 40%（吐槽爽文档默认目标）
  - 长段计数：80-120 字段每章 ≤ 2
详见 STRUCTURE.md 第 11.2 节 + memory feedback_paragraph_length_hard_constraint
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from style_analyzer import (  # noqa: E402
    analyze_text, AI_STRUCTURAL_BANNED, CRAFT_SIGNATURE_QUOTA_WORDS,
)
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写

# ── 阈值定义 ──────────────────────────────────────────────────
DEFAULT_THRESHOLDS = {
    "dialogue_ratio":        {"min": 0.30, "max": 0.80},
    "para_mean_len":         {"min": 10,   "max": 40},
    "ultra_short_ratio":     {"max": 0.50},
    "single_sent_ratio":     {"max": 0.90},
    "onomatopoeia_count":    {"min": 0},
    "ultra_long_sent_count": {"min": 0},
    "banned_words":          {"max": 0},
    "ai_tags":               {"max": 0},
    "quota_per_word":        {"max": 5},
    "bracket_settings":      {"min": 0},
    "comma_period_ratio":    {"min": 0.5, "max": 6.0},
    # v18 复核：旧 2000-6000 是"混合 txt 含 CHANGES JSON"口径下设的。
    # 接入 chapter_io.read_body() 后只算纯正文，字数变小，宽松兜底区间下调到 1800-5800。
    "chapter_words":         {"min": 1800, "max": 5800},
    # v23.12（2026-05-21）段长硬约束 —— 来自网文段长调研（12 来源互证）
    "para_max_chars":        {"warn": 80, "hard_gate": 120, "exception_per_chapter": 1},
    "single_line_ratio":     {"min": 0.30},  # 默认松：≥30%（strict 拉到 40%）
    "long_para_per_chapter": {"max": 3},     # 默认松：每章 ≤3（strict 拉到 2）
}
STRICT_THRESHOLDS = {
    "dialogue_ratio":        {"min": 0.40, "max": 0.70},
    "para_mean_len":         {"min": 14,   "max": 30},
    "ultra_short_ratio":     {"max": 0.30},
    "single_sent_ratio":     {"max": 0.80},
    "onomatopoeia_count":    {"min": 2, "max": 5},
    "ultra_long_sent_count": {"min": 1},
    "banned_words":          {"max": 0},
    "ai_tags":               {"max": 0},
    "quota_per_word":        {"max": 3},
    "bracket_settings":      {"min": 1},
    "comma_period_ratio":    {"min": 1.5, "max": 4.5},
    # v18 复核：旧 3000-5000 是被 CHANGES JSON 污染的混合数据下设的（实测旧稿
    # ch1-3 混合 txt 比纯正文多 2641-5339 字）。改用 cio.read_body() 拿纯正文后，
    # 旧稿 ch1-3 纯正文 count_words = 3013 / 3805 / 4983（均值 ~3934）。
    # STRICT 区间按纯正文基线重定为 2800-5200（覆盖实测最小 3013 与最大 4983 并留余量）。
    "chapter_words":         {"min": 2800, "max": 5200},
    # v23.12 strict：吐槽爽文档目标
    "para_max_chars":        {"warn": 80, "hard_gate": 120, "exception_per_chapter": 1},
    "single_line_ratio":     {"min": 0.40},  # 严格：单句独行 ≥ 40%
    "long_para_per_chapter": {"max": 2},     # 严格：长段每章 ≤ 2
}

# ============================================================
# v2 cluster 化阈值（2026-05-28）：scanner 升维 cluster 视野的 11 处阈值比例化
# 当 env CLUSTER_MODE=1 时启用。比例化原则：
#   · 字数阈值放大到 cluster 范围（10k-25k）
#   · 比例阈值放宽（voice 混合 / 仪式独白稀释）
#   · 长段计数改用比例（按 cluster 段数 × ratio）
# ============================================================
CLUSTER_THRESHOLDS = {
    "dialogue_ratio":        {"min": 0.15, "max": 0.70},  # 仪式/独白稀释 · 下限 40%→15%
    "para_mean_len":         {"min": 14,   "max": 35},    # cluster 段长容忍 · 上限 30→35
    "ultra_short_ratio":     {"max": 0.40},
    "single_sent_ratio":     {"max": 0.85},
    "onomatopoeia_count":    {"min": 0, "max": 999},      # cluster 视野不强求
    "ultra_long_sent_count": {"min": 0},
    "banned_words":          {"max": 0},                  # 不放宽
    "ai_tags":               {"max": 0},                  # 不放宽
    "quota_per_word":        {"max": 5},
    "bracket_settings":      {"min": 0},                  # 蛊真人 voice 不用方括号
    "comma_period_ratio":    {"min": 0.6, "max": 5.0},    # voice 混合 · 放宽
    "chapter_words":         {"min": 8000, "max": 30000}, # cluster 健康区间
    "para_max_chars":        {"warn": 80, "hard_gate": 120, "exception_per_chapter": 1},
    "single_line_ratio":     {"min": 0.30},               # cluster 多段可容忍
    "long_para_per_chapter": {"max_ratio": 0.02},         # 比例化: ≤2% 段超 80 字（替代绝对数）
}

# ── 工具函数 ──────────────────────────────────────────────────

def _find_lines(text: str, pattern: str) -> list[int]:
    """返回 pattern 出现的行号列表（1-based）。"""
    return [i for i, ln in enumerate(text.split("\n"), 1) if pattern in ln]

def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"

def _rpct(lo: float, hi: float) -> str:
    return f"{lo * 100:.0f}-{hi * 100:.0f}%"

def _rng(lo: float, hi: float, u: str = "") -> str:
    return f"{lo:.4g}-{hi:.4g}{u}"


# ── 风格 JSON 覆盖 ───────────────────────────────────────────

def _stat_mean(v, *, mean_keys: tuple[str, ...] = ("mean",)) -> float | None:
    """从 {"<mean_key>": x} 或裸数值取均值，非数值/缺失返回 None。

    2026-05-30 北极星⑤ [override 键名/单位不符]：不同蒸馏批次对同一统计量用了不同
    的「均值字段名」（段长用 mean / mean_chars / mean_sentences），故均值字段名也要
    tolerant —— 按 mean_keys 顺序找第一个数值字段。"""
    if isinstance(v, dict):
        for mk in mean_keys:
            m = v.get(mk)
            if isinstance(m, (int, float)):
                return float(m)
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _first_stat_mean(q: dict, *keys: str, mean_keys: tuple[str, ...] = ("mean",)) -> float | None:
    """按 keys 顺序在 quantitative 里找第一个有数值均值的统计量。

    2026-05-30 北极星⑤：作者档 override 读 key 必须 tolerant 兼容多命名——同一指标
    在不同蒸馏批次里键名不同（如 chapter_words vs chapter_chars、dialogue_ratio vs
    dialogue_ratio_pct）。键名/单位不符 = override 失效 = 用通用 band 苛求真作者
    （矫枉过正）。统一在此处兼容，让作者档真正第一权威。"""
    for k in keys:
        m = _stat_mean(q.get(k), mean_keys=mean_keys)
        if m is not None:
            return m
    return None


# 对话占比：蒸馏批次键名/单位不一 —— 0-1 ratio 用 dialogue_ratio / dialogue_ratio_mean，
# 百分比(0-100)用 dialogue_ratio_pct。validate_style 内部 dialogue_ratio 单位是 **0-1 ratio**
# (style_analyzer.calc_dialogue_ratio = 对话字数/总字数)，故 _pct 键读出后必须 /100 归一。
_DIALOGUE_RATIO_KEYS = ("dialogue_ratio", "dialogue_ratio_mean")
_DIALOGUE_PCT_KEYS = ("dialogue_ratio_pct", "dialogue_ratio_percent", "dialogue_pct")
# 章字数：纯正文字数同一指标的多命名（cio.count_words 口径）。
_CHAPTER_WORDS_KEYS = ("chapter_words", "chapter_chars", "chapter_char_count")


def _extract_author_dialogue_ratio(q: dict) -> float | None:
    """从作者档 quantitative 推**真实对话占比**（归一到 0-1 ratio，对齐 validate_style 内部单位）。

    2026-05-30 北极星⑤ [override 键名/单位不符]：旧实现只读 q["dialogue_ratio"]（假设 0-1），
    但蛊真人档实际键是 dialogue_ratio_pct（百分比 25.4）→ 键名+单位双不符 → override 失效
    → 对话占比退回通用 band(0.30-0.80) 苛求真作者（蛊真人真实 ~25% 被顶成 FAIL = 矫枉过正）。
    修：① 先读 0-1 ratio 键（dialogue_ratio…）原样用；② 再读 _pct 键并 /100 归一到 ratio。
    两类都缺 → None（不 override，保通用 band）。"""
    # ① 0-1 ratio 键（惊悚乐园：dialogue_ratio.mean = 0.2701）
    r = _first_stat_mean(q, *_DIALOGUE_RATIO_KEYS)
    if r is not None:
        # 容错：若误把百分比写进 ratio 键（值 > 1），按百分比归一（防双重错配）
        return r / 100.0 if r > 1.0 else r
    # ② 百分比键（蛊真人：dialogue_ratio_pct.mean = 25.409）→ /100 转 ratio
    p = _first_stat_mean(q, *_DIALOGUE_PCT_KEYS)
    if p is not None:
        return p / 100.0
    return None


def _extract_author_chapter_words(q: dict) -> float | None:
    """从作者档 quantitative 推**真实章字数均值**（tolerant 兼容 chapter_words / chapter_chars）。

    2026-05-30 北极星⑤ [override 键名/单位不符]：旧实现只读 q["chapter_words"]，但蛊真人档
    实际键是 chapter_chars → 键名不符 → 章字数 override 失效 → 退回通用 band。两者都是
    「纯正文字数」同口径，单位一致，只差命名，故 tolerant 兼容即可（无需单位换算）。"""
    return _first_stat_mean(q, *_CHAPTER_WORDS_KEYS)


def _extract_author_para_mean(q: dict) -> float | None:
    """从作者档 quantitative 推**真实段落均长**（CJK 字/段）。
    绝不用 sentence_length（句长）——句长≠段长（北极星⑤ [B-段长单位错配]）。
    ① paragraph_length_chars.mean 直取（亦兼容 paragraph_length.mean_chars · 惊悚乐园键）；
    ② chapter_chars/chapter_words.mean / paragraph_count.mean 算。
    都缺/为 null → 返回 None（调用方据此不收窄段长 band）。"""
    # ① 真实段长键（蛊真人 paragraph_length_chars / 惊悚乐园 paragraph_length.mean_chars）
    plc = _stat_mean(q.get("paragraph_length_chars"))
    if plc is None:
        # 兼容 {"paragraph_length": {"mean_chars": 52.0}} 命名（mean 字段名也不同）
        plc = _stat_mean(q.get("paragraph_length"), mean_keys=("mean_chars", "mean"))
    if plc is not None and plc > 0:
        return plc
    # ② 章字数÷段数（tolerant 兼容 chapter_chars / chapter_words）
    cc = _extract_author_chapter_words(q)
    pc = _stat_mean(q.get("paragraph_count"))
    if cc is not None and pc is not None and pc > 0:
        return cc / pc
    return None


def _extract_author_long_para_ratio(q: dict, para_mean: float | None) -> float | None:
    """从作者档推**真实长段率上限**（每章 80-120 CJK 字段占比的合理上界 max_ratio）。

    2026-05-30 北极星⑤ [long_para 不受作者档 override]：CLUSTER_THRESHOLDS 里
    long_para_per_chapter.max_ratio 固定 2%，是按**短段吐槽爽文**（段均 ~15-20 字）标的。
    但长段签名作者（蛊真人段均 ~30 字 · 签名密叙段；惊悚乐园段均 ~52 字 · gt50 占 43.8%）
    真实长段率远高于 2% → 被通用 2% 顶成 FAIL（"带作者档反更苛"，违反原则⑤）。段越长 →
    80-120 字段天然越多，这是该作者笔法不是穿帮，应放宽 advisory（绝不放松真超长 hard_gate，
    那是 _chk_para_max 管的 > 120 字非对话段，本函数只动 80-120 字 WARN 带的计数阈值）。

    取数优先级（都缺 → None，调用方保通用 2%）。实证锚点（两书原文逐章统计 80-120 字段率）：
      · 蛊真人 段均 ~30 字 → per-chapter 长段率 mean 2.8% / p90 6.3%（固定 2% 顶掉约半数真章 = FAIL）
      · 惊悚乐园 段均 ~52 字 → per-chapter 长段率 mean 14% / p90 23%
      ① 显式长段分布桶：paragraph_length_distribution.gt80 / .gt50（80-120 字率 ≈ gt50 的一部分，
         保守取 gt50 的一半上界，再封顶 0.30 防极端）；亦兼容 paragraph_length_chars 分布。
         惊悚乐园 gt50=0.4379 → 0.219，覆盖其 p90(23%)，对齐真分布。
      ② 退而用真实段均长推：段均越长 80-120 字段天然越多（实测非线性）。以通用基线段均 ~20 字 / 2%
         为锚，幂律外推 max_ratio ≈ 0.02 × (para_mean/20)^2.5，钳到 [0.02, 0.15]。
         蛊真人 段均 30 → 0.055（覆盖其 p85 长段率，正常章不再误 FAIL）。
    """
    # ① 显式长段分布桶（惊悚乐园：paragraph_length_distribution.gt50=0.4379）
    for dist_key in ("paragraph_length_distribution", "paragraph_length_chars_distribution"):
        dist = q.get(dist_key)
        if isinstance(dist, dict):
            gt80 = dist.get("gt80")
            if isinstance(gt80, (int, float)) and gt80 > 0:
                return min(0.30, max(0.02, float(gt80)))
            gt50 = dist.get("gt50")
            if isinstance(gt50, (int, float)) and gt50 > 0:
                # gt50 含 50-80 / 80-120 / >120 多段，80-120 带保守取一半，封顶 0.30
                return min(0.30, max(0.02, float(gt50) * 0.5))
    # ② 退用真实段均长幂律外推（锚：段均 20 字 ↔ 2% · 指数 2.5 拟合两书实测 p90）
    if para_mean is not None and para_mean > 20:
        return min(0.15, max(0.02, 0.02 * (para_mean / 20.0) ** 2.5))
    return None


# 单段超长 hard_gate 天花板的绝对上限（北极星⑤ · 2026-05-30 R3/R5）：作者档驱动放宽时
# 仍封顶 300 CJK，防真失控叙述段（>300 字纯叙述无论作者签名都属穿帮/info-dump 失控）。
_PARA_MAX_HARD_BASE = 120                   # 通用基线 hard_gate（无长段签名 / 无作者档时守此）
_PARA_MAX_HARD_ABS_CAP = 300                # 作者档放宽的绝对天花板（防真失控）


def _extract_author_max_para_chars(q: dict, para_mean: float | None) -> int | None:
    """从作者档推**单段超长 hard_gate 天花板**（CJK 字）——仅当作者**实证有长段签名**时放宽。

    2026-05-30 北极星⑤ [#2 长段签名作者 120 字 hard_gate 天花板无作者档 override · R3/R5]：
    `_chk_para_max` 的 120 字 hard_gate 是按短段爽文标的，但长段签名作者（惊悚乐园：画外音
    对读者 + 游戏 info-dump · 实证 paragraph_length.mean_chars=52 / gt50=0.4379 / 真作者
    6/6 章非对话段 130-245 字 · 全库非对话段 p95=121/p99=174）被通用 120 硬墙误伤
    （121 字精心长段 = fatal 不可豁免）。作者档第一权威（北极星⑤）：作者实证写长段则该维度
    由作者档说了算，放宽到作者真实长段合理上限。

    判别「长段签名」（任一成立即放宽 · 都基于作者档实证非 genre 猜测）：
      · 显式长段分布桶 gt80 ≥ 0.05 或 gt50 ≥ 0.20（高比例段落 > 50/80 字）；或
      · 真实段均长 para_mean ≥ 40 字（段落本就长的作者）。
    放宽后的 hard_gate 天花板（取最大估计 · 覆盖作者真实长段范围）：
      · 分布有 p95/p99-级长段证据 → mean_chars × 系数（实测 惊悚乐园 mean 52 ×3 = 156，
        覆盖其 130-245 真长段段中位且不至放太松）；
      · 钳到 [_PARA_MAX_HARD_BASE(120), _PARA_MAX_HARD_ABS_CAP(300)]——**绝不破 120 下限**
        （防把短段作者放松）且**绝不破 300 上限**（防真失控叙述段）。

    无作者档 / 作者无长段签名 → 返回 None（调用方保通用 120 · 防 AI 滥用超长段）。守纪律 b/c。
    """
    dist = None
    for dist_key in ("paragraph_length_distribution", "paragraph_length_chars_distribution"):
        d = q.get(dist_key)
        if isinstance(d, dict):
            dist = d
            break
    gt80 = dist.get("gt80") if isinstance(dist, dict) else None
    gt50 = dist.get("gt50") if isinstance(dist, dict) else None
    has_signature = (
        (isinstance(gt80, (int, float)) and gt80 >= 0.05)
        or (isinstance(gt50, (int, float)) and gt50 >= 0.20)
        or (para_mean is not None and para_mean >= 40)
    )
    if not has_signature:
        return None
    # 天花板估计：以真实段均长 × 系数推作者长段合理上限（段均越长 → 长段越长）。
    # 系数 4.0 拟合 惊悚乐园 全 250 章实测：mean 52 → 208，落在其非对话段 p99(174) 之上、真长段
    # 上界(任务实证 130-245)之内。逐章回测（per-chapter >ceiling 非对话段计数 p95=1，配例外 1）：
    #   · 通用 120 天花板：135/250 章 单段超长 FAIL（系统性误伤真作者长段签名）
    #   · 天花板 208：仅 5/250 FAIL，其中 3 章是 >300 字真失控/坏数据（绝对上限正确拦），
    #     2 章是 209-243 字极端双长段（作者最上沿·不强求 100% 过 = 非放开）。
    # 无 para_mean 但有 gt 分布签名 → 退用保守 208（=52×4 锚）。系数仅对长段签名作者生效·
    # 钳到 [120,300]·守纪律 c（绝对上限防真失控·短段作者/无档仍 120 防 AI 滥用）。
    est = (para_mean * 4.0) if (para_mean is not None and para_mean > 0) else 208.0
    ceiling = int(round(est))
    ceiling = max(_PARA_MAX_HARD_BASE, min(_PARA_MAX_HARD_ABS_CAP, ceiling))
    return ceiling


# ── 章字数 override 的 cluster 视野（北极星① · 2026-05-30 #1）─────────────────
# 根因：CLUSTER_MODE 下 validate 跑在**整 cluster draft**上（cluster-write 产 cluster
# draft·splitter 才切章·validate 在 cluster draft 上跑 CLUSTER_MODE），CLUSTER_THRESHOLDS
# 已把章字数 band 设成 cluster 级 8000-30000。但带 --style 时 _apply_style_overrides 用
# 作者**单章**字数 mean(蛊真人 chapter_chars 2719 / 惊悚 chapter_words 2921)±500 覆盖 →
# 塌回单章 band(2218-3218)→ 整 cluster(18846/19754 字)必 FAIL（系统性误伤所有 cluster draft）。
# 修：仅 cluster 视野（base band 下限 ≥ _CLUSTER_WORDS_FLOOR=8000，即来自 CLUSTER_THRESHOLDS）
# 时，把作者单章 mean **按估算每 cluster 章数放大到 cluster 级**——下限 = mean × 每 cluster
# 最少章数(_CLUSTER_MIN_CHAPTERS)，上限 = mean × 最多章数(_CLUSTER_MAX_CHAPTERS)，再与
# 通用健康 cluster band 取并集（绝不窄于 cluster 健康区间）。单章视野(DEFAULT/STRICT·base
# 下限 < 8000)不动——仍用作者单章 mean ±500（向后兼容·零回归）。
_CLUSTER_WORDS_FLOOR = 8000      # 判别 cluster 视野：base band 下限 ≥ 此 = 来自 CLUSTER_THRESHOLDS
_CLUSTER_MIN_CHAPTERS = 3        # 每 cluster 估算最少章数（下限放大系数）
_CLUSTER_MAX_CHAPTERS = 8        # 每 cluster 估算最多章数（上限放大系数）


def _cluster_chapter_words_band(cw_mean: float, base_lo: float, base_hi: float) -> dict:
    """cluster 视野下，把作者**单章**字数 mean 放大成 cluster 级 band，再与通用健康
    cluster band 取并集（北极星① · #1）。

    · 下限 = min(作者单章 mean × 最少章数, 通用 cluster 下限) —— 不高于健康下限，防短 cluster 误 FAIL。
    · 上限 = max(作者单章 mean × 最多章数, 通用 cluster 上限) —— 不低于健康上限，容厚重 cluster。
    实证：蛊真人单章 mean 2719 → [2719×3, 2719×8]=[8157,21752] ∪ [8000,30000]=[8000,30000]
         （18846 字真 cluster 落内·不再 FAIL）；惊悚单章 mean 2921 同理覆盖 19754。
    并集口径保证「带作者档」绝不比「不带作者档（纯 CLUSTER_THRESHOLDS）」更苛（守原则⑤）。
    """
    est_lo = cw_mean * _CLUSTER_MIN_CHAPTERS
    est_hi = cw_mean * _CLUSTER_MAX_CHAPTERS
    return {"min": min(est_lo, base_lo), "max": max(est_hi, base_hi)}


# ── L1a 作者经验分位数 band（北极星① · 2026-05-30 · 根治矫枉过正根因）───────────
# 根因：_apply_style_overrides 用「作者实测 mean ± 固定容差」覆盖 band（段长 mean×0.7-1.3 /
# 对话占比 mean±0.15）。对**长段议论体作者**（蛊真人段长方差大 · 惊悚乐园段均 52 / 段长跨度
# 36-72）固定容差系统性误判：mean±30% 把 p95(72) 顶出 band（68 上界）→ 真作者正常长段章 FAIL。
# 前 6 轮补丁全是 whack-a-mole（B 段长单位 / A 对话段豁免 / C 配额降级 / D 键名兼容 / F split /
# G long_para）——都在补「容差 band 误判」的个案，未触根。根治：band 从作者样本**经验分位数
# [p5,p95]** 涌现（覆盖作者真实 90% 章节区间），取代 mean±容差。
#
# 影子并行（守纪律 2）：env QUANTILE_BAND_MODE 控制——
#   · shadow（默认）：算 quantile band 但**只记录新旧 band 分歧到 stderr · 不改判决**（返回旧
#     mean±容差 band·零回归）。收敛验证后才放量。
#   · active：正式用 [p5,p95] 分位数 band。
#   · off：完全关闭（连 shadow 日志都不打·纯旧行为）。
import os as _os


def _quantile_band_mode() -> str:
    """读 QUANTILE_BAND_MODE（默认 active·2026-05-31 放量·取并集band⊇旧band 绝不更苛）· {shadow,active,off}· 其余按 active。"""
    m = (_os.environ.get("QUANTILE_BAND_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def _extract_quantile_pair(stat: dict | None) -> tuple[float, float] | None:
    """从统计字段取 (p5, p95) 经验分位数对——两者都是数值才返回（否则 None=无分位数 band 数据）。

    L1a：作者档 quantitative 字段（如 paragraph_length_chars）现含 {p5,p25,p50,p75,p95,mean}。
    只要 p5/p95 都在且 p5<=p95 即可用作经验 band。缺任一 → None（调用方退回旧 mean±容差 band）。"""
    if not isinstance(stat, dict):
        return None
    p5 = stat.get("p5")
    p95 = stat.get("p95")
    if isinstance(p5, (int, float)) and isinstance(p95, (int, float)) and p5 <= p95:
        return float(p5), float(p95)
    return None


def _maybe_quantile_band(
    dim_name: str, stat: dict | None, old_band: dict, *, lo_min: float | None = None,
    hi_max: float | None = None,
) -> dict:
    """L1a band 决策（影子并行 · 北极星①）：有 [p5,p95] 分位数数据时算经验 band，按
    QUANTILE_BAND_MODE 决定是否真用——

    · off / 无分位数数据 → 原样返回 old_band（旧 mean±容差·零回归）。
    · shadow（默认）→ 算新 band·把新旧分歧记 stderr·**返回 old_band**（不改判决）。
    · active → 返回分位数 band（[p5,p95]·可选钳 lo_min/hi_max 防越界，如对话占比钳 [0,1]）。

    分位数 band = [p5, p95]（覆盖作者真实 90% 章节区间·绝不窄于单点 mean±容差能覆盖的真分布）。"""
    mode = _quantile_band_mode()
    if mode == "off":
        return old_band
    qp = _extract_quantile_pair(stat)
    if qp is None:
        return old_band  # 无分位数数据：保旧 band（向后兼容老蒸馏档）
    p5, p95 = qp
    new_lo, new_hi = p5, p95
    if lo_min is not None:
        new_lo = max(lo_min, new_lo)
    if hi_max is not None:
        new_hi = min(hi_max, new_hi)
    o_lo, o_hi = old_band.get("min"), old_band.get("max")
    # 分位数 band 与旧 mean±容差 band 取**并集**（绝不窄于旧 band）。
    # 放量验证实证(2026-05-31)：蛊真人(高方差重尾)[p5,p95]=[24.5,38.3] 比 mean±30%=[22.5,41.7]
    # 更窄，直接「替代」会把真作者 4 章顶坏(WARN→FAIL/PASS→WARN)=矫枉过正(违北极星⑤)。
    # 取并集后 band⊇旧band·重尾作者绝不更苛；宽尾作者(惊悚乐园)仍救回长段(上沿 67.6→71.94)。
    union_lo = min(new_lo, o_lo) if o_lo is not None else new_lo
    union_hi = max(new_hi, o_hi) if o_hi is not None else new_hi
    if mode == "shadow":
        # 只记录新旧分歧·不改判决（返回旧 band）。便于收敛分析放量决策。
        try:
            print(
                f"[QUANTILE_BAND shadow] {dim_name}: old(mean±容差)=[{o_lo:.4g},{o_hi:.4g}] "
                f"vs new(p5-p95∪old)=[{union_lo:.4g},{union_hi:.4g}]",
                file=sys.stderr,
            )
        except Exception:
            pass
        return old_band
    # active：分位数 band ∪ 旧 band（取并集·绝不更苛·只可能放宽救回长段作者）
    return {"min": union_lo, "max": union_hi}


def _apply_style_overrides(t: dict, sd: dict, author_dir=None) -> dict:
    t = {k: dict(v) for k, v in t.items()}
    # 2026-05-29 北极星 P4 [H2-style]：标记「本项目有作者风格档」→ _chk_banned 据此把
    # 工艺签名禁用词降 WARN（不硬毙作者签名笔法），AI 结构套话仍 FAIL。
    t["_has_author_profile"] = True
    q = sd.get("quantitative", {})
    # 对话占比 mean -> +/- 0.15（归一为 0-1 ratio，对齐 validate_style 内部单位）。
    # 2026-05-30 北极星⑤：tolerant 读 dialogue_ratio(0-1) | dialogue_ratio_pct(百分比/100)，
    # 键名/单位双兼容 —— 否则蛊真人(pct 键)override 失效，对话占比用通用 band 苛求真作者。
    dr_mean = _extract_author_dialogue_ratio(q)
    if dr_mean is not None:
        _old_dlg_band = {"min": max(0.0, dr_mean - 0.15), "max": min(1.0, dr_mean + 0.15)}
        # L1a 根治（北极星①）：若 dialogue_ratio 桶含 [p5,p95]（0-1 ratio）经验分位数，用经验 band
        # 取代 mean±0.15（影子并行·默认 shadow）。钳 [0,1] 防越界。只认 0-1 ratio 键的分位数
        # （dialogue_ratio_pct 是百分比·单位不一·暂不在分位数路径处理·保 mean±容差 D 修兼容）。
        _dlg_stat = q.get("dialogue_ratio") if _extract_quantile_pair(
            q.get("dialogue_ratio")) else None
        t["dialogue_ratio"] = _maybe_quantile_band(
            "对话占比", _dlg_stat, _old_dlg_band, lo_min=0.0, hi_max=1.0)
    # 2026-05-30 北极星⑤ [B-段长单位错配]：段落均长 band 必须用**真实段长数据**推，
    # 绝不拿句长(sentence_length)冒充段长——句长 ≠ 段长，错配会把段落本就长的作者
    # (如蛊真人段均 ~30 字)从通用 PASS 顶成 FAIL，"带作者档反更苛"违反原则⑤。
    # 取数优先级：① paragraph_length_chars.mean（蒸馏直出的真实段长）
    #             ② chapter_chars.mean / paragraph_count.mean（章字数÷段数 = 真实段均长）
    # 两者都缺 → 段长维度【不做 override】，保持通用 band（14-35，不收窄）。
    para_mean = _extract_author_para_mean(q)
    if para_mean is not None and para_mean > 0:
        _old_para_band = {"min": max(1, para_mean * 0.7), "max": para_mean * 1.3}
        # L1a 根治（北极星①）：有 paragraph_length_chars 的 [p5,p95] 经验分位数时，用经验 band
        # 取代 mean±30%（影子并行·默认 shadow 只记录分歧不改判决·active 才放量）。复用现有
        # _extract_author_para_mean 同源取数链（paragraph_length_chars / paragraph_length），
        # 不新起一套。无分位数（老蒸馏档）→ 退回 mean±容差（零回归）。
        _plc = q.get("paragraph_length_chars")
        if not isinstance(_plc, dict) or _extract_quantile_pair(_plc) is None:
            # 兼容惊悚乐园老命名 paragraph_length（mean_chars）——分位数补在 paragraph_length_chars，
            # 若该键缺分位数则尝试 paragraph_length 桶（蒸馏新批次可能写在此）。
            _plc = q.get("paragraph_length") if _extract_quantile_pair(
                q.get("paragraph_length")) else _plc
        t["para_mean_len"] = _maybe_quantile_band(
            "段落均长", _plc, _old_para_band, lo_min=1.0)
    # 2026-05-30 北极星⑤ [long_para 不受作者档 override]：长段率上限按作者真实长段分布/段均长
    # 放宽——长段签名作者（蛊真人/惊悚乐园）的 80-120 字段率天然高于通用 2%，固定 2% 会把
    # 作者签名笔法误判 FAIL。仅当 long_para_per_chapter 用 max_ratio（cluster 视野）才 override；
    # chapter 视野绝对数 max 不动（短章段少，绝对数本就宽）。有数据才放宽，无数据保通用。
    lp_cfg = t.get("long_para_per_chapter", {})
    if "max_ratio" in lp_cfg:
        lp_ratio = _extract_author_long_para_ratio(q, para_mean)
        if lp_ratio is not None and lp_ratio > lp_cfg["max_ratio"]:
            t["long_para_per_chapter"] = {"max_ratio": lp_ratio}
    # 章字数 mean -> +/- 500。2026-05-30 北极星⑤：tolerant 读 chapter_words | chapter_chars
    # （同口径多命名）—— 否则蛊真人(chapter_chars 键)override 失效，章字数退回通用 band。
    # 2026-05-30 北极星① [#1 章字数 band 无 cluster 视野]：CLUSTER_MODE 下 validate 跑在整
    # cluster draft 上，base band 已是 cluster 级(8000-30000)；此时**绝不能**用作者单章 mean
    # ±500 塌回单章 band(否则 18846 字真 cluster 必 FAIL)。判 base 下限 ≥ _CLUSTER_WORDS_FLOOR
    # → cluster 视野：把作者单章 mean 放大成 cluster band 并与健康区间取并集；否则(单章视野)
    # 仍 ±500（向后兼容）。
    cw_mean = _extract_author_chapter_words(q)
    if cw_mean is not None:
        _cw_base = t.get("chapter_words", {})
        if _cw_base.get("min", 0) >= _CLUSTER_WORDS_FLOOR:
            t["chapter_words"] = _cluster_chapter_words_band(
                cw_mean, _cw_base["min"], _cw_base["max"])
        else:
            t["chapter_words"] = {"min": max(500, cw_mean - 500), "max": cw_mean + 500}
    # must_have_per_chapter
    must = sd.get("must_have_per_chapter", {})
    if "onomatopoeia" in must:
        t["onomatopoeia_count"]["min"] = int(must["onomatopoeia"])
    if "bracket_settings" in must:
        t["bracket_settings"]["min"] = int(must["bracket_settings"])
    # 2026-05-29 北极星 P4 [M1-dont] + 2026-05-30 [#2 R3/R5]：单段超长 hard_gate(默认 120 CJK)
    # 对**长段签名作者**是硬伤——一个 121 字精心长段 = fatal 不可豁免。作者档第一权威（北极星⑤）：
    #   ① 作者档【显式声明】max_para_chars → 直接用（作者最权威的明示意图）；
    #   ② 否则作者档**实证有长段签名**（gt80/gt50 高 或 段均 ≥40，如惊悚乐园 mean 52/gt50 0.44/
    #      真作者非对话段 130-245 字）→ 按真实分布驱动放宽天花板（_extract_author_max_para_chars）。
    # 两路都**钳到 [120, 300]**：绝不破 120 下限（无作者档/短段作者仍守 120 防 AI 滥用超长段）·
    # 绝不破 300 上限（防真失控叙述段）。守纪律 b/c：只对实证长段作者精准放宽·非放开。
    mpc = q.get("max_para_chars") or sd.get("max_para_chars")
    if not (isinstance(mpc, (int, float)) and mpc > 0):
        # 无显式声明 → 看作者档长段签名实证驱动（para_mean 已在上方算出·复用）
        mpc = _extract_author_max_para_chars(q, para_mean)
    base_hard = t.get("para_max_chars", {}).get("hard_gate", _PARA_MAX_HARD_BASE)
    if isinstance(mpc, (int, float)) and mpc > base_hard:
        new_hard = max(_PARA_MAX_HARD_BASE, min(_PARA_MAX_HARD_ABS_CAP, int(mpc)))
        if new_hard > base_hard:
            _exc = t.get("para_max_chars", {}).get("exception_per_chapter", 1)
            t["para_max_chars"] = {
                "warn": max(80, int(new_hard * 0.7)),
                "hard_gate": new_hard,
                "exception_per_chapter": _exc,
            }
    # ── L2-1 PID 反馈微调（北极星⑤ · 2026-05-30）──────────────────────
    # 至此作者档【前馈 override】已全部施加（L1a 分位数 band + 各 mean±容差 + cluster band）。
    # PID 反馈层【最后】叠加：只动 4 个被控连续 advisory 阈值（para_mean_len/dialogue_ratio/
    # long_para_per_chapter/quota_per_word），把真作者 FPR 驱动到 0。前馈优先级 > 反馈
    # （前馈先定 band，PID 只在其基础上做保守微调）。env PID_THRESHOLD_MODE=off（默认）时
    # apply_pid_delta 直接返回原值（零回归·回测验证前不生效）。物理隔离：tuner 白名单只读写 4 键，
    # 15 个 HARD_GATE_CODES 不在本回路。author_dir=None（旧调用方/回测自管 Δ）→ 不动。
    if author_dir is not None:
        try:
            import pid_threshold_tuner as _pid  # noqa: E402
            t = _pid.apply_pid_delta(t, author_dir)
        except Exception as _e:  # 反馈层失败绝不中断校验（顾问非法官）
            print(f"[PID] apply_pid_delta 跳过（{_e}）", file=sys.stderr)
    return t


# ── CheckResult ───────────────────────────────────────────────

class CheckResult:
    __slots__ = ("name", "status", "detail", "target_desc", "fail_lines")
    def __init__(self, name: str, status: str, detail: str,
                 target_desc: str, fail_lines: list[int] | None = None):
        self.name, self.status, self.detail = name, status, detail
        self.target_desc = target_desc
        self.fail_lines = fail_lines or []


def _range_check(name: str, val: float, lo: float | None, hi: float | None,
                 vs: str, ts: str, *, wm: float = 0.0) -> CheckResult:
    """通用范围检查。wm = warn margin，在 FAIL 边界附近降级为 WARN。"""
    if lo is not None and val < lo:
        s = "WARN" if (wm > 0 and val >= lo - wm) else "FAIL"
        return CheckResult(name, s, vs, ts)
    if hi is not None and val > hi:
        s = "WARN" if (wm > 0 and val <= hi + wm) else "FAIL"
        return CheckResult(name, s, vs, ts)
    return CheckResult(name, "PASS", vs, ts)


# ── 12 项检查 ─────────────────────────────────────────────────

def _chk_dialogue(p: dict, t: dict) -> CheckResult:
    v, lo, hi = p["dialogue_ratio"], t["dialogue_ratio"]["min"], t["dialogue_ratio"]["max"]
    return _range_check("对话占比", v, lo, hi, _pct(v), f"目标 {_rpct(lo, hi)}", wm=0.05)

def _chk_para_len(p: dict, t: dict) -> CheckResult:
    v, lo, hi = p["paragraph_stats"]["mean"], t["para_mean_len"]["min"], t["para_mean_len"]["max"]
    return _range_check("段落均长", v, lo, hi, f"{v:.1f} 字", f"目标 {_rng(lo, hi, ' 字')}", wm=3)

def _chk_ultra_short(p: dict, t: dict) -> CheckResult:
    v, hi = p["ultra_short_para_ratio"], t["ultra_short_ratio"]["max"]
    return _range_check("极短段占比", v, None, hi, _pct(v), f"建议 <={_pct(hi)}", wm=0.05)

def _chk_single_sent(p: dict, t: dict) -> CheckResult:
    v, hi = p["single_sentence_para_ratio"], t["single_sent_ratio"]["max"]
    return _range_check("单句成段率", v, None, hi, _pct(v), f"建议 <={_pct(hi)}", wm=0.05)

def _chk_onomatopoeia(p: dict, t: dict) -> CheckResult:
    v, lo = p["onomatopoeia_para_count"], t["onomatopoeia_count"]["min"]
    hi = t["onomatopoeia_count"].get("max")
    if hi is not None and v > hi:
        return CheckResult("拟声格式", "WARN", f"{v} 处 (过多)", f"目标 {lo}-{hi}")
    if v >= lo:
        tgt = f"目标 {lo}-{hi}" if hi else f"目标 >={lo}"
        return CheckResult("拟声格式", "PASS", f"{v} 处", tgt)
    return CheckResult("拟声格式", "WARN" if v > 0 else "FAIL", f"{v} 处", f"目标 >={lo}")

def _chk_long_sent(p: dict, t: dict) -> CheckResult:
    v, lo = p["ultra_long_sentence_count"], t["ultra_long_sent_count"]["min"]
    if v >= lo:
        return CheckResult("极长句(50+字)", "PASS", f"{v} 处", f"目标 >={lo}")
    return CheckResult("极长句(50+字)", "WARN", f"{v} 处", f"目标 >={lo}")

def _word_hit_check(text: str, hits: dict, name: str, target: str,
                    max_lines: int = 5) -> CheckResult:
    """禁用词/AI标签通用检查——有命中即 FAIL，附带行号。"""
    if not hits:
        return CheckResult(name, "PASS", "0 个", target)
    parts, lines = [], []
    for w, c in hits.items():
        ls = _find_lines(text, w)
        lines.extend(ls)
        parts.append(f'"{w}" x{c} (行 {", ".join(str(l) for l in ls[:max_lines])})')
    return CheckResult(name, "FAIL", "发现 " + "; ".join(parts), target, lines)

def _chk_banned(text: str, p: dict, t: dict) -> CheckResult:
    hits = p.get("banned_word_hits", {}) or {}
    # 2026-05-29 北极星 P4 [H2-style]：有作者风格档时分级——AI 结构套话仍 FAIL，
    # 工艺签名词降 WARN（不硬毙作者签名笔法，复刻作者优先于通用反 AI 腔；守原则5）。
    if t.get("_has_author_profile") and hits:
        ai_hits = {w: c for w, c in hits.items() if w in AI_STRUCTURAL_BANNED}
        craft_hits = {w: c for w, c in hits.items() if w not in AI_STRUCTURAL_BANNED}
        if ai_hits:  # AI 结构套话命中 → 仍 FAIL（不可放行）
            return _word_hit_check(text, ai_hits, "禁用词(AI结构套话)", "目标 0 个")
        if craft_hits:  # 仅工艺签名词命中 → WARN（作者可能将其作为签名笔法）
            r = _word_hit_check(text, craft_hits, "工艺签名词(作者档下降级)", "建议 0 个 · 若作者签名笔法可豁免")
            r.status = "WARN"
            return r
        return CheckResult("禁用词", "PASS", "0 个", "目标 0 个")
    return _word_hit_check(text, hits, "禁用词", "目标 0 个")

def _chk_ai_tags(text: str, p: dict, t: dict) -> CheckResult:
    return _word_hit_check(text, p.get("ai_dialogue_tag_hits", {}), "AI对话标签", "目标 0 个")

def _chk_quota(text: str, p: dict, t: dict) -> CheckResult:
    hits, lim = p.get("quota_word_hits", {}), t["quota_per_word"]["max"]
    if not hits:
        return CheckResult("配额词", "PASS", "全部达标", f"各<={lim}")
    # 2026-05-30 北极星⑤ [C-配额词作者档降级]：对齐 _chk_banned 的作者档分级逻辑——
    # 有作者风格档时，配额词里的**工艺签名类**（顿时/微微/似乎/仿佛 = CRAFT_SIGNATURE_QUOTA_WORDS）
    # 可能是该作者的签名笔法，超额降 WARN（advisory 可豁免）而非 FAIL；
    # 非签名类配额词（突然/下一刻/下意识/莫名 = 节奏转场堆砌词）超额仍 FAIL。
    # 无作者档 → 全部超额即 FAIL（旧行为，向后兼容）。
    has_profile = bool(t.get("_has_author_profile"))
    over_hard, over_craft, lines = [], [], []
    for w, c in hits.items():
        if c > lim:
            ls = _find_lines(text, w)
            lines.extend(ls)
            entry = f'"{w}" x{c} (行 {", ".join(str(l) for l in ls[:3])})'
            if has_profile and w in CRAFT_SIGNATURE_QUOTA_WORDS:
                over_craft.append(entry)
            else:
                over_hard.append(entry)
    if not over_hard and not over_craft:
        return CheckResult("配额词", "PASS", f"各词均<={lim}", f"各<={lim}")
    if over_hard:  # 非签名类超额 → FAIL（不可放行）
        detail = "超额: " + "; ".join(over_hard)
        if over_craft:
            detail += "；签名类(作者档降级): " + "; ".join(over_craft)
        return CheckResult("配额词", "FAIL", detail, f"各<={lim}", lines)
    # 仅工艺签名类超额 + 有作者档 → WARN（作者可能将其作为签名笔法，可豁免）
    return CheckResult("配额词(作者档下降级)", "WARN",
                       "签名类超额(可豁免): " + "; ".join(over_craft),
                       f"各<={lim} · 若作者签名笔法可豁免", lines)

def _chk_brackets(p: dict, t: dict) -> CheckResult:
    v, lo = p["bracket_setting_count"], t["bracket_settings"]["min"]
    if v >= lo:
        return CheckResult("方括号设定", "PASS", f"{v} 个", f"目标 >={lo}")
    return CheckResult("方括号设定", "WARN" if lo <= 1 else "FAIL", f"{v} 个", f"目标 >={lo}")

def _chk_comma_ratio(p: dict, t: dict) -> CheckResult:
    v = p.get("punctuation_density_per_1000", {}).get("comma_period_ratio", 0)
    lo, hi = t["comma_period_ratio"]["min"], t["comma_period_ratio"]["max"]
    return _range_check("逗句比", v, lo, hi, f"{v:.2f}", f"目标 {_rng(lo, hi)}", wm=0.3)

def _chk_words(text: str, p: dict, t: dict) -> CheckResult:
    # v18：字数口径统一走 cio.count_words()（非空白字符数，含标点）——
    # 与 save-state / git commit / build_manifest 全系统一致。
    # 旧实现用 analyze_text 的 total_chinese_chars（纯汉字），口径不一致已弃用。
    v, lo, hi = cio.count_words(text), t["chapter_words"]["min"], t["chapter_words"]["max"]
    return _range_check("章节字数", v, lo, hi, str(v), f"目标 {int(lo)}-{int(hi)}", wm=200)


# ── v23.12 段长硬约束（基于 2026-05-21 网文段长调研 12 来源互证）──

_CJK_RE = re.compile(r"[一-鿿]")

# 中文弯引号 / 方头引号（codepoint 判，区分左右 U+201C≠U+201D）
_Q_OPEN_CP = ("“", "「", "『")   # “ 「 『
_Q_CLOSE_CP = ("”", "」", "』")  # ” 」 』

# 2026-05-30 北极星⑤ [A-说话人前缀对话段豁免漏检 · R2/R4]：中文网文**最高频**对话形式不是
# 纯引号段，而是「说话人/动作前缀 + 提示语（：/，X道：）+ 引号包裹主体」——实证两书原文
# 含冒号引语段 1143 处，提示语字符 715/1143 是 U+FF1A「：」。这类段首字是 CJK（说话人名）
# 而非引号，旧 _is_full_dialogue_para 只认「段首=开引号」→ 漏检 → 被当非对话超长段触发
# STYLE_单段超长 hard_gate FAIL（误伤真作者对话段，如蛊真人 ch444 段45『葛光便答：“…”』）。
# 提示语收尾标记：全/半角冒号是最强判别符（叙述句几乎不会以「：+开引号」起头除非引入言语）。
_DIALOGUE_CUE_COLONS = ("：", ":")          # 提示语→引语 收尾冒号（全角 U+FF1A / 半角）
# 说话人前缀允许的最大 CJK 长度（实证：5 处真失误段前缀 4-14 字）。收紧到 20 以排除
# 「30-100 字叙述块 + 短引语」这类应仍受门禁的真长叙述段（守纪律 a：只认真对话不误豁免叙述）。
_MAX_SPEAKER_PREFIX_CJK = 20
_SENT_END_CHARS = "。！？…"                  # 句末终止符（真提示语是单条引入·不含完整句）


# 2026-05-30 北极星① [#2 混合格式欠切]：\n\n 切后某段仍是**混合格式巨段**（内含多个单 \n
# 的章内分段被 \n\n 切漏）时，对该段再用单 \n 细切的判别阈值。两条件**同时**成立才细切：
#   · 段 CJK 字数 > _MIXED_SEG_MIN_CJK（巨段·绝非单条正常段）
#   · 段内含 ≥ _MIXED_SEG_MIN_NL 个单 \n（章内本就用单 \n 分段·被 \n\n 切漏）
# 实证锚（120 章扫描）：纯 \n\n 作者（蛊真人）最大 \n\n-段仅 84 字 / 0 个 >200字且≥2内\n 段
# → 永不触发细切（零回归）；惊悚多章 \n\n join 的合并段 2642-3580 字 / 40-66 内 \n → 触发细切。
# 阈值 200/2 把「单条正常长段（含 1 个软换行如诗行）」与「整章被 \n\n 切漏的混合巨段」分开。
_MIXED_SEG_MIN_CJK = 200
_MIXED_SEG_MIN_NL = 2


def _split_paras(text: str) -> list[str]:
    """tolerant 段落切分（与 style_analyzer.split_paragraphs 对齐）。

    2026-05-30 北极星⑤ [段落 split bug]：旧实现一律 `text.split("\\n\\n")` 切段，但
    很多真作者原文（如惊悚乐园第025章）**整章无双换行 \\n\\n**——只用单 \\n 分段 +
    U+3000 缩进。此时 `\\n\\n` 切出 0 个分隔 → 整章被当成 1 个巨段 → 段落均长/单句
    独行占比/单段超长/长段计数全部错算（单句独行误成 0% FAIL，单段超长既可能误触发
    也可能误掩盖 hard_gate）。而 analyze_text 走的 style_analyzer.split_paragraphs
    一律按单 \\n 切——同一脚本两套段定义，自相矛盾。

    修：**有 \\n\\n 用 \\n\\n 切（保持双换行格式作者不变，如蛊真人 56 个 \\n\\n → 57 段）；
    无 \\n\\n 退回单 \\n 切（与 style_analyzer 对齐，惊悚乐园 → 59 段）**。
    去空段 + 去无 CJK 段（与 style_analyzer.split_paragraphs 一致：count_chinese>0）。

    2026-05-30 北极星① [#2 混合格式欠切]：cluster draft 常由**多章合并**或 writer 混合
    输出而成——**章间 \\n\\n + 章内单 \\n**（如惊悚多章 \\n\\n join：每章内部只用单 \\n
    分段，章与章之间 \\n\\n）。此时纯按 \\n\\n 切 → **每章塌成 1 个巨段**（实测 6 章合并
    切出 6 段 2642-3580 字）→ 全超 300 绝对上限 → 误触发 STYLE_单段超长 hard_gate。
    修：\\n\\n 切后，对**仍是混合格式巨段**（CJK > _MIXED_SEG_MIN_CJK 且含 ≥
    _MIXED_SEG_MIN_NL 个单 \\n）的段**再用单 \\n 细切**——把章内被 \\n\\n 切漏的单 \\n
    分段正确拆开。守纪律：纯 \\n\\n（蛊真人·\\n\\n-段最大 84 字·从不触发细切）不变 ·
    纯单 \\n（惊悚单章·无 \\n\\n 走 fallback）不变 · 「单条正常长段含 1 个软换行（如
    上联\\n下联）」< 200 字 / 仅 1 个 \\n → 不被细切（不破坏 \\n\\n 作者既有正确分段）。
    """
    if "\n\n" in text:
        out: list[str] = []
        for seg in text.split("\n\n"):
            seg = seg.strip()
            if not seg or not _CJK_RE.search(seg):
                continue
            # 混合格式巨段（章内单 \n 被 \n\n 切漏）→ 再用单 \n 细切；否则原样保留
            if (len(_CJK_RE.findall(seg)) > _MIXED_SEG_MIN_CJK
                    and seg.count("\n") >= _MIXED_SEG_MIN_NL):
                out.extend(sub.strip() for sub in seg.split("\n")
                           if sub.strip() and _CJK_RE.search(sub))
            else:
                out.append(seg)
        return out
    return [p.strip() for p in text.split("\n")
            if p.strip() and _CJK_RE.search(p)]


def _para_cjk_lens(text: str) -> list[int]:
    """每段 CJK 字符数（tolerant 段切分 · 见 _split_paras）。"""
    return [len(_CJK_RE.findall(p)) for p in _split_paras(text)]


def _is_full_dialogue_para(para: str) -> bool:
    """整段是否为**完整对话段**——对话不可中切是叙事常态（北极星⑤ [A-对话段超长豁免]），
    此类超长段豁免 STYLE_单段超长 hard_gate（降 advisory）。识别两种形式：

      ① 纯引号段：整段被中文弯引号(U+201C…U+201D)或方头引号(「…」/『…』)成对包裹。
      ② **说话人前缀对话段**（中文网文最高频形式 · 2026-05-30 R2/R4 补漏）：
         「可选短说话人/动作前缀 + 提示语（以全/半角冒号收尾）+ 引号包裹的言语主体，
         且段尾正是闭引号」。如『葛光便答：“……”』『墨瑶意志大笑一阵，语气又缓和道：“……”』。

    用 codepoint 严格判左右引号成对，段内换行不影响。守纪律 a「只认真对话不误豁免叙述」：
    前缀必须**短**（≤_MAX_SPEAKER_PREFIX_CJK CJK）且**不含句末终止符**（真提示语是单条引入·
    非多句叙述块）·言语主体必须被引号成对包裹且段尾收于闭引号——避免把「长叙述块 + 短引语」
    误判成对话豁免（那类仍是应受门禁的真长叙述段）。"""
    s = para.strip()
    if len(s) < 2:
        return False
    # ① 纯引号段：段首=开引号 且 段尾=对应闭引号（codepoint 成对）
    for o, c in zip(_Q_OPEN_CP, _Q_CLOSE_CP):
        if s[0] == o and s[-1] == c:
            return True
    # ② 说话人前缀对话段：段尾必须是闭引号（言语主体收于段尾）
    if s[-1] not in _Q_CLOSE_CP:
        return False
    # 定位言语主体的开引号（首个开引号即提示语之后的引语起点）
    open_idx = -1
    for i, ch in enumerate(s):
        if ch in _Q_OPEN_CP:
            open_idx = i
            break
    if open_idx <= 0:                       # 无开引号 或 段首即开引号（①已处理）
        return False
    prefix = s[:open_idx].rstrip()          # 容半角冒号与引号间空格（如 `小明说: "…"`）
    if not prefix:
        return False
    # 前缀必须以提示语冒号收尾（最强判别符：叙述句几乎不会以「：+开引号」引语）
    if prefix[-1] not in _DIALOGUE_CUE_COLONS:
        return False
    # 前缀须短（说话人/动作引入·非整段叙述）且不含句末终止符（真提示语是单条引入）
    if any(ec in prefix for ec in _SENT_END_CHARS):
        return False
    if len(_CJK_RE.findall(prefix)) > _MAX_SPEAKER_PREFIX_CJK:
        return False
    # 言语主体须被引号成对包裹（开引号后到段尾闭引号之间是引语）——open_idx 处开引号
    # 必须与段尾闭引号是同一对弯/方头引号（codepoint 成对）。
    body_open = s[open_idx]
    body_close = s[-1]
    for o, c in zip(_Q_OPEN_CP, _Q_CLOSE_CP):
        if body_open == o and body_close == c:
            return True
    return False


def _chk_para_max(text: str, p: dict, t: dict) -> CheckResult:
    """单段超长 hard_gate：单段 > 120 CJK 字（每章 ≤1 例外）。
    超 hard_gate 阈值 = FAIL（audit_hub 标 STYLE_单段超长 = HARD_GATE）。
    超 warn 阈值 = WARN。

    2026-05-30 北极星⑤ [A-对话段超长豁免]：**完整对话段**（整段被中文弯/方头引号成对
    包裹）超 hard_gate 仅降 WARN（advisory 可豁免）——对话不可中切是叙事常态，唯一
    hard_gate 触发在对话段是矫枉过正。**非对话**超长段仍按原逻辑 FAIL（绝不放过真超长）。"""
    cfg = t.get("para_max_chars", {"warn": 80, "hard_gate": 120, "exception_per_chapter": 1})
    warn_th, hard_th, ex = cfg["warn"], cfg["hard_gate"], cfg.get("exception_per_chapter", 1)
    para_text_list = _split_paras(text)  # tolerant 切段（无 \n\n 退单 \n · 见 _split_paras）
    lens = [len(_CJK_RE.findall(p_)) for p_ in para_text_list]
    if not lens:
        return CheckResult("单段超长", "PASS", "无段落", f"目标 ≤{warn_th} (hard_gate {hard_th})")
    # 超 hard_gate 段拆两类：完整对话段（豁免）vs 非对话段（仍 hard_gate）
    over_hard = [(i + 1, n) for i, n in enumerate(lens) if n > hard_th]
    over_hard_nondialog = [
        (i, n) for (i, n) in over_hard
        if not _is_full_dialogue_para(para_text_list[i - 1])
    ]
    over_hard_dialog = [
        (i, n) for (i, n) in over_hard
        if _is_full_dialogue_para(para_text_list[i - 1])
    ]
    over_warn = [(i + 1, n) for i, n in enumerate(lens) if warn_th < n <= hard_th]
    # 找超长段在 text 中行号（按段索引近似 = 段首行号）
    fail_lines = []
    for idx, n in over_hard_nondialog[:5]:
        # 找该段首行
        if idx <= len(para_text_list):
            first_line = para_text_list[idx - 1].split("\n")[0][:20]
            ls = _find_lines(text, first_line)
            if ls:
                fail_lines.extend(ls[:1])
    # 2026-05-30 北极星⑤ [#2 绝对上限·守纪律 c]：非对话段超 **绝对上限**（_PARA_MAX_HARD_ABS_CAP
    # =300 CJK）= 真失控叙述段（info-dump 失控/穿帮），**无论作者档放宽到多少、无论例外名额**
    # 都 FAIL——绝对上限是防真失控的最后硬墙，单条 500 字裸叙述段不被「每章 ≤1 例外」放过。
    runaway = [(i, n) for (i, n) in over_hard_nondialog if n > _PARA_MAX_HARD_ABS_CAP]
    if runaway:
        detail = f"{len(runaway)} 段非对话 > 绝对上限 {_PARA_MAX_HARD_ABS_CAP} 字（真失控叙述段·不可豁免）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in runaway[:3])
        if over_hard_dialog:
            detail += f"；另 {len(over_hard_dialog)} 段完整对话超长(已豁免)"
        return CheckResult("单段超长", "FAIL", detail,
                           f"目标 ≤{warn_th} (hard_gate {hard_th}, 绝对上限 {_PARA_MAX_HARD_ABS_CAP})",
                           fail_lines)
    # 非对话超长段：例外条款（≤ ex 个 → WARN，> ex → FAIL hard_gate）
    if len(over_hard_nondialog) > ex:
        detail = f"{len(over_hard_nondialog)} 段非对话 > {hard_th} 字（超例外 {ex}）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_hard_nondialog[:3])
        if over_hard_dialog:
            detail += f"；另 {len(over_hard_dialog)} 段完整对话超长(已豁免)"
        return CheckResult("单段超长", "FAIL", detail,
                           f"目标 ≤{warn_th} (hard_gate {hard_th}, 例外 ≤{ex})",
                           fail_lines)
    # 完整对话段超长：豁免 hard_gate → WARN（advisory 可豁免）。
    if over_hard_dialog and not over_hard_nondialog:
        detail = f"{len(over_hard_dialog)} 段完整对话 > {hard_th} 字（对话不可中切·已豁免 hard_gate）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_hard_dialog[:3])
        return CheckResult("单段超长", "WARN", detail,
                           f"目标 ≤{warn_th} (对话段豁免 hard_gate {hard_th})")
    if over_hard_nondialog:
        detail = f"{len(over_hard_nondialog)} 段非对话 > {hard_th} 字（在例外内）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_hard_nondialog[:3])
        if over_hard_dialog:
            detail += f"；另 {len(over_hard_dialog)} 段完整对话超长(已豁免)"
        return CheckResult("单段超长", "WARN", detail,
                           f"目标 ≤{warn_th} (hard_gate {hard_th}, 例外 ≤{ex})",
                           fail_lines)
    if over_warn:
        detail = f"{len(over_warn)} 段 {warn_th}-{hard_th} 字（建议切）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_warn[:3])
        return CheckResult("单段超长", "WARN", detail,
                           f"目标 ≤{warn_th} (hard_gate {hard_th})")
    return CheckResult("单段超长", "PASS",
                       f"最长段 {max(lens)} 字",
                       f"目标 ≤{warn_th} (hard_gate {hard_th})")


def _chk_single_line_ratio(text: str, p: dict, t: dict) -> CheckResult:
    """单句独行占比 ≥ target（吐槽爽文档目标 ≥40%）。
    单句独行 = 段内只有 1 个句号/问号/感叹号（不含逗号、分号）。"""
    cfg = t.get("single_line_ratio", {"min": 0.30})
    target = cfg["min"]
    paras = _split_paras(text)  # tolerant 切段（无 \n\n 退单 \n · 见 _split_paras）
    if not paras:
        return CheckResult("单句独行占比", "PASS", "无段落", f"目标 ≥{_pct(target)}")
    # 单句独行 = 段内句子终止符（。！？）总数 ≤ 1 且段长 ≤ 30 CJK
    single = 0
    for p_ in paras:
        end_count = sum(p_.count(c) for c in "。！？")
        cjk_len = len(_CJK_RE.findall(p_))
        # 对话段 (含「」或""引号且短) 也算「独行」
        is_dialog_short = ('"' in p_ or '“' in p_ or '”' in p_ or '「' in p_) and cjk_len <= 50  # 2026-05-30 补弯引号
        if end_count <= 1 and (cjk_len <= 30 or is_dialog_short):
            single += 1
    ratio = single / len(paras)
    vs = f"{_pct(ratio)} ({single}/{len(paras)})"
    ts = f"目标 ≥{_pct(target)}"
    if ratio >= target:
        return CheckResult("单句独行占比", "PASS", vs, ts)
    # 低于目标 5% 内为 WARN，更低 FAIL
    if ratio >= target - 0.10:
        return CheckResult("单句独行占比", "WARN", vs, ts)
    return CheckResult("单句独行占比", "FAIL", vs, ts)


def _chk_long_para_count(text: str, p: dict, t: dict) -> CheckResult:
    """长段计数：80-120 字段每章 ≤ target。
    v2 cluster 化（2026-05-28）：cluster 视野改比例（max_ratio = 0.02 段超 80 字），
    绝对数仅 chapter 视野用。"""
    cfg = t.get("long_para_per_chapter", {"max": 3})
    pm = t.get("para_max_chars", {"warn": 80, "hard_gate": 120})
    warn_th, hard_th = pm["warn"], pm["hard_gate"]
    lens = _para_cjk_lens(text)
    long_count = sum(1 for n in lens if warn_th < n <= hard_th)
    total_paras = len(lens) or 1

    # v2 cluster 化：max_ratio 优先（cluster 视野）/ max 绝对数（chapter 视野）
    if "max_ratio" in cfg:
        max_ratio = cfg["max_ratio"]
        max_count = int(total_paras * max_ratio)
        vs = f"{long_count}/{total_paras} 段 {warn_th}-{hard_th} 字（{long_count/total_paras*100:.1f}%）"
        ts = f"目标 ≤{max_ratio*100:.0f}%（约 {max_count} 段）"
        warn_slack = int(total_paras * max_ratio * 1.5)  # 1.5x ratio 为 WARN
    else:
        max_count = cfg.get("max", 3)
        vs = f"{long_count} 段 {warn_th}-{hard_th} 字"
        ts = f"目标 ≤{max_count} 段"
        warn_slack = max_count + 2

    if long_count <= max_count:
        return CheckResult("长段计数", "PASS", vs, ts)
    if long_count <= warn_slack:
        return CheckResult("长段计数", "WARN", vs, ts)
    return CheckResult("长段计数", "FAIL", vs, ts)


# ── D9 滤镜词密度（deep POV · 2026-05-31 · 全 advisory · 非 hard_gate）──────────
# 「滤镜词」(filter words)：把读者与场景之间隔一层主角感知动词（想/觉得/感到/看到/听到…）。
# 删掉滤镜词、直写被感知之物 = deep POV（更贴近网文「沉浸式爽感」笔法）。本维度**纯顾问**：
#   · 命中率超阈值 → advisory「可删滤镜词贴近 deep POV」（写作 agent 可豁免）；
#   · **绝不 hard_gate**（不进 audit_hub.HARD_GATE_CODES）· 不参与 FAIL 退出码；
#   · env D9_FILTER_WORDS_MODE 控制：
#       - shadow（默认）：算密度但**只 print 到 stderr · 不产出 CheckResult**（零回归·默认行为不变）；
#       - advisory：产出 PASS/WARN CheckResult（WARN 即「可删滤镜词」提示·永不 FAIL）；
#       - off：完全关闭（连 shadow 日志都不打）。
#
# 阈值校准（北极星④顾问非法官 + 纪律 4 真原文不误判）——实证两书全 936 章逐章滤镜词命中率
# （per 1000 CJK · 同 style_analyzer per_1000 denom 口径）：
#   · 蛊真人 686 章：mean 4.26 / median 4.01 / p90 6.98 / p95 8.08 / max 12.74
#   · 惊悚乐园 250 章：mean 5.16 / median 5.16 / p90 7.78 / p95 8.70 / max 10.90
# 真作者上沿 ~13/1000 → 阈值取 14.0（safely 高于两书 max·正常真作者章绝不触发 advisory），
# 只有显著滤镜词堆砌（典型 AI「他看到…他感到…他意识到…」流水账）才超阈值提示。这是
# advisory 软提示不是判决：宁可漏报真问题也绝不误伤真作者签名笔法（守原则⑤）。
_D9_FILTER_WORDS = ("想", "觉得", "感到", "意识到", "看到", "听到", "明白", "知道", "发现", "察觉")
# 阈值高于两书原文 max(12.74) · 留余量到 14.0（真作者正常章命中率 ≤ ~13 绝不误判）
_D9_DENSITY_ADVISORY_PER_1000 = 14.0


def _d9_filter_words_mode() -> str:
    """读 D9_FILTER_WORDS_MODE（默认 shadow）· 仅 {shadow, advisory, off} 合法 · 其余按 shadow。"""
    m = (_os.environ.get("D9_FILTER_WORDS_MODE") or "shadow").strip().lower()
    return m if m in ("shadow", "advisory", "off") else "shadow"


def _filter_word_density(text: str) -> tuple[float, int, dict[str, int]]:
    """滤镜词命中率（每 1000 CJK 字命中数 · 同 style_analyzer per_1000 口径）。

    返回 (density_per_1000, total_cjk, {滤镜词: 命中数})。CJK=0 → (0.0, 0, {})。
    命中数用子串 count（与 _word_hit_check / quota 同口径·tolerant 不分词·零依赖）。"""
    total_cjk = len(_CJK_RE.findall(text))
    hits: dict[str, int] = {}
    for w in _D9_FILTER_WORDS:
        c = text.count(w)
        if c:
            hits[w] = c
    total_hits = sum(hits.values())
    density = (total_hits / total_cjk * 1000.0) if total_cjk > 0 else 0.0
    return density, total_cjk, hits


def _chk_filter_words_d9(text: str, p: dict, t: dict) -> CheckResult | None:
    """D9 滤镜词密度 advisory（deep POV · 全 advisory 非 hard_gate）。

    · off → None（不产出检查项）。
    · shadow（默认）→ 只 print stderr·返回 None（不产出检查项·默认行为零回归）。
    · advisory → 产出 CheckResult：超阈值 WARN「可删滤镜词贴近 deep POV」·否则 PASS。
      **永不 FAIL**（顾问非法官·绝不进 HARD_GATE_CODES·不影响退出码）。"""
    mode = _d9_filter_words_mode()
    if mode == "off":
        return None
    density, total_cjk, hits = _filter_word_density(text)
    th = _D9_DENSITY_ADVISORY_PER_1000
    top = ", ".join(
        f'"{w}"x{c}' for w, c in sorted(hits.items(), key=lambda kv: -kv[1])[:4]
    ) or "无"
    vs_str = f"{density:.2f}/千字 ({sum(hits.values())}/{total_cjk} · {top})"
    ts_str = f"建议 ≤{th:.0f}/千字 · 超则可删滤镜词贴近 deep POV"
    if mode == "shadow":
        # 只记录·不改判决（不产出 CheckResult·默认行为不变）
        try:
            verdict = "OVER" if density > th else "ok"
            print(
                f"[D9_FILTER_WORDS shadow] 滤镜词密度={density:.2f}/千字 阈值={th:.0f} "
                f"[{verdict}] top={top}",
                file=sys.stderr,
            )
        except Exception:
            pass
        return None
    # advisory：产出 PASS / WARN（永不 FAIL）
    if density > th:
        return CheckResult(
            "滤镜词密度(deep POV)", "WARN",
            f"{vs_str} · 滤镜词偏多，可删想/觉得/看到等贴近 deep POV（advisory 可豁免）",
            ts_str,
        )
    return CheckResult("滤镜词密度(deep POV)", "PASS", vs_str, ts_str)


# ── 主校验 & 报告 ────────────────────────────────────────────

def validate_style(text: str, thresholds: dict) -> list[CheckResult]:
    """对文本执行 15 项风格校验，返回 CheckResult 列表。
    v23.12 新增 3 项段长检查（单段超长 hard_gate / 单句独行 / 长段计数）。
    2026-05-31 新增 D9 滤镜词密度 advisory（默认 shadow → 不产出检查项·零回归；
    D9_FILTER_WORDS_MODE=advisory 才产出 PASS/WARN·永不 FAIL·非 hard_gate）。"""
    p = analyze_text(text)
    results = [
        _chk_dialogue(p, thresholds),    _chk_para_len(p, thresholds),
        _chk_ultra_short(p, thresholds), _chk_single_sent(p, thresholds),
        _chk_onomatopoeia(p, thresholds),_chk_long_sent(p, thresholds),
        _chk_banned(text, p, thresholds),_chk_ai_tags(text, p, thresholds),
        _chk_quota(text, p, thresholds), _chk_brackets(p, thresholds),
        _chk_comma_ratio(p, thresholds), _chk_words(text, p, thresholds),
        # v23.12 段长 3 项
        _chk_para_max(text, p, thresholds),
        _chk_single_line_ratio(text, p, thresholds),
        _chk_long_para_count(text, p, thresholds),
    ]
    # D9 滤镜词密度 advisory（shadow/off 默认返回 None → 不进 results · 默认行为零回归）
    _d9 = _chk_filter_words_d9(text, p, thresholds)
    if _d9 is not None:
        results.append(_d9)
    return results


def format_report(results: list[CheckResult]) -> str:
    """格式化为与 validate_chapter.py 兼容的结构化输出。"""
    lines = [f"[{r.status}] {r.name}: {r.detail} ({r.target_desc})" for r in results]
    pc = sum(1 for r in results if r.status == "PASS")
    wc = sum(1 for r in results if r.status == "WARN")
    fc = sum(1 for r in results if r.status == "FAIL")
    lines.append("")
    lines.append(f"风格合规: {pc}/{len(results)} 通过, {wc} 警告, {fc} 失败")
    lines.append(f"退出码: {1 if fc else 0} ({'有 FAIL' if fc else '全部通过'})")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python validate_style.py <章节txt路径> "
              "[--style <风格JSON路径>] [--strict]", file=sys.stderr)
        sys.exit(2)

    chapter_path = Path(sys.argv[1])
    style_path: Path | None = None
    strict = False
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--style" and i + 1 < len(sys.argv):
            style_path = Path(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--strict":
            strict = True; i += 1
        else:
            i += 1

    if not chapter_path.exists():
        print(f"[FATAL] 文件不存在: {chapter_path}", file=sys.stderr); sys.exit(2)
    # v18：统一走 chapter_io 取纯正文。能从路径解析出 项目根+章节号 时用
    # cio.read_body()（v18 已分离的直接读 txt；旧混合 txt 自动剥离 CHANGES）；
    # 解析不出时退回"读原文 + 同口径剥离 CHANGES 段"，杜绝各脚本各自 split。
    text = None
    _m = re.search(r"第(\d+)章", chapter_path.name)
    if _m:
        _ch = int(_m.group(1))
        # 章节/第NNN章/第NNN章.txt → 项目根 = 上溯两级；平铺 第NNN章.txt → 上溯一级
        _proj = (chapter_path.parent.parent.parent
                 if chapter_path.parent.parent.name == "章节"
                 else chapter_path.parent)
        try:
            text = cio.read_body(_proj, _ch)
        except Exception:
            text = None
    if text is None:
        try:
            _raw = chapter_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[FATAL] 无法读取文件: {e}", file=sys.stderr); sys.exit(2)
        for _sep in cio.CHANGES_SEPARATORS:
            if _sep in _raw:
                _raw = _raw.split(_sep)[0].rstrip()
                break
        text = _raw
    if not text.strip():
        print("[FATAL] 文件内容为空", file=sys.stderr); sys.exit(2)

    # v2 cluster 化（2026-05-28）：CLUSTER_MODE env=1 时用 CLUSTER_THRESHOLDS（比例化）
    import os as _os
    _cluster_mode = _os.environ.get("CLUSTER_MODE") == "1"
    if _cluster_mode:
        _base = CLUSTER_THRESHOLDS
        print("[validate_style] CLUSTER_MODE=1 · 用 cluster 视野阈值", file=sys.stderr)
    else:
        _base = STRICT_THRESHOLDS if strict else DEFAULT_THRESHOLDS
    thresholds = {k: dict(v) for k, v in _base.items()}

    if style_path is not None:
        if not style_path.exists():
            print(f"[FATAL] 风格文件不存在: {style_path}", file=sys.stderr); sys.exit(2)
        try:
            sd = json.loads(style_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[FATAL] 风格 JSON 解析失败: {e}", file=sys.stderr); sys.exit(2)
        # L2-1：作者目录 = 作者风格.json 所在目录（workspace/styles/{作者}/）·per-作者 PID state 锚此。
        _author_dir = style_path.resolve().parent
        thresholds = _apply_style_overrides(thresholds, sd, author_dir=_author_dir)

    results = validate_style(text, thresholds)
    print(format_report(results))
    sys.exit(1 if any(r.status == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    main()
