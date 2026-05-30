#!/usr/bin/env python3
"""style_evaluator.py — SFS (Style Fidelity Score) 中文小说风格评估器
用法: python style_evaluator.py --ref <原文> --gen <AI生成> [--baseline X.json] [--output out.json]
"""
from __future__ import annotations
import json, math, random, re, sys
from pathlib import Path

import numpy as np
from scipy.spatial.distance import jensenshannon

# --- 导入 style_analyzer ---
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from style_analyzer import (  # noqa: E402
    analyze_text,
    compare_profiles,
    BANNED_WORDS,
    AI_STRUCTURAL_BANNED,
    CRAFT_SIGNATURE_BANNED,
    count_chinese,
    split_paragraphs,
    split_sentences,
    calc_stats,
    CHINESE_CHAR,
    COMMA_PATTERN,
    FUNCTION_WORDS,
)

# ---- JSON 编码器 / 工具函数 ----

class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):  return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.bool_,)):    return bool(obj)
        if isinstance(obj, np.ndarray):     return obj.tolist()
        return super().default(obj)

def _read_texts(path: Path) -> str:
    """读取单文件或目录下所有 txt，合并返回。"""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if path.is_dir():
        parts = []
        for f in sorted(path.glob("*.txt")):
            parts.append(f.read_text(encoding="utf-8"))
        if not parts:
            print(f"[错误] 目录中无 txt 文件: {path}", file=sys.stderr)
            sys.exit(1)
        return "\n\n".join(parts)
    print(f"[错误] 路径不存在: {path}", file=sys.stderr)
    sys.exit(1)


def _read_ref_texts(ref_args: list[str]) -> list[str]:
    """E5：支持多个 --ref 参数构建多基线池。

    每个 ref 参数可以是文件或目录。
    - 文件 → 作为一个基线样本
    - 目录 → 每个 .txt 文件作为一个独立基线样本
    """
    texts: list[str] = []
    for arg in ref_args:
        p = Path(arg)
        if p.is_file():
            texts.append(p.read_text(encoding="utf-8"))
        elif p.is_dir():
            for f in sorted(p.glob("*.txt")):
                texts.append(f.read_text(encoding="utf-8"))
        else:
            print(f"[错误] ref 路径不存在: {p}", file=sys.stderr)
            sys.exit(1)
    if not texts:
        print(f"[错误] 没有可用的 ref 样本", file=sys.stderr)
        sys.exit(1)
    return texts


def _to_dist_array(dist_dict: dict, keys: list[str] | None = None) -> np.ndarray:
    """将分布 dict 转为 numpy 数组，保证键对齐。"""
    if keys is None:
        keys = sorted(set(list(dist_dict.keys())))
    arr = np.array([dist_dict.get(k, 0.0) for k in keys], dtype=float)
    total = arr.sum()
    if total > 0:
        arr = arr / total
    return arr


