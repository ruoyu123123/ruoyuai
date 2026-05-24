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
    count_chinese,
    split_paragraphs,
    CHINESE_CHAR,
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


def compute_programmatic_score(ref_profile: dict, gen_profile: dict,
                               gen_text: str) -> dict:
    """
    计算 12 维程序化评分，总权重 55%（内部归一化到 100 分制）。
    返回 {"total": float, "dimensions": [...], "grade": str}。

    v2 (E5)：支持 ref_profile 是区间 profile（含 _source_profiles 列表）。
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
    """生成 LLM 评分 prompt 文本。"""
    ref_samples = _sample_paragraphs(ref_text, 3)
    gen_samples = _sample_paragraphs(gen_text, 3)

    dims = [
        ("叙事结构", "叙事视角、场景转换、时间线处理的一致性"),
        ("对话风格", "角色对话的口语化程度、口癖保留、语气词使用"),
        ("情绪节奏", "紧张/舒缓的交替节奏、段落长短的节奏感"),
        ("角色声纹", "不同角色的语言辨识度、性格在对话中的体现"),
        ("章首章末", "开头吸引力、结尾悬念/余韵的处理手法"),
        ("反AI腔", "是否存在AI常见套话、机械化表达、缺少人味的句式"),
        ("招牌技法", "原作者独特的修辞手法、比喻风格、描写偏好"),
        ("信息密度", "每段传递的信息量、描写与叙事的比例平衡"),
    ]
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
                    ps_dims: list[dict]) -> tuple[list[dict], list[str]]:
    """生成 style_alerts 和 improvement_suggestions。

    v2 (E5)：兼容区间 profile（先转标量）。
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
# 主流程
# ============================================================

def evaluate(ref_text, gen_text: str,
             baseline: dict | None = None) -> dict:
    """执行完整 SFS 评估，返回结构化报告。

    v2 (E5)：ref_text 支持 str（单基线）或 list[str]（多基线，子型区间评分）。
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

    ps = compute_programmatic_score(ref_profile, gen_profile, gen_text)
    alerts, suggestions = generate_alerts(ref_profile, gen_profile,
                                          ps["dimensions"])

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
    }
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SFS 风格保真度评分器 — 对比原文与 AI 生成文本的风格匹配度"
    )
    parser.add_argument("--ref", required=True, action="append",
                        help="原文文件或目录路径（可多次指定 --ref A.txt --ref B.txt 启用多基线区间评分）")
    parser.add_argument("--gen", required=True,
                        help="AI 生成文件或目录路径")
    parser.add_argument("--baseline", default=None,
                        help="风格基线 JSON 文件（可选，覆盖 ref 分析结果）")
    parser.add_argument("--output", default=None,
                        help="报告输出路径（默认输出到 stdout）")
    args = parser.parse_args()

    gen_path = Path(args.gen)

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

    report = evaluate(ref_text, gen_text, baseline)

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
