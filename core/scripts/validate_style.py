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

from style_analyzer import analyze_text, AI_STRUCTURAL_BANNED  # noqa: E402
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

def _apply_style_overrides(t: dict, sd: dict) -> dict:
    t = {k: dict(v) for k, v in t.items()}
    # 2026-05-29 北极星 P4 [H2-style]：标记「本项目有作者风格档」→ _chk_banned 据此把
    # 工艺签名禁用词降 WARN（不硬毙作者签名笔法），AI 结构套话仍 FAIL。
    t["_has_author_profile"] = True
    q = sd.get("quantitative", {})
    # dialogue_ratio.mean -> +/- 15%
    dr = q.get("dialogue_ratio", {})
    m = dr.get("mean") if isinstance(dr, dict) else (dr if isinstance(dr, (int, float)) else None)
    if m is not None:
        t["dialogue_ratio"] = {"min": max(0.0, m - 0.15), "max": min(1.0, m + 0.15)}
    # sentence_length.mean -> para_mean_len +/- 30%
    sl = q.get("sentence_length", {})
    if isinstance(sl, dict) and "mean" in sl:
        t["para_mean_len"] = {"min": max(1, sl["mean"] * 0.7), "max": sl["mean"] * 1.3}
    # chapter_words.mean -> +/- 500
    cw = q.get("chapter_words", {})
    m = cw.get("mean") if isinstance(cw, dict) else (cw if isinstance(cw, (int, float)) else None)
    if m is not None:
        t["chapter_words"] = {"min": max(500, m - 500), "max": m + 500}
    # must_have_per_chapter
    must = sd.get("must_have_per_chapter", {})
    if "onomatopoeia" in must:
        t["onomatopoeia_count"]["min"] = int(must["onomatopoeia"])
    if "bracket_settings" in must:
        t["bracket_settings"]["min"] = int(must["bracket_settings"])
    # 2026-05-29 北极星 P4 [M1-dont]：段长 hard_gate(默认 120 CJK)对长句作者(严肃/古风/意识流)
    # 是硬伤——一个 121 字精心长段 = fatal 不可豁免。仅当作者风格档【显式声明】max_para_chars
    # 才放宽（不按 genre 标签自动猜，守原则5「不干涉模型判断」+「没调查没发言权」）。未声明 → 保持默认。
    mpc = q.get("max_para_chars") or sd.get("max_para_chars")
    if isinstance(mpc, (int, float)) and mpc > t.get("para_max_chars", {}).get("hard_gate", 120):
        _exc = t.get("para_max_chars", {}).get("exception_per_chapter", 1)
        t["para_max_chars"] = {"warn": max(80, int(mpc * 0.7)), "hard_gate": int(mpc), "exception_per_chapter": _exc}
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
    over, lines = [], []
    for w, c in hits.items():
        if c > lim:
            ls = _find_lines(text, w)
            lines.extend(ls)
            over.append(f'"{w}" x{c} (行 {", ".join(str(l) for l in ls[:3])})')
    if not over:
        return CheckResult("配额词", "PASS", f"各词均<={lim}", f"各<={lim}")
    return CheckResult("配额词", "FAIL", "超额: " + "; ".join(over), f"各<={lim}", lines)

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

def _para_cjk_lens(text: str) -> list[int]:
    """每段 CJK 字符数。段分隔 = 双换行。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [len(_CJK_RE.findall(p)) for p in paras]


def _chk_para_max(text: str, p: dict, t: dict) -> CheckResult:
    """单段超长 hard_gate：单段 > 120 CJK 字（每章 ≤1 例外）。
    超 hard_gate 阈值 = FAIL（audit_hub 标 STYLE_单段超长 = HARD_GATE）。
    超 warn 阈值 = WARN。"""
    cfg = t.get("para_max_chars", {"warn": 80, "hard_gate": 120, "exception_per_chapter": 1})
    warn_th, hard_th, ex = cfg["warn"], cfg["hard_gate"], cfg.get("exception_per_chapter", 1)
    lens = _para_cjk_lens(text)
    if not lens:
        return CheckResult("单段超长", "PASS", "无段落", f"目标 ≤{warn_th} (hard_gate {hard_th})")
    over_hard = [(i + 1, n) for i, n in enumerate(lens) if n > hard_th]
    over_warn = [(i + 1, n) for i, n in enumerate(lens) if warn_th < n <= hard_th]
    # 找超长段在 text 中行号（按段索引近似 = 段首行号）
    para_text_list = [p.strip() for p in text.split("\n\n") if p.strip()]
    fail_lines = []
    for idx, n in over_hard[:5]:
        # 找该段首行
        if idx <= len(para_text_list):
            first_line = para_text_list[idx - 1].split("\n")[0][:20]
            ls = _find_lines(text, first_line)
            if ls:
                fail_lines.extend(ls[:1])
    # 例外条款：≤ ex 个超 hard_gate 算 WARN（按调研「每章 ≤1 例外」）
    if len(over_hard) > ex:
        detail = f"{len(over_hard)} 段 > {hard_th} 字（超例外 {ex}）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_hard[:3])
        return CheckResult("单段超长", "FAIL", detail,
                           f"目标 ≤{warn_th} (hard_gate {hard_th}, 例外 ≤{ex})",
                           fail_lines)
    if over_hard:
        detail = f"{len(over_hard)} 段 > {hard_th} 字（在例外内）：" + \
                 ", ".join(f"段{i}({n}字)" for i, n in over_hard[:3])
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
    paras = [p_.strip() for p_ in text.split("\n\n") if p_.strip()]
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


# ── 主校验 & 报告 ────────────────────────────────────────────

def validate_style(text: str, thresholds: dict) -> list[CheckResult]:
    """对文本执行 15 项风格校验，返回 CheckResult 列表。
    v23.12 新增 3 项段长检查（单段超长 hard_gate / 单句独行 / 长段计数）。"""
    p = analyze_text(text)
    return [
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
        thresholds = _apply_style_overrides(thresholds, sd)

    results = validate_style(text, thresholds)
    print(format_report(results))
    sys.exit(1 if any(r.status == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    main()
