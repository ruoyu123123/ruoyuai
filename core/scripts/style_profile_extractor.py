#!/usr/bin/env python3
"""
style_profile_extractor.py — 作者量化风格指纹提取器（L1a 升格 · 纯 Python 统计）

北极星：①贴合作者风格 ⑤ advisory（不黑箱不干涉模型判断）⑥ 别过度复杂（纯 stdlib·复用现有）。

【为什么】
  实证（"Breaking the Imitation Game"）：把目标作者的多维风格数值**显式告知 writer**
  （"句长均值 19 字 / 单句独行占比 51% / 问号密度 4.2 个每千字 …"）比让模型自己看样本去悟更有效。
  系统此前只在 validate_style 的 L1a 评分阈值里**算了**作者句长分位数，但**没把它当成写作时
  显式下发给 writer 的目标硬数字**。本模块把那套 L1a 分位数升格 + 扩成多维「作者量化风格指纹」。

【做什么】
  从两类数据源抽多维风格剖面（纯 Python·零新依赖·复用 style_analyzer 的取数原语）：
    (A) 作者原文章节文本列表 → 最可靠的 ground truth（聚合 per-章经验分位数）。
    (B) 已蒸馏的 作者风格.json.quantitative → 复用已算好的 mean/std/分位数（无原文时兜底）。
  两条路都收敛成同一个 fingerprint dict：
    · sentence_length         句长 mean / std（+ 若有分位数 p5/p50/p95）
    · single_sentence_para_ratio  单句独行占比（爽文节奏核心指标）
    · paragraph_length_chars  段长分位数 [p5,p50,p95]（复用 L1a 段长桶）
    · dialogue_ratio          对话密度
    · punctuation_per_1k      标点频率（问号/感叹/省略号/逗号/句号/破折号）
    · function_words_per_1k   功能词虚词比（节选高区分度的）
    · signature_collocations  高频签名词搭配（从 golden_passages / 原文抽，非 AI 套话）
  每个数值字段附一句**显式自然语言目标指令**（directive），供 build_manifest 注入 writer。

【边界】
  · 纯统计 advisory：只产「目标数值 + 指令文案」，不硬锁、不门禁、不改判决。
  · 零依赖：只用 stdlib + 同目录 style_analyzer（jieba 都不碰）。
  · 数据源 schema 容错：蛊真人（dialogue_ratio_pct / punctuation_per_1k）与
    惊悚乐园（dialogue_ratio / punctuation_density_per_1000）两套蒸馏 schema 并存——本模块都吃。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402  复用 count_chinese / split_* / calc_quantiles 等取数原语


# 功能词指纹只下发高区分度子集（全 15 个里挑对作者画像最有信息量的），避免 prompt 噪声。
_FINGERPRINT_FUNCTION_WORDS = ["的", "了", "着", "却", "便", "竟", "倒", "只", "又", "也"]

# 标点指纹下发顺序（人最能感知的几类节奏标点优先）。
_PUNCT_KEYS = ["question", "exclamation", "ellipsis", "comma", "period", "dash", "comma_period_ratio"]
_PUNCT_LABEL = {
    "question": "问号", "exclamation": "感叹号", "ellipsis": "省略号",
    "comma": "逗号", "period": "句号", "dash": "破折号", "comma_period_ratio": "逗句比",
}

# 签名搭配抽取：4-8 字含动作/感官的连续片段，排除纯虚词/纯标点。AI 套话排除（复用 style_analyzer 名单）。
_AI_STOP = set(sa.AI_STRUCTURAL_BANNED) | set(sa.CRAFT_SIGNATURE_BANNED) | set(sa.QUOTA_WORDS)
_COLLO_PAT = re.compile(r"[一-鿿]{4,8}")


def _num(v):
    """只接受真数值（蒸馏 LLM 可能把 mean 写成 '约20字'/'40%' 字符串 → 返回 None 不崩）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _stat_pair(stat):
    """从一个统计 dict 取 (mean, std)，两者都缺则 (None,None)。容错字符串。"""
    if not isinstance(stat, dict):
        return None, None
    return _num(stat.get("mean")), _num(stat.get("std"))


def _quantile_triple(stat):
    """从统计 dict 取 (p5,p50,p95)；缺任一返回 None。p50 缺则用 median 兜底。"""
    if not isinstance(stat, dict):
        return None
    p5 = _num(stat.get("p5"))
    p50 = _num(stat.get("p50"))
    if p50 is None:
        p50 = _num(stat.get("median"))
    p95 = _num(stat.get("p95"))
    if p5 is not None and p95 is not None and p5 <= p95:
        return {"p5": round(p5, 2), "p50": round(p50, 2) if p50 is not None else None,
                "p95": round(p95, 2)}
    return None