def _cosine_sim(a: dict, b: dict) -> float:
    """两个 dict 向量的余弦相似度。"""
    keys = sorted(set(list(a.keys()) + list(b.keys())))
    va = np.array([a.get(k, 0.0) for k in keys], dtype=float)
    vb = np.array([b.get(k, 0.0) for k in keys], dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _pct_match(ref_val: float, gen_val: float) -> float:
    """百分比偏差匹配度: 1 - |diff| / max(|ref|, 0.01)，结果 0~1。

    支持 ref_val 是区间字典 {"min": x, "max": y, "mean": z}：
    - 落入区间得满分 1.0
    - 超出区间按到最近边界的相对距离扣分
    """
    if isinstance(ref_val, dict) and "min" in ref_val and "max" in ref_val:
        lo = ref_val["min"]
        hi = ref_val["max"]
        if lo <= gen_val <= hi:
            return 1.0
        if gen_val < lo:
            base = max(abs(lo), 0.01)
            diff_ratio = (lo - gen_val) / base
        else:
            base = max(abs(hi), 0.01)
            diff_ratio = (gen_val - hi) / base
        return max(0.0, min(1.0, 1.0 - diff_ratio))

    base = max(abs(ref_val), 0.01)
    diff_ratio = abs(ref_val - gen_val) / base
    return max(0.0, min(1.0, 1.0 - diff_ratio))


def _interval_jsd_score(ref_dists: list[dict], gen_dist: dict) -> float:
    """多基线分布的 JSD：取与所有 ref 中最接近一个的 JSD（最佳匹配）。"""
    if not ref_dists:
        return 0.0
    if len(ref_dists) == 1:
        return _jsd_score(ref_dists[0], gen_dist)
    return max(_jsd_score(r, gen_dist) for r in ref_dists)


def _build_interval_value(values: list) -> dict | float:
    """从多个数值构建区间字典。"""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return {
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def _build_interval_profile(profiles: list[dict]) -> dict:
    """从多个 profile 构建区间 profile。

    数值字段 → 区间字典 {min, max, mean}
    分布字段（dict of float） → 保持原 profile 列表（在 JSD 时取最佳匹配）
    """
    if not profiles:
        return {}
    if len(profiles) == 1:
        return profiles[0]

    out: dict = {}
    keys = profiles[0].keys()
    for k in keys:
        v0 = profiles[0].get(k)
        if isinstance(v0, (int, float)):
            vals = [p.get(k, 0) for p in profiles
                    if isinstance(p.get(k), (int, float))]
            out[k] = _build_interval_value(vals)
        elif isinstance(v0, dict):
            # 字典字段：分两类
            sub_keys = set()
            for p in profiles:
                if isinstance(p.get(k), dict):
                    sub_keys |= set(p[k].keys())
            sub_vals: dict = {}
            all_numeric = True
            for sk in sub_keys:
                vals = []
                for p in profiles:
                    sub = p.get(k, {})
                    if isinstance(sub.get(sk), (int, float)):
                        vals.append(sub[sk])
                if vals:
                    sub_vals[sk] = _build_interval_value(vals)
                else:
                    sub_vals[sk] = profiles[0].get(k, {}).get(sk)
                    all_numeric = False
            out[k] = sub_vals
        else:
            out[k] = v0
    # 同时保留原始 profile 列表，供 JSD 多基线
    out["_source_profiles"] = profiles
    return out


def _jsd_score(ref_dist: dict, gen_dist: dict) -> float:
    """用 JSD 计算两个分布的相似度，返回 0~1（1 = 完全相同）。"""
    keys = sorted(set(list(ref_dist.keys()) + list(gen_dist.keys())))
    p = _to_dist_array(ref_dist, keys)
    q = _to_dist_array(gen_dist, keys)
    # 添加微小平滑避免零概率
    eps = 1e-10
    p = p + eps
    q = q + eps
    p = p / p.sum()
    q = q / q.sum()
    jsd_val = jensenshannon(p, q, base=2)
    # jensenshannon 返回 sqrt(JSD)，范围 [0, 1]
    return max(0.0, 1.0 - jsd_val)


def _para_opening_diversity(text: str) -> float:
    """段落开头多样性：连续段首字重复率的反面，0~1。"""
    paras = split_paragraphs(text)
    if len(paras) < 2:
        return 1.0
    first_chars = []
    for p in paras:
        cn = CHINESE_CHAR.findall(p)
        if cn:
            first_chars.append(cn[0])
    if len(first_chars) < 2:
        return 1.0
    repeat_count = sum(
        1 for i in range(1, len(first_chars)) if first_chars[i] == first_chars[i - 1]
    )
    repeat_ratio = repeat_count / (len(first_chars) - 1)
    return 1.0 - repeat_ratio


# ============================================================
# 核心评分：程序化评分 (PS)
# ============================================================

def _resolve_numeric(v, default=0.0):
    """处理区间字典：取 mean 用作单点对照（compare_profiles 用）。"""
    if isinstance(v, dict) and "mean" in v:
        return v["mean"]
    if isinstance(v, (int, float)):
        return v
    return default


def _interval_pct_match(ref_val, gen_val: float) -> float:
    """区间百分比匹配（落入区间得满分；非区间退化为 _pct_match）。"""
    return _pct_match(ref_val, gen_val)


def _craft_freq_per_1000(profile: dict, source_profiles: list[dict]) -> float:
    """工艺签名词（CRAFT_SIGNATURE_BANNED）的每千字频率。

    多基线时跨所有 source_profiles 聚合（总命中 / 总字数 × 1000）——比逐 profile
    平均更稳健，反映作者用这批词的真实总体频率。banned_word_hits 是原始计数，
    必须按字数归一化，否则不同长度文本不可比。
    """
    profiles = source_profiles or [profile]
    total_hits = 0
    total_chars = 0
    for p in profiles:
        bw = p.get("banned_word_hits", {}) or {}
        for w, c in bw.items():
            if w in CRAFT_SIGNATURE_BANNED and isinstance(c, (int, float)):
                total_hits += c
        chars = p.get("total_chinese_chars", 0)
        if isinstance(chars, (int, float)):
            total_chars += chars
    if total_chars <= 0:
        return 0.0
    return total_hits * 1000.0 / total_chars


def compute_programmatic_score(ref_profile: dict, gen_profile: dict,
                               gen_text: str,
                               has_author_profile: bool = False) -> dict:
    """
    计算 12 维程序化评分，总权重 55%（内部归一化到 100 分制）。
    返回 {"total": float, "dimensions": [...], "grade": str}。

    v2 (E5)：支持 ref_profile 是区间 profile（含 _source_profiles 列表）。

    has_author_profile（2026-05-30 北极星①修 #4）：当本项目有作者风格档时，
    维度7『禁用词扣分』改为分级——AI 结构套话仍单边硬扣（任何作者都不用），
    工艺签名词（顿时/淡淡/显然…）改为对照 ref 侧频率差值（复刻频率≈参考频率=好），
    对齐 validate_style._chk_banned 的作者档优先逻辑（守原则①贴合作者风格 + ⑤不干涉模型）。
    """
    dims: list[dict] = []
    # 多基线源 profile（用于 JSD 多基线评分）
    source_profiles = ref_profile.get("_source_profiles", [ref_profile])
    multi_baseline = len(source_profiles) > 1

    def add(name: str, weight: float, score: float,
            ref_val=None, gen_val=None):
        flagged = bool(score < 0.60)
        dims.append({
            "name": name,
            "score": round(float(score * 100), 2),
            "weight": float(weight),
            "ref_val": ref_val,
            "gen_val": gen_val,
            "flagged": flagged,
        })

    # 1. 句长分布 JSD (8%)
    g_sld = gen_profile.get("sentence_length_distribution", {})
    if multi_baseline:
        r_slds = [p.get("sentence_length_distribution", {}) for p in source_profiles]
        score1 = _interval_jsd_score(r_slds, g_sld)
        r_sld_display = ref_profile.get("sentence_length_distribution", {})
    else:
        r_sld_display = ref_profile.get("sentence_length_distribution", {})
        score1 = _jsd_score(r_sld_display, g_sld)
    add("句长分布 JSD", 0.08, score1, r_sld_display, g_sld)

    # 2. 段落长度分布 JSD (5%)
    g_pld = gen_profile.get("paragraph_length_distribution", {})
    if multi_baseline:
        r_plds = [p.get("paragraph_length_distribution", {}) for p in source_profiles]
        score2 = _interval_jsd_score(r_plds, g_pld)
        r_pld_display = ref_profile.get("paragraph_length_distribution", {})
    else:
        r_pld_display = ref_profile.get("paragraph_length_distribution", {})
        score2 = _jsd_score(r_pld_display, g_pld)
    add("段落长度分布 JSD", 0.05, score2, r_pld_display, g_pld)

    # 3. 对话占比偏差 (6%)
    r_dr = ref_profile.get("dialogue_ratio", 0)
    g_dr = gen_profile.get("dialogue_ratio", 0)
    dr_score = _interval_pct_match(r_dr, g_dr)
    add("对话占比偏差", 0.06, min(dr_score, 1.0),
        r_dr if isinstance(r_dr, dict) else round(r_dr, 4), round(g_dr, 4))

    # 4. 标点密度指纹 (4%) — 5 维向量余弦
    g_punc = gen_profile.get("punctuation_density_per_1000", {})
    punc_keys = ["comma", "period", "ellipsis", "exclamation", "question"]
    g_punc5 = {k: g_punc.get(k, 0) for k in punc_keys}
    if multi_baseline:
        # 多基线：取与所有 ref 中最佳匹配
        punc_scores = []
        for p in source_profiles:
            rp = p.get("punctuation_density_per_1000", {})
            r_punc5 = {k: rp.get(k, 0) for k in punc_keys}
            punc_scores.append(max(_cosine_sim(r_punc5, g_punc5), 0))
        score4 = max(punc_scores)
        r_punc_display = {k: ref_profile.get("punctuation_density_per_1000", {}).get(k, 0)
                          for k in punc_keys}
    else:
        r_punc = ref_profile.get("punctuation_density_per_1000", {})
        r_punc_display = {k: r_punc.get(k, 0) for k in punc_keys}
        score4 = max(_cosine_sim(r_punc_display, g_punc5), 0)
    add("标点密度指纹", 0.04, score4, r_punc_display, g_punc5)

    # 5. 功能词指纹 (8%) — 15 维余弦
    g_fw = gen_profile.get("function_word_fingerprint_per_1000", {})
    if multi_baseline:
        fw_scores = []
        for p in source_profiles:
            r_fw = p.get("function_word_fingerprint_per_1000", {})
            fw_scores.append(max(_cosine_sim(r_fw, g_fw), 0))
        score5 = max(fw_scores)
        r_fw_display = ref_profile.get("function_word_fingerprint_per_1000", {})
    else:
        r_fw_display = ref_profile.get("function_word_fingerprint_per_1000", {})
        score5 = max(_cosine_sim(r_fw_display, g_fw), 0)
    add("功能词指纹", 0.08, score5, r_fw_display, g_fw)

    # 6. 句长标准差匹配 (5%)
    r_std = ref_profile.get("sentence_stats", {}).get("std", 0)
    g_std = gen_profile.get("sentence_stats", {}).get("std", 0)
    add("句长标准差匹配", 0.05, _interval_pct_match(r_std, g_std),
        r_std if isinstance(r_std, dict) else round(r_std, 2),
        round(g_std, 2))

    # 7. 禁用词扣分 (5%)
    g_banned = gen_profile.get("banned_word_hits", {})
    if has_author_profile:
        # 2026-05-30 北极星①修 #4：有作者风格档时分级（对齐 validate_style._chk_banned）。
        # ① AI 结构套话（与此同时/值得一提的是…）：任何作者都不用 → 仍单边硬扣。
        ai_hit = sum(c for w, c in g_banned.items() if w in AI_STRUCTURAL_BANNED)
        ai_score = max(0.0, 1.0 - ai_hit * 0.05)
        # ② 工艺签名词（顿时/淡淡/显然…）：可能是该作者签名笔法 → 改对照 ref 侧频率。
        #    复刻频率 ≈ 参考频率 = 好（落入即满分），不是单边硬扣。源作者高频用「淡淡」时
        #    忠实复刻不该被扣分（守原则①贴合作者风格 + ⑤不干涉模型）。
        g_craft_f = _craft_freq_per_1000(gen_profile, [gen_profile])
        r_craft_f = _craft_freq_per_1000(ref_profile, source_profiles)
        craft_score = _pct_match(r_craft_f, g_craft_f)
        # 两子项各占维度7一半权重
        banned_score = (ai_score + craft_score) / 2.0
        add("禁用词扣分", 0.05, banned_score,
            {"ai_struct_ref": 0, "craft_freq_per1000_ref": round(r_craft_f, 3)},
            {"ai_struct_hit": ai_hit, "craft_freq_per1000_gen": round(g_craft_f, 3)})
    else:
        # 无作者风格档：保持旧行为（全集单边硬扣 · 通用反 AI 腔兜底）。
        hit_count = sum(g_banned.values()) if g_banned else 0
        banned_score = max(0, 1.0 - hit_count * 0.05)
        add("禁用词扣分", 0.05, banned_score, 0, hit_count)

    # 8. 段落开头多样性 (3%)
    r_div = _para_opening_diversity(
        _reconstruct_hint(ref_profile, "ref"))  # 用 profile 不够，需原文
    g_div = _para_opening_diversity(gen_text)
    # 这里只用 gen_text 的多样性与 ref 对比
    add("段落开头多样性", 0.03, _pct_match(r_div, g_div),
        round(r_div, 4), round(g_div, 4))

    # 9. 极短段占比匹配 (3%) — 使用拟声豁免后的版本（向后兼容旧字段）
    r_usp = ref_profile.get("ultra_short_para_ratio", 0)
    g_usp = gen_profile.get("ultra_short_para_ratio", 0)
    add("极短段占比匹配", 0.03, _interval_pct_match(r_usp, g_usp),
        r_usp if isinstance(r_usp, dict) else round(r_usp, 4),
        round(g_usp, 4))

    # 10. 单句成段率匹配 (3%)
    r_ssp = ref_profile.get("single_sentence_para_ratio", 0)
    g_ssp = gen_profile.get("single_sentence_para_ratio", 0)
    add("单句成段率匹配", 0.03, _interval_pct_match(r_ssp, g_ssp),
        r_ssp if isinstance(r_ssp, dict) else round(r_ssp, 4),
        round(g_ssp, 4))

    # 11. 拟声段数量匹配 (2.5%)
    r_ono = ref_profile.get("onomatopoeia_para_count", 0)
    g_ono = gen_profile.get("onomatopoeia_para_count", 0)
    r_ono_num = _resolve_numeric(r_ono)
    add("拟声段数量匹配", 0.025, _interval_pct_match(r_ono, float(g_ono)),
        r_ono if isinstance(r_ono, dict) else r_ono, g_ono)

    # 12. 群戏人数匹配 (2.5%)
    # v2 (E4)：使用 active_speaker_count（如果存在），否则降级为 speaker_count
    r_sp = ref_profile.get("active_speaker_count",
                           ref_profile.get("speaker_count", 0))
    g_sp = gen_profile.get("active_speaker_count",
                           gen_profile.get("speaker_count", 0))
    add("群戏人数匹配", 0.025, _interval_pct_match(r_sp, float(g_sp)),
        r_sp if isinstance(r_sp, dict) else r_sp, g_sp)

    # 13. (新增) 引号化独白比匹配 (2%) — E6 引入
    r_mr = ref_profile.get("inner_monologue_ratio", 0)
    g_mr = gen_profile.get("inner_monologue_ratio", 0)
    add("引号化独白比", 0.02, _interval_pct_match(r_mr, g_mr),
        r_mr if isinstance(r_mr, dict) else round(r_mr, 4),
        round(g_mr, 4))

    # 加权总分
    total_weight = sum(d["weight"] for d in dims)
    weighted = sum(d["score"] * d["weight"] for d in dims) / total_weight \
        if total_weight > 0 else 0
    grade = ("A" if weighted >= 90 else "B" if weighted >= 80
             else "C" if weighted >= 70 else "D")

    return {"total": round(weighted, 2), "dimensions": dims, "grade": grade}


# 段落开头多样性需要原文文本，但 ref 可能只有 profile。
# 用一个全局字典缓存文本。
_TEXT_CACHE: dict[str, str] = {}


def _reconstruct_hint(profile: dict, key: str) -> str:
    """从缓存获取原文文本，供段落开头多样性计算。"""
    return _TEXT_CACHE.get(key, "")


# ============================================================
# LLM 评分 Prompt 生成
# ============================================================

def _sample_paragraphs(text: str, n: int = 3,
                       min_len: int = 200, max_len: int = 500) -> list[str]:
    """从文本中随机抽取 n 段，每段 min_len~max_len 字符。"""
    paras = split_paragraphs(text)
    # 合并相邻短段以达到最小长度
    chunks: list[str] = []
    buf = ""
    for p in paras:
        buf += p + "\n"
        if count_chinese(buf) >= min_len:
            chunks.append(buf.strip())
            buf = ""
    if buf.strip() and count_chinese(buf) >= min_len // 2:
        chunks.append(buf.strip())

    # 截断到 max_len
    trimmed = []
    for c in chunks:
        cn = count_chinese(c)
        if cn > max_len:
            # 粗略截断到 max_len 汉字
            idx, cnt = 0, 0
            for idx, ch in enumerate(c):
                if re.match(r"[一-鿿]", ch):
                    cnt += 1
                if cnt >= max_len:
                    break
            c = c[:idx + 1] + "……"
        if count_chinese(c) >= min_len // 2:
            trimmed.append(c)

    if not trimmed:
        # 文本太短，返回全部
        return [text[:1500]] if text else []

    random.seed(42)
    selected = random.sample(trimmed, min(n, len(trimmed)))
    return selected


def generate_llm_prompt(ref_text: str, gen_text: str) -> str:
    """生成 LLM 评分 prompt 文本。

    L3e（2026-05-31 · env SFS_LLM_DEBIAS）：默认 off → 旧单序 + random.sample 选片
    （行为完全不变 · 零回归）；on → 转调 generate_pairwise_llm_prompt（pairwise 顺序双跑
    + 风格代表性选片 · 消 LLM 顺序偏置 + 选片内容偏置）。"""
    if _sfs_llm_debias_on():
        return generate_pairwise_llm_prompt(ref_text, gen_text)

    ref_samples = _sample_paragraphs(ref_text, 3)
    gen_samples = _sample_paragraphs(gen_text, 3)

    dims = list(_SFS_LLM_DIMS)
    L = ["# 风格保真度 LLM 评分", "",
         "请根据以下原文样本和 AI 生成样本，对 AI 文本的风格匹配度打分。", "",
         "## 原文样本", ""]
    for i, s in enumerate(ref_samples, 1):
        L += [f"### 原文段落 {i}", s, ""]
    L += ["## AI 生成样本", ""]
    for i, s in enumerate(gen_samples, 1):
        L += [f"### 生成段落 {i}", s, ""]
    L += ["## 评分维度（每维度 1-10 分）", ""]
    grades = [("9-10", "优秀", "高度一致，几乎无法区分"),
              ("7-8", "良好", "基本匹配，偶有偏差但不出戏"),
              ("5-6", "一般", "有明显差异，能感受到不是原作者"),
              ("3-4", "较差", "严重偏离，AI味明显"),
              ("1-2", "极差", "完全不匹配，像另一个作者")]
    for name, desc in dims:
        L += [f"### {name}", f"说明：{desc}",
              "| 分数 | 等级 | 描述 |", "|------|------|------|"]
        for sc, lv, ds in grades:
            L.append(f"| {sc} | {lv} | {name}{ds} |")
        L.append("")
    L += ["## 输出格式", "请严格按以下 JSON 格式输出评分：", "```json", "{"]
    for i, (name, _) in enumerate(dims):
        comma = "," if i < len(dims) - 1 else ""
        L.append(f'  "{name}": {{"score": <1-10>, "reason": "<一句话理由>"}}{comma}')
    L += ["}", "```"]
    return "\n".join(L)


# ============================================================
# 告警与建议生成
# ============================================================

def _to_scalar(v, default=0.0):
    """区间字典 → mean；否则原值。"""
    if isinstance(v, dict) and "mean" in v:
        return v["mean"]
    if isinstance(v, (int, float)):
        return v
    return default


def generate_alerts(ref_profile: dict, gen_profile: dict,
                    ps_dims: list[dict],
                    has_author_profile: bool = False) -> tuple[list[dict], list[str]]:
    """生成 style_alerts 和 improvement_suggestions。

    v2 (E5)：兼容区间 profile（先转标量）。

    has_author_profile（2026-05-30 修 #4）：有作者风格档时，禁用词告警只对 AI 结构套话
    报 critical/warning；工艺签名词（顿时/淡淡…）只作 info 提示（不催删 · 守原则①复刻作者优先）。
    """
    alerts: list[dict] = []
    suggestions: list[str] = []

    # 对话占比
    r_dr = _to_scalar(ref_profile.get("dialogue_ratio", 0))
    g_dr = _to_scalar(gen_profile.get("dialogue_ratio", 0))
    if r_dr > 0.1 and abs(r_dr - g_dr) / max(r_dr, 0.01) > 0.5:
        level = "critical" if abs(r_dr - g_dr) / max(r_dr, 0.01) > 0.7 else "warning"
        alerts.append({
            "level": level,
            "message": f"对话占比严重偏{'低' if g_dr < r_dr else '高'}: "
                       f"原文 {r_dr*100:.0f}%, 生成 {g_dr*100:.0f}%"
        })
        target = r_dr * 100
        suggestions.append(f"调整对话密度，目标 {target:.0f}%+")

    # 逗句比
    r_cpr = _to_scalar(ref_profile.get("punctuation_density_per_1000", {}).get(
        "comma_period_ratio", 0))
    g_cpr = _to_scalar(gen_profile.get("punctuation_density_per_1000", {}).get(
        "comma_period_ratio", 0))
    if abs(r_cpr - g_cpr) / max(r_cpr, 0.01) > 0.5:
        alerts.append({
            "level": "warning",
            "message": f"逗句比偏差过大: 原文 {r_cpr:.2f}, 生成 {g_cpr:.2f}"
        })
        if g_cpr < r_cpr:
            suggestions.append("增加逗号使用频率，长句中应有更多逗号断句")
        else:
            suggestions.append("减少逗号使用频率，句式可以更简洁利落")

    # 禁用词
    g_banned = gen_profile.get("banned_word_hits", {})
    if g_banned:
        if has_author_profile:
            # 有作者档：AI 结构套话照常催删；工艺签名词仅 info（作者签名笔法不催删）。
            ai_hits = {w: c for w, c in g_banned.items() if w in AI_STRUCTURAL_BANNED}
            craft_hits = {w: c for w, c in g_banned.items()
                          if w in CRAFT_SIGNATURE_BANNED}
            if ai_hits:
                total_ai = sum(ai_hits.values())
                alerts.append({
                    "level": "critical" if total_ai > 5 else "warning",
                    "message": f"命中 {total_ai} 个 AI 结构套话: {', '.join(ai_hits.keys())}"
                })
                suggestions.append(f"删除 AI 结构套话: {', '.join(ai_hits.keys())}")
            if craft_hits:
                alerts.append({
                    "level": "info",
                    "message": f"工艺签名词 {sum(craft_hits.values())} 处"
                               f"（{', '.join(craft_hits.keys())}）· 已对照作者频率评分，"
                               f"若为该作者签名笔法可保留"
                })
        else:
            hit_words = ", ".join(g_banned.keys())
            total_hits = sum(g_banned.values())
            alerts.append({
                "level": "critical" if total_hits > 5 else "warning",
                "message": f"命中 {total_hits} 个禁用词: {hit_words}"
            })
            suggestions.append(f"删除禁用词: {hit_words}")

    # 句长标准差
    r_std = _to_scalar(ref_profile.get("sentence_stats", {}).get("std", 0))
    g_std = _to_scalar(gen_profile.get("sentence_stats", {}).get("std", 0))
    if r_std > 0 and abs(r_std - g_std) / max(r_std, 0.01) > 0.4:
        alerts.append({
            "level": "warning",
            "message": f"句长变化幅度偏差: 原文 std={r_std:.1f}, "
                       f"生成 std={g_std:.1f}"
        })
        if g_std < r_std:
            suggestions.append("增加句长变化：混用长短句，紧张时短句连发，描写时长句展开")
        else:
            suggestions.append("减少句长波动：保持更稳定的句式节奏")

    # 基于 flagged 维度补充建议
    for d in ps_dims:
        if d["flagged"] and d["name"] not in ("禁用词扣分",):
            if d["name"] not in [a.get("_dim") for a in alerts]:
                suggestions.append(
                    f"关注「{d['name']}」维度 (得分 {d['score']:.0f})"
                )

    return alerts, suggestions


# ============================================================
# 基线映射
# ============================================================

def _apply_baseline(ref_profile: dict, baseline: dict) -> dict:
    """将风格 JSON 的 quantitative 字段映射到 analyze_text 输出格式。"""
    q = baseline.get("quantitative", {})
    if not q:
        return ref_profile

    sl = q.get("sentence_length", {})
    if sl.get("mean"):
        ref_profile["sentence_stats"] = {
            "mean": sl.get("mean", ref_profile.get("sentence_stats", {}).get("mean", 0)),
            "std": sl.get("std", ref_profile.get("sentence_stats", {}).get("std", 0)),
            "min": sl.get("min", 2),
            "max": sl.get("max", 90),
            "median": sl.get("mean", 15),
            "count": ref_profile.get("sentence_stats", {}).get("count", 100),
        }

    dr = q.get("dialogue_ratio", {})
    if isinstance(dr, dict):
        val = dr.get("mean") or dr.get("overall") or dr.get("early")
        if val is not None:
            ref_profile["dialogue_ratio"] = val
    elif isinstance(dr, (int, float)):
        ref_profile["dialogue_ratio"] = dr

    cw = q.get("chapter_words", {})
    if cw.get("mean"):
        ref_profile["total_chinese_chars"] = int(cw["mean"])

    pl = q.get("paragraph_length", {})
    if pl.get("mean_sentences"):
        ref_profile["para_sentence_stats"] = {
            "mean": pl["mean_sentences"],
            "std": pl.get("std", 2),
            "min": 1, "max": 10, "median": pl["mean_sentences"],
            "count": ref_profile.get("paragraph_count", 100),
        }

    # 工具：把 baseline 中 {"mean": x, "std": y} 形态压平为 float（取 mean）
    def _flatten_mean(v):
        if isinstance(v, dict):
            return v.get("mean", v.get("value", 0.0))
        return v

    punc = q.get("punctuation_density_per_1000", {})
    if punc:
        rp = ref_profile.get("punctuation_density_per_1000", {})
        for k in ["comma", "period", "comma_period_ratio", "ellipsis",
                  "exclamation", "question", "dash"]:
            if k in punc:
                rp[k] = _flatten_mean(punc[k])
        ref_profile["punctuation_density_per_1000"] = rp

    fw = q.get("function_word_fingerprint_per_1000", {})
    if fw:
        ref_profile["function_word_fingerprint_per_1000"] = {
            k: _flatten_mean(v) for k, v in fw.items()
        }

    # 从 style_profile 中提取额外约束
    sp = baseline.get("style_profile", {})
    desc = sp.get("description", {})
    if desc.get("psychology_ratio") is not None:
        pass  # reserved for future use

    # 从 must_have 中提取极短段目标
    mh = baseline.get("must_have_per_chapter", {})
    if isinstance(mh, dict):
        ultra = mh.get("ultra_short_para_ratio_max")
        if ultra is not None:
            ref_profile["ultra_short_para_ratio"] = ultra
        single = mh.get("single_sentence_para_ratio_max")
        if single is not None:
            ref_profile["single_sentence_para_ratio"] = single

    # 用 writing_rules 中的量化目标覆盖（如有）
    pl_data = q.get("paragraph_length", {})
    if isinstance(pl_data, dict) and "single_sentence_ratio" in pl_data:
        ref_profile["single_sentence_para_ratio"] = pl_data["single_sentence_ratio"]

    # 设定合理的极短段基线（原作者约 12-22%）
    if ref_profile.get("ultra_short_para_ratio", 0) > 0.40:
        ref_profile["ultra_short_para_ratio"] = 0.17

    return ref_profile


# ============================================================
# L3a：滑窗 burstiness + 去题材 SFS（2026-05-30 · 北极星①⑤⑥）
# ------------------------------------------------------------
# 根因（memory reference-system-validation-method）：旧 SFS 把整 cluster 当**一个**
# 向量比 → 长文里局部段长崩塌被均值抹平（实测蛊真人单章 B 级 / cluster 级仅 D 级）；
# 且名词/人名/情节动词等**题材信号**淹没了真正的风格信号（虚词/标点/句长节奏）。
#
# 升级（全在本文件内自包含 · 不改 style_analyzer · L1a 在那边改避免冲突）：
#   (a) 滑窗 burstiness：每 ~1500-2000 CJK 一窗，逐窗算句长/段长/功能词指纹 →
#       输出窗间方差（burstiness）。方差过低 = AI 腔均匀化。并**定位最崩窗**
#       （索引 + 指标），逐窗逐维可读不黑箱。
#   (b) 去题材 SFS：cluster 级风格比只看虚词/标点/句长节奏（可加 POS 若 jieba.posseg
#       可用），对名词/人名/情节动词**停用**（jieba 停用；不可用则用功能词白名单）。
#
# 影子并行（北极星纪律 2）：env L3A_BURSTINESS_MODE 控制——
#   · shadow（默认）：算 L3a 全量指标，挂在 report 的 l3a_* 加项里**只记录不改判决**，
#     不动 sfs_quick / programmatic_score / grade（零回归）。新旧分歧写 stderr。
#   · active：同样计算并附加，但额外把 L3a 发现的崩塌窗 / 低 burstiness 升为 advisory
#     issue（gate_level 永远 advisory · 绝不进 hard_gate · 顾问非法官）。
#   · off：完全不算 L3a（纯旧 SFS 行为）。
# 不论哪种模式，产出的所有 issue gate_level 强制 = advisory（北极星⑤）。
# ============================================================

# 滑窗目标 CJK 字数（窗口下/上界 · 与 CLAUDE.md「每 1500-2000 字一窗」对齐）
_L3A_WINDOW_TARGET = 1750
_L3A_WINDOW_MIN = 1500
_L3A_WINDOW_MAX = 2000

# jieba.posseg 可用性探测（零依赖纪律：不可用则降级到功能词白名单 · 不报错不下载模型）
try:
    import jieba  # noqa: E402
    import jieba.posseg as _pseg  # noqa: E402
    jieba.setLogLevel(20)
    _JIEBA_AVAILABLE = True
except Exception:  # pragma: no cover - 环境无 jieba 时降级
    _pseg = None
    _JIEBA_AVAILABLE = False

# 去题材 POS 白名单：保留虚词/结构性词（风格信号），停用内容词（题材信号）。
# jieba flag 头字母：
#   r=代词 p=介词 c=连词 u/ul/uj/ud/uv/uz=助词 d=副词 e=叹词 y=语气词 o=拟声 x=标点
# → 这些是**作者风格指纹**（不随题材变）。停用：n*=名词/人名/地名/机构、v*=动词、
#   a=形容词、m=数词、i=成语、l=习用语、t=时间、s=处所、nz=专名 等内容词（携带题材）。
_L3A_STYLE_POS_PREFIXES = ("r", "p", "c", "u", "d", "e", "y", "o")
# x（标点）单列：标点节奏由 punctuation 指纹单独刻画，POS 分布里排除避免双算。


def _l3a_burstiness_mode() -> str:
    """L3A_BURSTINESS_MODE：默认 active（2026-05-31 放量·n_windows≥5门控防小尺寸误判·全advisory）· 非法回退 active · {shadow,off} 原样。"""
    import os
    m = (os.environ.get("L3A_BURSTINESS_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def _split_windows(text: str, target: int = _L3A_WINDOW_TARGET,
                   win_min: int = _L3A_WINDOW_MIN,
                   win_max: int = _L3A_WINDOW_MAX) -> list[str]:
    """按 CJK 字数把整段文本切成 ~target 字的滑窗（在段落边界处切，不切碎句子）。

    逐段累积，达 target 即收一窗（不超过 win_max）。末窗若 < win_min 且已有 ≥1 窗，
    并入前一窗（避免尾巴小窗污染方差）。返回窗口文本列表（保留段内换行）。
    单段超长（> win_max）也独立成窗（不强拆，保段落完整 = 风格单元）。"""
    paras = split_paragraphs(text)
    if not paras:
        return []
    windows: list[str] = []
    buf: list[str] = []
    buf_cjk = 0
    for p in paras:
        plen = count_chinese(p)
        if buf and buf_cjk + plen > win_max:
            windows.append("\n".join(buf))
            buf, buf_cjk = [], 0
        buf.append(p)
        buf_cjk += plen
        if buf_cjk >= target:
            windows.append("\n".join(buf))
            buf, buf_cjk = [], 0
    if buf:
        tail = "\n".join(buf)
        if count_chinese(tail) < win_min and windows:
            windows[-1] = windows[-1] + "\n" + tail
        else:
            windows.append(tail)
    return windows


def _window_metrics(win_text: str) -> dict:
    """单窗风格指标（只取与题材无关的节奏/结构维度 · 逐维可读）。"""
    sents = split_sentences(win_text)
    sent_lens = [count_chinese(s) for s in sents]
    paras = split_paragraphs(win_text)
    para_lens = [count_chinese(p) for p in paras]
    s_stats = calc_stats([float(x) for x in sent_lens])
    p_stats = calc_stats([float(x) for x in para_lens])
    cjk = count_chinese(win_text) or 1
    per_1000 = 1000.0 / cjk
    # 单句成段率（爽文节奏的关键节拍 · 崩塌时常先垮）
    single = 0
    for p in paras:
        if len(split_sentences(p)) <= 1:
            single += 1
    return {
        "cjk_chars": count_chinese(win_text),
        "sentence_mean_len": s_stats["mean"],
        "sentence_std": s_stats["std"],
        "paragraph_mean_len": p_stats["mean"],
        "single_sentence_para_ratio": round(single / len(paras), 4) if paras else 0.0,
        "comma_per_1000": round(len(COMMA_PATTERN.findall(win_text)) * per_1000, 2),
        "function_word_per_1000": {
            w: round(win_text.count(w) * per_1000, 2) for w in FUNCTION_WORDS
        },
    }


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def compute_burstiness(text: str) -> dict:
    """滑窗 burstiness：逐窗算句长/段长/单句成段率/逗号密度 → 窗间方差 + 定位最崩窗。

    burstiness 高 = 节奏有起伏（真人写作）；过低 = AI 腔把每窗摊成一个模子（均匀化）。
    「最崩窗」= 段均长偏离全窗中位数最大、且偏**低**方向的窗（局部段长崩塌——长文里被
    cluster 均值抹平的真痛点），逐窗逐维列出指标（advisory 可读，不黑箱判决）。"""
    windows = _split_windows(text)
    if len(windows) < 2:
        # 单窗（短文/单章）无法算窗间方差——返回 n_windows 让调用方知道不适用。
        wm = [_window_metrics(windows[0])] if windows else []
        return {
            "n_windows": len(windows),
            "applicable": False,
            "reason": "窗口数 < 2（文本太短，cluster 级 burstiness 不适用）",
            "windows": wm,
        }
    metrics = [_window_metrics(w) for w in windows]
    sent_means = [m["sentence_mean_len"] for m in metrics]
    para_means = [m["paragraph_mean_len"] for m in metrics]
    single_ratios = [m["single_sentence_para_ratio"] for m in metrics]
    comma_dens = [m["comma_per_1000"] for m in metrics]

    burst = {
        "sentence_mean_len": round(_variance(sent_means), 4),
        "paragraph_mean_len": round(_variance(para_means), 4),
        "single_sentence_para_ratio": round(_variance(single_ratios), 6),
        "comma_per_1000": round(_variance(comma_dens), 4),
    }
    # 综合 burstiness：用变异系数（CV = std/mean）的均值，跨维度可比（不受量纲影响）。
    cvs = []
    for vals in (sent_means, para_means, single_ratios, comma_dens):
        m = sum(vals) / len(vals)
        if m > 1e-9:
            cvs.append(math.sqrt(_variance(vals)) / m)
    overall_cv = round(sum(cvs) / len(cvs), 4) if cvs else 0.0

    # 定位最崩窗：段均长偏离中位数最大且偏低（局部段长崩塌方向）。
    srt = sorted(para_means)
    n = len(srt)
    median_pm = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    worst_idx, worst_dev = 0, -1.0
    for i, pm in enumerate(para_means):
        dev = median_pm - pm  # 偏低为正（崩塌）
        if dev > worst_dev:
            worst_dev, worst_idx = dev, i

    return {
        "n_windows": len(windows),
        "applicable": True,
        "burstiness_variance": burst,
        "overall_burstiness_cv": overall_cv,
        "window_metrics": metrics,
        "median_paragraph_mean_len": round(median_pm, 2),
        "worst_window": {
            "index": worst_idx,
            "paragraph_mean_len": para_means[worst_idx],
            "deviation_below_median": round(worst_dev, 2),
            "metrics": metrics[worst_idx],
        },
    }


def _pos_style_distribution(text: str) -> dict:
    """去题材 POS 分布：只保留虚词/结构性 POS（风格信号），停用内容词（题材信号）。

    jieba.posseg 可用 → 按 flag 头字母过滤（保留 r/p/c/u/d/e/y/o，停 n*/v*/a/m/i/l/t/s…）。
    返回 {pos_flag: 占比}（在保留集合内归一化）。jieba 不可用 → 返回 {}（调用方降级到
    功能词白名单比对 · 守零依赖纪律不报错）。"""
    if not _JIEBA_AVAILABLE or _pseg is None:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for _w, flag in _pseg.cut(text):
        if not flag:
            continue
        head = flag[0]
        if head in _L3A_STYLE_POS_PREFIXES:
            counts[flag] = counts.get(flag, 0) + 1
            total += 1
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def compute_style_only_sfs(ref_text: str, gen_text: str) -> dict:
    """去题材风格 SFS：只比风格特征（虚词指纹 / 标点指纹 / 句长节奏 / 去题材 POS 分布），
    对名词/人名/情节动词停用——避免跨题材时题材信号淹没文风信号导致误判。

    四个子项余弦/匹配后等权平均（0~100）。POS 子项仅在 jieba 可用时计入（否则降级，
    剩三项重新等权）。逐项输出便于 advisory 可读。"""
    rp = analyze_text(ref_text)
    gp = analyze_text(gen_text)

    # ① 功能词指纹（虚词 · 去题材核心）
    fw_sim = max(_cosine_sim(
        rp.get("function_word_fingerprint_per_1000", {}),
        gp.get("function_word_fingerprint_per_1000", {})), 0.0)
    # ② 标点指纹（5 维节奏 · 与题材无关）
    punc_keys = ["comma", "period", "ellipsis", "exclamation", "question"]
    r_punc = {k: rp.get("punctuation_density_per_1000", {}).get(k, 0) for k in punc_keys}
    g_punc = {k: gp.get("punctuation_density_per_1000", {}).get(k, 0) for k in punc_keys}
    punc_sim = max(_cosine_sim(r_punc, g_punc), 0.0)
    # ③ 句长节奏（句长分布 JSD · 节拍而非内容）
    rhythm = _jsd_score(rp.get("sentence_length_distribution", {}),
                        gp.get("sentence_length_distribution", {}))
    # ④ 去题材 POS 分布（jieba 可用时）
    r_pos = _pos_style_distribution(ref_text)
    g_pos = _pos_style_distribution(gen_text)
    pos_available = bool(r_pos) and bool(g_pos)
    pos_sim = max(_cosine_sim(r_pos, g_pos), 0.0) if pos_available else None

    # 统一转 float（_jsd_score 经 scipy 返回 np.float64 · 显式 cast 让 subscores 纯 stdlib 可读）
    fw_sim, punc_sim, rhythm = float(fw_sim), float(punc_sim), float(rhythm)
    subscores = {
        "function_word_cosine": round(fw_sim * 100, 2),
        "punctuation_cosine": round(punc_sim * 100, 2),
        "sentence_rhythm_jsd": round(rhythm * 100, 2),
    }
    parts = [fw_sim, punc_sim, rhythm]
    if pos_sim is not None:
        pos_sim = float(pos_sim)
        subscores["detopic_pos_cosine"] = round(pos_sim * 100, 2)
        parts.append(pos_sim)
    total = round(sum(parts) / len(parts) * 100, 2)
    return {
        "style_only_sfs": total,
        "subscores": subscores,
        "jieba_pos_used": pos_available,
        "topic_stopwords_applied": True if pos_available else "function_word_whitelist_fallback",
    }


def compute_l3a(ref_text: str, gen_text: str) -> dict:
    """L3a 组合：滑窗 burstiness（gen）+ 去题材 SFS（ref vs gen）+ advisory issue 列表。

    issue gate_level 强制 = advisory（北极星⑤ · 绝不黑箱判决 / 绝不进 hard_gate）。
    阈值仅作 advisory 触发线（可读提示），不参与任何 PASS/FAIL 判决。"""
    burst = compute_burstiness(gen_text)
    style_sfs = compute_style_only_sfs(ref_text, gen_text)
    issues: list[dict] = []

    # advisory ①：窗间 burstiness 过低（AI 腔均匀化）——CV < 0.04 提示节奏被摊平。
    # 阈值校准（北极星纪律 3 矫枉过正金标准）：真作者实测 CV——蛊真人 0.06-0.11（议论体
    # 节奏平稳）/ 惊悚乐园 0.16-0.20（对话多方差大）；AI 完全均匀化 → CV≈0.0。取 0.04 阈值
    # 干净分开二者（真作者下界 0.06 远高于 0.04 → 绝不误判真作者 · 守金标准）。
    # 放量门控(2026-05-31 验证)：n_windows<5(约<10k CJK·边界小cluster)不出 low_burstiness
    # advisory——小尺寸下低方差议论体作者(蛊真人 ch1-3·4窗·CV=0.0337)会被误判，属「低方差
    # 作者+最小窗口数」尺寸伪影(目标域 13-20k/6窗+ CV≥0.06 不受影响·守金标准)。
    if (burst.get("applicable") and burst.get("n_windows", 0) >= 5
            and burst.get("overall_burstiness_cv", 1.0) < 0.04):
        issues.append({
            "code": "L3A_LOW_BURSTINESS",
            "gate_level": "advisory",
            "message": f"窗间节奏方差过低（CV={burst['overall_burstiness_cv']}）"
                       f"· 共 {burst['n_windows']} 窗 · 疑似 AI 腔均匀化，建议在某些窗加短句连发/段长起伏",
        })
    # advisory ②：最崩窗段长显著低于中位数（局部段长崩塌 · cluster 级被均值抹平的真痛点）。
    if burst.get("applicable"):
        ww = burst["worst_window"]
        if ww["deviation_below_median"] > max(8.0, burst["median_paragraph_mean_len"] * 0.35):
            issues.append({
                "code": "L3A_WINDOW_COLLAPSE",
                "gate_level": "advisory",
                "message": f"第 {ww['index']} 窗段均长 {ww['paragraph_mean_len']} 显著低于"
                           f"中位数 {burst['median_paragraph_mean_len']}（崩 {ww['deviation_below_median']}）"
                           f"· 该窗节奏可能局部崩塌，定位复查",
            })
    # advisory ③：去题材风格 SFS 偏低（虚词/标点/句长节奏与作者不符）。
    if style_sfs["style_only_sfs"] < 60.0:
        issues.append({
            "code": "L3A_STYLE_ONLY_LOW",
            "gate_level": "advisory",
            "message": f"去题材风格 SFS {style_sfs['style_only_sfs']}（虚词/标点/句长节奏）偏低"
                       f"· 子项 {style_sfs['subscores']}",
        })

    # 兜底：任何外部消费都不应把 L3a 当判决——显式标注。
    for it in issues:
        it["gate_level"] = "advisory"  # 强制（北极星⑤ · 双保险）

    return {
        "mode": _l3a_burstiness_mode(),
        "burstiness": burst,
        "style_only_sfs": style_sfs,
        "advisory_issues": issues,
        "note": "全部 advisory · 不改 sfs_quick/grade 判决 · 不进 hard_gate（顾问非法官）",
    }


# ============================================================
# L3e：SFS 评分消偏（2026-05-31 · 北极星①⑤⑥）
# ------------------------------------------------------------
# 根因（本批任务说明 · 实证）：
#   ① LLM-as-judge 有**顺序偏置**——同一对样本，谁先呈现谁占优，可致 >10% 漂移。
#   ② few-shot / 参考片段若按**内容相似**选片，反而降低风格保真（Catch Me 实证：内容近
#      的片段把模型往「抄内容」带，而非「学文风」）。应按**风格代表性**选片
#      （聚类质心 / 句式覆盖），让参考片段覆盖作者的句长节奏谱系而非贴近 gen 的题材。
#
# 升级（全在本文件内自包含 · 纯增量 · 不改 L3a 也不改旧 generate_llm_prompt/_sample_paragraphs）：
#   (a) generate_pairwise_llm_prompt：复刻稿 vs 作者原文片段做 **pairwise** 评分，且
#       **交换呈现顺序双跑**（A=原文先/复刻后，B=复刻先/原文后）。prompt 显式要求模型
#       对两种顺序各打一次分；average_pairwise_scores 取均值消顺序偏置。
#   (b) _select_representative_samples：参考片段按**风格代表性**选——对候选段算去题材
#       风格特征向量（句长节奏/标点/虚词/单句成段率），用 k-center（贪心最远点）+ 质心
#       覆盖选 n 段，最大化句式谱系覆盖，**不看与 gen 的内容相似**。
#
# 影子并行（北极星纪律 7 · 回归 0）：env SFS_LLM_DEBIAS 控制——
#   · off（默认）：generate_llm_prompt 行为完全不变（旧单序 + random.sample 选片）。
#   · on：generate_llm_prompt 转调 pairwise 双序 + 代表性选片（消偏增强版）。
# 不论哪种模式，本层只改**评分鲁棒性**（prompt 构造 / 选片 / 取均值），属 advisory：
#   绝不改 sfs_quick / programmatic_score / grade 任何确定性判决（顾问非法官 · 北极星⑤）。
# ============================================================


def _sfs_llm_debias_on() -> bool:
    """SFS_LLM_DEBIAS：默认 off（旧单序行为零回归）· 显式 {1,true,on,yes} 才开 pairwise 消偏。"""
    import os
    v = (os.environ.get("SFS_LLM_DEBIAS") or "").strip().lower()
    return v in ("1", "true", "on", "yes")


def _segment_style_feature(seg: str) -> list[float]:
    """单个候选片段的**去题材**风格特征向量（句长节奏/标点/虚词/单句成段率）。

    只取与题材无关的节奏/结构维度（与 L3a / compute_style_only_sfs 同哲学）——名词/人名/
    情节动词等题材信号一律不进向量。维度固定可比（同序），供 k-center / 质心选片用。"""
    sents = split_sentences(seg)
    sent_lens = [float(count_chinese(s)) for s in sents]
    paras = split_paragraphs(seg)
    s_stats = calc_stats(sent_lens)
    cjk = count_chinese(seg) or 1
    per_1000 = 1000.0 / cjk
    comma_d = len(COMMA_PATTERN.findall(seg)) * per_1000
    # 单句成段率（爽文节拍 · 崩塌时先垮 · 风格信号）
    single = sum(1 for p in paras if len(split_sentences(p)) <= 1)
    single_ratio = single / len(paras) if paras else 0.0
    # 功能词密度（虚词指纹 · 去题材核心）
    fw_vec = [seg.count(w) * per_1000 for w in FUNCTION_WORDS]
    return [
        s_stats["mean"],          # 句均长
        s_stats["std"],           # 句长波动（节奏起伏）
        comma_d,                  # 逗号密度（断句节奏）
        single_ratio * 100.0,     # 单句成段率（放大到与上面同量级）
        *fw_vec,                  # 15 维虚词指纹
    ]


def _l2_dist(a: list[float], b: list[float]) -> float:
    """两个等长向量的欧氏距离（纯 stdlib · 不依赖 numpy · 选片用）。"""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _candidate_segments(text: str, min_len: int = 200, max_len: int = 500) -> list[str]:
    """把文本切成 ~min_len-max_len CJK 的候选片段（复用 _sample_paragraphs 的合并/截断逻辑，
    但**返回全部候选**不抽样——交给代表性选片决定选哪些）。"""
    paras = split_paragraphs(text)
    chunks: list[str] = []
    buf = ""
    for p in paras:
        buf += p + "\n"
        if count_chinese(buf) >= min_len:
            chunks.append(buf.strip())
            buf = ""
    if buf.strip() and count_chinese(buf) >= min_len // 2:
        chunks.append(buf.strip())

    trimmed: list[str] = []
    for c in chunks:
        if count_chinese(c) > max_len:
            idx, cnt = 0, 0
            for idx, ch in enumerate(c):
                if re.match(r"[一-鿿]", ch):
                    cnt += 1
                if cnt >= max_len:
                    break
            c = c[:idx + 1] + "……"
        if count_chinese(c) >= min_len // 2:
            trimmed.append(c)
    return trimmed


def _select_representative_samples(text: str, n: int = 3,
                                   min_len: int = 200, max_len: int = 500) -> list[str]:
    """按**风格代表性**选 n 个参考片段（取代旧 _sample_paragraphs 的纯 random.sample）。

    算法（确定性 · 可复现 · 不看与 gen 的内容相似）：
      1. 切全部候选段 → 算每段去题材风格特征向量（_segment_style_feature）。
      2. 标准化各维度（除以该维标准差）让句长/虚词等不同量纲可比。
      3. 选第 1 个 = 最贴近**语料风格质心**的段（最具代表性的「典型句式」）。
      4. 后续每个 = 离已选集合**最远**的段（k-center 贪心 · 最大化句式谱系覆盖：
         长句段、短句连发段、对话节奏段都被覆盖到，而非全选同一种节奏）。
    质心打底 + 最远点扩展 = 既「典型」又「全谱系」，比随机选更代表作者风格分布。
    候选不足 n 个 → 全返回（按原文出现序，保可读）。"""
    cands = _candidate_segments(text, min_len, max_len)
    if not cands:
        return [text[:1500]] if text else []
    if len(cands) <= n:
        return cands

    feats = [_segment_style_feature(c) for c in cands]
    dim = len(feats[0])
    # 各维标准差（标准化 · 避免句长大数值主导欧氏距离）
    stds: list[float] = []
    for d in range(dim):
        col = [f[d] for f in feats]
        m = sum(col) / len(col)
        var = sum((x - m) ** 2 for x in col) / len(col)
        stds.append(math.sqrt(var) or 1.0)
    norm = [[v / stds[d] for d, v in enumerate(f)] for f in feats]

    # 语料风格质心
    centroid = [sum(f[d] for f in norm) / len(norm) for d in range(dim)]

    # 第 1 个 = 最贴质心（最典型）
    first = min(range(len(norm)), key=lambda i: _l2_dist(norm[i], centroid))
    selected = [first]
    # k-center 贪心：每次选离已选集合最远的（最大化句式谱系覆盖）
    while len(selected) < n:
        best_i, best_d = -1, -1.0
        for i in range(len(norm)):
            if i in selected:
                continue
            d_min = min(_l2_dist(norm[i], norm[j]) for j in selected)
            if d_min > best_d:
                best_d, best_i = d_min, i
        if best_i < 0:
            break
        selected.append(best_i)

    # 按原文出现序返回（可读性 · 不打乱阅读顺序）
    return [cands[i] for i in sorted(selected)]


# LLM 评分维度（pairwise 与旧单序共用 · 单一来源避免分歧）
_SFS_LLM_DIMS = [
    ("叙事结构", "叙事视角、场景转换、时间线处理的一致性"),
    ("对话风格", "角色对话的口语化程度、口癖保留、语气词使用"),
    ("情绪节奏", "紧张/舒缓的交替节奏、段落长短的节奏感"),
    ("角色声纹", "不同角色的语言辨识度、性格在对话中的体现"),
    ("章首章末", "开头吸引力、结尾悬念/余韵的处理手法"),
    ("反AI腔", "是否存在AI常见套话、机械化表达、缺少人味的句式"),
    ("招牌技法", "原作者独特的修辞手法、比喻风格、描写偏好"),
    ("信息密度", "每段传递的信息量、描写与叙事的比例平衡"),
]


def generate_pairwise_llm_prompt(ref_text: str, gen_text: str,
                                 n_samples: int = 3) -> str:
    """生成**消偏** LLM 评分 prompt：pairwise（复刻稿 vs 作者原文片段）+ **交换呈现顺序双跑**。

    消两类偏（任务根因）：
      ① 顺序偏置：同一对样本两种呈现顺序（A=原文先/复刻后，B=复刻先/原文后）各打一次分，
         prompt 显式要求两份打分 → average_pairwise_scores 取均值抵消「谁先谁占优」。
      ② 选片偏置：参考片段按**风格代表性**（_select_representative_samples · 质心+句式覆盖）
         选，不按与 gen 的内容相似选——避免内容近的片段把模型带去「抄内容」而非「学文风」。

    输出 JSON 要求两个键 order_A / order_B（各 8 维 1-10），由 average_pairwise_scores 合并。"""
    ref_samples = _select_representative_samples(ref_text, n_samples)
    gen_samples = _select_representative_samples(gen_text, n_samples)

    def _render_block(title: str, samples: list[str], label: str) -> list[str]:
        out = [f"## {title}", ""]
        for i, s in enumerate(samples, 1):
            out += [f"### {label} {i}", s, ""]
        return out

    L = ["# 风格保真度 LLM 评分（pairwise 消偏 · 顺序双跑）", "",
         "下面给出**作者原文片段**与**AI 复刻片段**。请做 pairwise 评分：判断 AI 复刻在多大",
         "程度上还原了作者的**写作风格**（句式节奏 / 用词 / 语气 / 技法），不是比内容是否相同。", "",
         "⚠️ 为消除呈现顺序对判断的偏置，请按**两种顺序各独立评分一次**，最后系统会取均值：", ""]

    # —— Order A：作者原文先，AI 复刻后 ——
    L += ["━━━━━━━━━━ 评分轮 A（先看作者原文，再看 AI 复刻）━━━━━━━━━━", ""]
    L += _render_block("作者原文片段（轮 A）", ref_samples, "原文段落")
    L += _render_block("AI 复刻片段（轮 A）", gen_samples, "复刻段落")

    # —— Order B：AI 复刻先，作者原文后（交换顺序）——
    L += ["", "━━━━━━━━━━ 评分轮 B（先看 AI 复刻，再看作者原文 · 顺序交换）━━━━━━━━━━", ""]
    L += _render_block("AI 复刻片段（轮 B）", gen_samples, "复刻段落")
    L += _render_block("作者原文片段（轮 B）", ref_samples, "原文段落")

    L += ["## 评分维度（每维度 1-10 分 · 两轮都要打）", ""]
    grades = [("9-10", "优秀", "高度一致，几乎无法区分"),
              ("7-8", "良好", "基本匹配，偶有偏差但不出戏"),
              ("5-6", "一般", "有明显差异，能感受到不是原作者"),
              ("3-4", "较差", "严重偏离，AI味明显"),
              ("1-2", "极差", "完全不匹配，像另一个作者")]
    for name, desc in _SFS_LLM_DIMS:
        L += [f"### {name}", f"说明：{desc}",
              "| 分数 | 等级 | 描述 |", "|------|------|------|"]
        for sc, lv, ds in grades:
            L.append(f"| {sc} | {lv} | {name}{ds} |")
        L.append("")

    L += ["## 输出格式", "请严格按以下 JSON 输出**两轮**评分（order_A 与 order_B）：",
          "```json", "{", '  "order_A": {']
    for i, (name, _) in enumerate(_SFS_LLM_DIMS):
        comma = "," if i < len(_SFS_LLM_DIMS) - 1 else ""
        L.append(f'    "{name}": {{"score": <1-10>, "reason": "<一句话理由>"}}{comma}')
    L += ["  },", '  "order_B": {']
    for i, (name, _) in enumerate(_SFS_LLM_DIMS):
        comma = "," if i < len(_SFS_LLM_DIMS) - 1 else ""
        L.append(f'    "{name}": {{"score": <1-10>, "reason": "<一句话理由>"}}{comma}')
    L += ["  }", "}", "```", "",
          "注意：order_A 与 order_B 是同一对样本、仅呈现顺序不同，分数应接近；",
          "系统会对两轮取均值作为最终风格保真分，以抵消呈现顺序带来的偏置。"]
    return "\n".join(L)


def average_pairwise_scores(order_a: dict, order_b: dict) -> dict:
    """合并 pairwise 双序评分：逐维取两轮均值消顺序偏置，并报告顺序偏置幅度（advisory）。

    入参为 LLM 返回的 order_A / order_B（{维度: {"score": x, "reason": ...}} 或 {维度: x}）。
    返回 {dimensions: {维度: 均值}, mean_score, order_bias: {per_dim, max_abs, mean_abs}}。
    order_bias 只是**可读诊断**（顺序偏置有多大）· 不改任何 hard 判决（北极星⑤ advisory）。"""
    def _score(v):
        if isinstance(v, dict):
            return float(v.get("score", 0))
        if isinstance(v, (int, float)):
            return float(v)
        return 0.0

    dims = [n for n, _ in _SFS_LLM_DIMS]
    keys = dims if all(d in order_a and d in order_b for d in dims) \
        else sorted(set(order_a) & set(order_b))

    merged: dict[str, float] = {}
    per_dim_bias: dict[str, float] = {}
    for k in keys:
        a, b = _score(order_a.get(k)), _score(order_b.get(k))
        merged[k] = round((a + b) / 2.0, 4)
        per_dim_bias[k] = round(abs(a - b), 4)

    mean_score = round(sum(merged.values()) / len(merged), 4) if merged else 0.0
    bias_vals = list(per_dim_bias.values())
    return {
        "dimensions": merged,
        "mean_score": mean_score,
        "order_bias": {
            "per_dim": per_dim_bias,
            "max_abs": round(max(bias_vals), 4) if bias_vals else 0.0,
            "mean_abs": round(sum(bias_vals) / len(bias_vals), 4) if bias_vals else 0.0,
        },
        "note": "已对两呈现顺序取均值消偏 · order_bias 仅诊断 advisory · 不改 sfs_quick/grade 判决",
    }


# ============================================================
# 主流程
# ============================================================

def evaluate(ref_text, gen_text: str,
             baseline: dict | None = None,
             has_author_profile: bool | None = None) -> dict:
    """执行完整 SFS 评估，返回结构化报告。

    v2 (E5)：ref_text 支持 str（单基线）或 list[str]（多基线，子型区间评分）。

    has_author_profile（2026-05-30 修 #4）：本项目是否有作者风格档。None=自动推断
    （提供 baseline 风格 JSON 即视为有作者档，对齐 validate_style._apply_style_overrides
    在应用风格 JSON 时置 _has_author_profile=True 的逻辑）。有作者档时维度7工艺签名词
    改对照 ref 频率而非单边硬扣（守原则①贴合作者风格）。
    """
    # 标准化为列表
    if isinstance(ref_text, str):
        ref_texts = [ref_text]
    else:
        ref_texts = list(ref_text)
    if not ref_texts:
        raise ValueError("ref_text must contain at least one sample")

    # 缓存文本供段落多样性计算（用第一个 ref）
    _TEXT_CACHE["ref"] = ref_texts[0]
    _TEXT_CACHE["gen"] = gen_text

    ref_profiles = [analyze_text(t) for t in ref_texts]
    gen_profile = analyze_text(gen_text)

    if len(ref_profiles) == 1:
        ref_profile = ref_profiles[0]
    else:
        ref_profile = _build_interval_profile(ref_profiles)

    # 如果提供了 baseline（风格 JSON），映射其字段到 analyze_text 格式
    if baseline:
        ref_profile = _apply_baseline(ref_profile, baseline)

    # 自动推断：提供了 baseline 风格 JSON 即视为有作者风格档
    if has_author_profile is None:
        has_author_profile = baseline is not None

    ps = compute_programmatic_score(ref_profile, gen_profile, gen_text,
                                    has_author_profile=has_author_profile)
    alerts, suggestions = generate_alerts(ref_profile, gen_profile,
                                          ps["dimensions"],
                                          has_author_profile=has_author_profile)

    # 同时调用 style_analyzer 的 compare_profiles 作为补充
    # 注意 compare_profiles 不支持区间 profile，传第一个 ref
    sa_comparison = compare_profiles(ref_profiles[0], gen_profile)

    report = {
        "sfs_quick": ps["total"],
        "programmatic_score": ps,
        "style_analyzer_comparison": {
            "weighted_score": sa_comparison.get("weighted_score"),
            "grade": sa_comparison.get("grade"),
            "flagged_count": sa_comparison.get("flagged_count"),
        },
        "style_alerts": alerts,
        "improvement_suggestions": suggestions,
        "multi_baseline": len(ref_profiles) > 1,
        "ref_count": len(ref_profiles),
        "has_author_profile": bool(has_author_profile),
    }

    # L3a 滑窗 burstiness + 去题材 SFS（影子并行 · 北极星①⑤⑥）。
    # shadow（默认）/active：计算并**附加**到 report（加项 · 不改上面任何判决字段，零回归）。
    # off：完全不算。active 与 shadow 的唯一区别：active 把 advisory issue 升到 report 顶层
    # advisory_issues 供消费方看见（仍 advisory · 绝不改 sfs_quick/grade / 绝不进 hard_gate）。
    l3a_mode = _l3a_burstiness_mode()
    if l3a_mode != "off":
        # gen 是 cluster 草稿 → 滑窗看 gen；去题材 SFS 用第一个 ref（与 prompt_ref 一致）。
        l3a = compute_l3a(ref_texts[0], gen_text)
        report["l3a"] = l3a
        if l3a_mode == "shadow":
            # 影子：分歧只写 stderr，不改判决也不把 issue 提到顶层（保默认零回归）。
            if l3a.get("advisory_issues"):
                codes = [i["code"] for i in l3a["advisory_issues"]]
                print(f"[L3a shadow] 检出 {len(codes)} 条 advisory（未改判决）: {codes}",
                      file=sys.stderr)
        elif l3a_mode == "active":
            # active：advisory issue 升顶层（消费方可见）· gate_level 永远 advisory。
            report["advisory_issues"] = l3a.get("advisory_issues", [])
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SFS 风格保真度评分器 — 对比原文与 AI 生成文本的风格匹配度"
    )
    parser.add_argument("--ref", required=False, action="append", default=[],
                        help="原文文件或目录路径（可多次指定 --ref A.txt --ref B.txt 启用多基线区间评分）")
    parser.add_argument("--gen", required=True,
                        help="AI 生成文件或目录路径")
    parser.add_argument("--baseline", default=None,
                        help="风格基线 JSON 文件（可选，覆盖 ref 分析结果）")
    parser.add_argument("--output", default=None,
                        help="报告输出路径（默认输出到 stdout）")
    # v23.13（2026-05-27）多基线自动抽样：解决「单 ref 评分对短段独白章误判过重」
    # 用例：复刻独白章 vs ref 对话章 → 单 ref 模式扣分严重 →
    # 自动抽 N 章混合章型 ref → 区间评分 → 落入任一章型带内得 1.0
    parser.add_argument("--multi-ref-from-dir", default=None,
                        help="风格库原文目录（如 workspace/styles/蛊真人/原文）→ 自动抽 N 章混合章型 ref")
    parser.add_argument("--multi-ref-count", type=int, default=5,
                        help="--multi-ref-from-dir 抽样数（默认 5）")
    parser.add_argument("--multi-ref-seed", type=int, default=42,
                        help="抽样随机种子（默认 42 · 保证可复现）")
    # 2026-05-29 修：distill-style.plan.json phase-2 调用形如
    # `style_evaluator.py --mode cluster --multi-ref-from-dir SFS`，但本脚本只做 SFS 评分
    # （cluster 6 维评分实际由独立的 cluster_evaluator.py 做）。原先没有 --mode 参数 →
    # argparse exit 2 崩溃拦死 plan。这里加一个无害的 --mode：默认 sfs 行为不变，接受 cluster
    # 不报错（仅作语义标注），让 plan 文档照跑不崩。
    parser.add_argument("--mode", choices=["sfs", "cluster"], default="sfs",
                        help="评分模式标注（无害参数）：sfs=默认 SFS 评分；cluster=plan phase-2 "
                             "cluster 视野调用兼容（行为同 sfs · cluster 6 维由 cluster_evaluator.py 负责）")
    # 2026-05-30 北极星①修 #4：作者风格档优先。默认 None=自动推断（有 --baseline 即视为有作者档）。
    # 显式 --has-author-profile / --no-author-profile 可覆盖。有作者档时维度7工艺签名词
    # （顿时/淡淡/显然…）对照 ref 频率而非单边硬扣 → 忠实复刻高频签名词的作者不再被扣分。
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--has-author-profile", dest="has_author_profile",
                     action="store_true", default=None,
                     help="强制视为有作者风格档（维度7工艺签名词对照 ref 频率不单边硬扣）")
    grp.add_argument("--no-author-profile", dest="has_author_profile",
                     action="store_false",
                     help="强制视为无作者风格档（维度7禁用词全集单边硬扣 · 通用反 AI 腔兜底）")
    args = parser.parse_args()

    gen_path = Path(args.gen)

    # v23.13 自动抽样多基线（如果指定 --multi-ref-from-dir）
    if args.multi_ref_from_dir:
        ref_dir = Path(args.multi_ref_from_dir)
        if not ref_dir.is_dir():
            print(f"[错误] --multi-ref-from-dir 不存在: {ref_dir}", file=sys.stderr)
            sys.exit(1)
        all_files = sorted(ref_dir.glob("第*章.txt"))
        if not all_files:
            all_files = sorted(ref_dir.glob("*.txt"))
        if len(all_files) < args.multi_ref_count:
            print(f"[警告] 目录仅 {len(all_files)} 章 < 抽样数 {args.multi_ref_count}，全用",
                  file=sys.stderr)
            sampled = all_files
        else:
            import random
            rng = random.Random(args.multi_ref_seed)
            sampled = rng.sample(all_files, args.multi_ref_count)
        args.ref.extend(str(f) for f in sampled)
        print(f"[multi-ref] 自动抽 {len(sampled)} 章混合章型 ref: "
              f"{[f.name for f in sampled]}", file=sys.stderr)

    if not args.ref:
        print("[错误] 必须指定 --ref 或 --multi-ref-from-dir", file=sys.stderr)
        sys.exit(1)

    # E5：多 ref 支持
    ref_texts_list = _read_ref_texts(args.ref)
    gen_text = _read_texts(gen_path)
    if len(ref_texts_list) == 1:
        ref_text = ref_texts_list[0]
    else:
        ref_text = ref_texts_list  # 传 list 给 evaluate

    baseline = None
    if args.baseline:
        bp = Path(args.baseline)
        if bp.exists():
            baseline = json.loads(bp.read_text(encoding="utf-8"))
        else:
            print(f"[警告] baseline 文件不存在: {bp}", file=sys.stderr)

    report = evaluate(ref_text, gen_text, baseline,
                      has_author_profile=args.has_author_profile)

    # 生成 LLM prompt 文件（多基线时取第一段作为 prompt 范例）
    prompt_ref = ref_text if isinstance(ref_text, str) else ref_text[0]
    llm_prompt = generate_llm_prompt(prompt_ref, gen_text)
    prompt_filename = (args.output or "sfs_report").replace(".json", "")
    prompt_path = Path(f"{prompt_filename}_llm_eval_prompt.txt")
    prompt_path.write_text(llm_prompt, encoding="utf-8")
    report["llm_prompt_file"] = str(prompt_path)
    print(f"[LLM prompt 已保存] {prompt_path}", file=sys.stderr)

    # 输出报告（使用自定义编码器处理 numpy 类型）
    json_str = json.dumps(report, ensure_ascii=False, indent=2, cls=_NumpyEncoder)
    if args.output:
        out = Path(args.output)
        out.write_text(json_str, encoding="utf-8")
        print(f"[报告已保存] {out}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