# ============ 数据源 A：作者原文章节文本 → 经验剖面（ground truth）============

def extract_from_chapter_texts(chapter_texts: list[str]) -> dict:
    """从作者原文章节文本列表聚合多维量化剖面（最可靠路径·纯 Python）。

    复用 style_analyzer.aggregate_chapter_quantiles（已聚合段长/标点/功能词 per-章分位数）
    + 逐章 analyze_text 取句长/单句独行/对话密度，聚合成 mean/std/分位数。坏章自动跳过。
    """
    if not chapter_texts:
        return {}

    quant_buckets = sa.aggregate_chapter_quantiles(chapter_texts)  # 段长/标点/功能词分位数桶

    sent_means: list[float] = []
    sent_stds: list[float] = []
    single_ratios: list[float] = []
    dialogue_ratios: list[float] = []
    collo_counter: Counter = Counter()
    used = 0
    for text in chapter_texts:
        if sa.count_chinese(text) <= 0:
            continue
        res = sa.analyze_text(text)
        ss = res.get("sentence_stats") or {}
        if _num(ss.get("mean")) is not None:
            sent_means.append(float(ss["mean"]))
        if _num(ss.get("std")) is not None:
            sent_stds.append(float(ss["std"]))
        sr = _num(res.get("single_sentence_para_ratio"))
        if sr is not None:
            single_ratios.append(sr)
        dr = _num(res.get("dialogue_ratio"))
        if dr is not None:
            dialogue_ratios.append(dr)
        _accumulate_collocations(text, collo_counter)
        used += 1

    def _mean(vals):
        return round(sum(vals) / len(vals), 2) if vals else None

    sent_mean = _mean(sent_means)
    # 句长 std 用 per-章 std 的均值（章内波动）而非章间 mean 的 std（更贴 writer 直觉）
    sent_std = _mean(sent_stds)

    return _assemble({
        "sentence_length": {"mean": sent_mean, "std": sent_std,
                            **(_quantile_triple(quant_buckets.get("sentence_length")) or {})},
        "sentence_length_quantiles": None,  # 句长分位数（raw 路无逐句聚合·留空）
        "single_sentence_para_ratio": _mean(single_ratios),
        "paragraph_length_chars": _quantile_triple(quant_buckets.get("paragraph_length_chars")),
        "dialogue_ratio": _mean(dialogue_ratios),
        "punctuation_per_1k": _punct_from_buckets(quant_buckets.get("punctuation_per_1k")),
        "function_words_per_1k": _fw_from_buckets(quant_buckets.get("function_words_per_1k")),
        "signature_collocations": _top_collocations(collo_counter),
    }, source="chapter_texts", n=used)


def _punct_from_buckets(buckets) -> dict:
    """从 aggregate_chapter_quantiles 的 punctuation_per_1k 桶取每标点的 mean（per-章分位数桶含 mean）。"""
    out: dict[str, float] = {}
    if not isinstance(buckets, dict):
        return out
    for k, stat in buckets.items():
        m = _num(stat.get("mean")) if isinstance(stat, dict) else None
        if m is not None:
            out[k] = round(m, 2)
    return out


def _fw_from_buckets(buckets) -> dict:
    out: dict[str, float] = {}
    if not isinstance(buckets, dict):
        return out
    for w, stat in buckets.items():
        m = _num(stat.get("mean")) if isinstance(stat, dict) else None
        if m is not None:
            out[w] = round(m, 2)
    return out


def _accumulate_collocations(text: str, counter: Counter) -> None:
    """累计 4-8 字签名片段（排除含 AI 套话/纯虚词的）。"""
    for seg in _COLLO_PAT.findall(text):
        if any(stop in seg for stop in _AI_STOP):
            continue
        # 排除整段都是高频虚词（无实义）
        if all(ch in sa.PRONOUNS or ch in "的了着也就都还" for ch in seg):
            continue
        counter[seg] += 1


def _top_collocations(counter: Counter, top_k: int = 8) -> list[str]:
    """取出现 ≥3 次的高频签名搭配 top_k（出现 1-2 次的不算签名·噪声）。"""
    return [w for w, c in counter.most_common(top_k * 4) if c >= 3][:top_k]


# ============ 数据源 B：已蒸馏 作者风格.json.quantitative → 剖面（兜底）============

def extract_from_author_profile(profile: dict) -> dict:
    """从已蒸馏 作者风格.json 的 quantitative 段抽剖面（无原文时兜底）。

    容错两套蒸馏 schema：
      · 惊悚乐园：dialogue_ratio{mean,std} / punctuation_density_per_1000 / paragraph_length{mean_chars}
      · 蛊真人  ：dialogue_ratio_pct{mean} / punctuation_per_1k / paragraph_length_chars(可能 null)
    缺的字段就缺（不编造），由 build_style_fingerprint 决定哪些维度可下发。
    """
    if not isinstance(profile, dict):
        return {}
    q = profile.get("quantitative")
    if not isinstance(q, dict):
        return {}

    # 句长
    sl_mean, sl_std = _stat_pair(q.get("sentence_length"))
    sl_q = _quantile_triple(q.get("sentence_length"))

    # 单句独行占比：惊悚乐园在 paragraph_length.single_sentence_para_ratio_mean
    single_ratio = None
    pl = q.get("paragraph_length")
    if isinstance(pl, dict):
        single_ratio = _num(pl.get("single_sentence_para_ratio_mean"))
    if single_ratio is None:
        single_ratio = _num(q.get("single_sentence_para_ratio"))

    # 段长分位数：先试 L1a 桶 paragraph_length_chars（分位数），再退回 paragraph_length.mean_chars
    para_q = _quantile_triple(q.get("paragraph_length_chars"))
    if para_q is None and isinstance(pl, dict):
        pm = _num(pl.get("mean_chars"))
        if pm is not None:
            para_q = {"p5": None, "p50": round(pm, 2), "p95": None}

    # 对话密度：dialogue_ratio{mean}(0-1) 或 dialogue_ratio_pct{mean}(0-100)
    dr = None
    if isinstance(q.get("dialogue_ratio"), dict):
        dr = _num(q["dialogue_ratio"].get("mean"))
    if dr is None and isinstance(q.get("dialogue_ratio_pct"), dict):
        pct = _num(q["dialogue_ratio_pct"].get("mean"))
        dr = round(pct / 100.0, 4) if pct is not None else None

    return _assemble({
        "sentence_length": {"mean": sl_mean, "std": sl_std, **(sl_q or {})},
        "sentence_length_quantiles": sl_q,
        "single_sentence_para_ratio": round(single_ratio, 4) if single_ratio is not None else None,
        "paragraph_length_chars": para_q,
        "dialogue_ratio": round(dr, 4) if dr is not None else None,
        "punctuation_per_1k": _punct_from_profile(q),
        "function_words_per_1k": _fw_from_profile(q),
        "signature_collocations": _collocations_from_profile(profile),
    }, source="author_profile", n=_num(profile.get("analyzed_chapters")) or None)


def _punct_from_profile(q: dict) -> dict:
    """容错 punctuation_density_per_1000{key:{mean}} 与 punctuation_per_1k{key:数值}。"""
    out: dict[str, float] = {}
    src = None
    if isinstance(q.get("punctuation_density_per_1000"), dict) and q["punctuation_density_per_1000"]:
        src = q["punctuation_density_per_1000"]
    elif isinstance(q.get("punctuation_per_1k"), dict) and q["punctuation_per_1k"]:
        src = q["punctuation_per_1k"]
    if not src:
        return out
    for k, v in src.items():
        m = _num(v.get("mean")) if isinstance(v, dict) else _num(v)
        if m is not None:
            out[k] = round(m, 2)
    return out


def _fw_from_profile(q: dict) -> dict:
    out: dict[str, float] = {}
    src = None
    if isinstance(q.get("function_word_fingerprint_per_1000"), dict) and q["function_word_fingerprint_per_1000"]:
        src = q["function_word_fingerprint_per_1000"]
    elif isinstance(q.get("function_words_per_1k"), dict) and q["function_words_per_1k"]:
        src = q["function_words_per_1k"]
    if not src:
        return out
    for w, v in src.items():
        m = _num(v.get("mean")) if isinstance(v, dict) else _num(v)
        if m is not None:
            out[w] = round(m, 2)
    return out


def _collocations_from_profile(profile: dict) -> list[str]:
    """从 golden_passages 文本抽高频签名搭配（蒸馏库已挑出的代表段）。"""
    passages = profile.get("golden_passages")
    if not isinstance(passages, list) or not passages:
        return []
    counter: Counter = Counter()
    for p in passages:
        txt = p.get("text") if isinstance(p, dict) else (p if isinstance(p, str) else "")
        if txt:
            _accumulate_collocations(txt, counter)
    # golden_passages 体量小，门槛降到 ≥2
    return [w for w, c in counter.most_common(32) if c >= 2][:8]


# ============ 统一组装 + 显式指令文案 ============

def _assemble(metrics: dict, source: str, n) -> dict:
    """把各维度数值 + 自然语言目标指令打包成 fingerprint。空维度自动剔除（不下发噪声）。"""
    directives: list[str] = []

    sl = metrics.get("sentence_length") or {}
    sl_mean = _num(sl.get("mean"))
    sl_std = _num(sl.get("std"))
    if sl_mean is not None:
        d = f"句长均值目标 {sl_mean:.0f} 字"
        if sl_std is not None:
            d += f"（章内 std≈{sl_std:.0f}，长短句交替而非匀速）"
        p5, p95 = _num(sl.get("p5")), _num(sl.get("p95"))
        if p5 is not None and p95 is not None:
            d += f"；90% 句子落在 {p5:.0f}-{p95:.0f} 字"
        directives.append(d)

    ssr = _num(metrics.get("single_sentence_para_ratio"))
    if ssr is not None:
        directives.append(f"单句独行段占比目标 {ssr:.0%}（对话/强节奏处一句一段）")

    para = metrics.get("paragraph_length_chars")
    if isinstance(para, dict):
        p50 = _num(para.get("p50"))
        p5, p95 = _num(para.get("p5")), _num(para.get("p95"))
        if p50 is not None and p5 is not None and p95 is not None:
            directives.append(f"段长中位 {p50:.0f} 字，90% 段落 {p5:.0f}-{p95:.0f} 字")
        elif p50 is not None:
            directives.append(f"段长均值约 {p50:.0f} 字")

    dr = _num(metrics.get("dialogue_ratio"))
    if dr is not None:
        directives.append(f"对话占比目标 {dr:.0%}")

    punct = metrics.get("punctuation_per_1k") or {}
    punct_bits = []
    for k in _PUNCT_KEYS:
        v = _num(punct.get(k))
        if v is None:
            continue
        if k == "comma_period_ratio":
            if v > 1.05:
                punct_bits.append(f"逗句比≈{v:.1f}:1（多用逗号连缀长句）")
        else:
            punct_bits.append(f"{_PUNCT_LABEL.get(k, k)}≈{v:.1f}/千字")
    if punct_bits:
        directives.append("标点节奏：" + "、".join(punct_bits))

    fw = metrics.get("function_words_per_1k") or {}
    fw_bits = []
    for w in _FINGERPRINT_FUNCTION_WORDS:
        v = _num(fw.get(w))
        if v is not None and v >= 1.0:  # 只下发明显高频的虚词（噪声门槛）
            fw_bits.append(f"「{w}」{v:.0f}")
    if fw_bits:
        directives.append("高频虚词（个/千字）：" + "、".join(fw_bits))

    collos = metrics.get("signature_collocations") or []
    if collos:
        directives.append("作者高频签名搭配（可借鉴语感，勿机械堆砌）：" + "、".join(collos))

    # 剔除空维度
    clean_metrics = {}
    for k, v in metrics.items():
        if v in (None, {}, []):
            continue
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if vv is not None}
            if not v:
                continue
        clean_metrics[k] = v

    return {
        "source": source,
        "n_chapters": int(n) if isinstance(n, (int, float)) else None,
        "metrics": clean_metrics,
        "directives": directives,
        "_doc": (
            "作者量化风格指纹（advisory）：写作时显式告知 writer 的多维目标硬数字。"
            "实证显式数值指令 > 让模型自己看样本悟。非门禁、非硬锁——writer 据此校准节奏，可偏离。"
        ),
    }


def build_style_fingerprint(
    profile: dict | None = None,
    chapter_texts: list[str] | None = None,
) -> dict:
    """统一入口：优先用原文（ground truth），无则退回已蒸馏 profile。两者皆空返回 {}。

    build_manifest 注入时调用本函数。chapter_texts 一般为空（写作时不重扫原文），
    走 profile 路（复用 作者风格.json 已蒸馏的 quantitative）。
    """
    if chapter_texts:
        fp = extract_from_chapter_texts(chapter_texts)
        if fp.get("directives"):
            return fp
    if isinstance(profile, dict):
        return extract_from_author_profile(profile)
    return {}


# ============ CLI（调试/离线生成指纹）============

def _read_chapter_dir(d: Path) -> list[str]:
    texts = []
    for p in sorted(d.glob("*.txt")):
        try:
            texts.append(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return texts


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="作者量化风格指纹提取器")
    ap.add_argument("--profile", help="作者风格.json 路径")
    ap.add_argument("--chapters-dir", help="原文章节目录（*.txt）")
    ap.add_argument("--output", help="输出 JSON 路径（默认 stdout）")
    args = ap.parse_args(argv)

    profile = None
    if args.profile:
        try:
            profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] 读 profile 失败: {e}", file=sys.stderr)

    texts = None
    if args.chapters_dir:
        texts = _read_chapter_dir(Path(args.chapters_dir))

    fp = build_style_fingerprint(profile=profile, chapter_texts=texts)
    out = json.dumps(fp, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"[OK] 指纹写入 {args.output}（{len(fp.get('directives', []))} 条指令）", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
