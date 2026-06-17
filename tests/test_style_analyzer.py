"""style_analyzer.py 专属回归测试 — 锁核心确定性原语（2026-06-17）。

style_analyzer 此前只被间接覆盖，无专属 test 文件：
  · test_l1a_quantile_band.py   → calc_quantiles / chapter_metrics_lite / aggregate_chapter_quantiles
  · test_narrative_seq.py       → narrative_function_raw_scores / _pstdev / score_narrative_function_sequence
                                  / narrative_seq_mode / analyze_text 的 narrative 注入
  · test_style_evaluator_banned.py / test_function_word_fingerprint_scanner.py
                                  → BANNED_WORDS / QUOTA_WORDS / FUNCTION_WORDS 常量
  · test_ttr_fidelity.py        → vocabulary_richness 字段存在

本文件**聚焦上述测试未覆盖的剩余确定性核心算法**，互不重叠：
  [A] 文本切分原语：count_chinese / split_sentences / split_paragraphs
  [B] 统计原语：calc_stats（奇偶中位数 / 边界）/ length_distribution（分桶边界）
  [C] 对话比 calc_dialogue_ratio（中英文引号 / 无引号 / 无 CJK）
  [D] 拟声段识别 is_onomatopoeia（白名单 + 破折号 + 反例）
  [E] 人名过滤 _is_valid_name（长度 / 代词 / 高频词 / 功能词分支）
  [F] 比对打分：cosine_similarity / pct_diff / compare_profiles（同档=A·罚分·flag）
  [G] 对话 vs 引号化独白 _split_dialogue_vs_monologue
  [H] 说话人分类 classify_speakers（registry 划分不变量 + 真名检出）

纯标准库 · 无参数 test_* · 确定性（不调 LLM / 不联网）· 真 import 真调用。
"""
import sys
import math
import pathlib

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts")
)
import style_analyzer as mod  # noqa: E402


# ════════════════════════════════════════════════════════════════
# [A] 文本切分原语
# ════════════════════════════════════════════════════════════════

def test_count_chinese_counts_only_cjk():
    """count_chinese 只数 CJK 汉字，忽略 ASCII / 数字 / 标点。"""
    assert mod.count_chinese("abc中文123文！") == 3
    assert mod.count_chinese("") == 0
    assert mod.count_chinese("123 abc") == 0


def test_split_sentences_drops_empty_and_nocjk():
    """按句末标点切 · 丢空段 · 丢无 CJK 段 · strip 两端。"""
    s = mod.split_sentences("他来了。她走了！谁知道？")
    assert s == ["他来了", "她走了", "谁知道"], s
    # 尾随空白 / 无 CJK 片段不计入
    assert mod.split_sentences("纯标点。。。   ") == ["纯标点"]
    assert mod.split_sentences("") == []
    assert mod.split_sentences("123 abc") == []


def test_split_paragraphs_drops_blank_and_nocjk_lines():
    """按行切 · 丢空行 / 纯空白行 / 无 CJK 行 · strip。"""
    paras = mod.split_paragraphs("第一段\n  \nabc\n第二段。")
    assert paras == ["第一段", "第二段。"], paras
    assert len(mod.split_paragraphs("甲\n乙\n丙")) == 3


# ════════════════════════════════════════════════════════════════
# [B] 统计原语：calc_stats / length_distribution
# ════════════════════════════════════════════════════════════════

def test_calc_stats_odd_median():
    """奇数个值 → 中位数取中间值 · mean/std 正确。"""
    s = mod.calc_stats([1.0, 2.0, 3.0])
    assert s["mean"] == 2.0
    assert s["median"] == 2.0
    assert s["min"] == 1.0 and s["max"] == 3.0
    assert s["count"] == 3
    # 总体标准差 sqrt(2/3) ≈ 0.816 → round(0.82)
    assert s["std"] == 0.82, s


def test_calc_stats_even_median_is_average_of_two_middle():
    """偶数个值 → 中位数取中间两值平均。"""
    s = mod.calc_stats([1.0, 2.0, 3.0, 4.0])
    assert s["mean"] == 2.5
    assert s["median"] == 2.5, s
    assert s["count"] == 4


def test_calc_stats_empty_and_single():
    """空集 → 全 0 / count=0 · 单值 → std=0 · median=该值。"""
    e = mod.calc_stats([])
    assert e == {"mean": 0, "std": 0, "min": 0, "max": 0, "median": 0, "count": 0}
    one = mod.calc_stats([5.0])
    assert one["std"] == 0.0 and one["median"] == 5.0 and one["count"] == 1


def test_length_distribution_bin_boundaries():
    """分桶边界：≤5 / 6-15 / 16-30 / 31-50 / >50 · 比例之和=1。"""
    # 每桶恰 1 个边界值 + 1 个桶内值 → 各桶 2 个，共 8 → 每桶 0.25... 用单边界值精确锁
    d = mod.length_distribution([5, 6, 15, 16, 30, 31, 50, 51])
    assert d["le5"] == 0.125      # 5
    assert d["6to15"] == 0.25     # 6,15
    assert d["16to30"] == 0.25    # 16,30
    assert d["31to50"] == 0.25    # 31,50
    assert d["gt50"] == 0.125     # 51
    assert abs(sum(d.values()) - 1.0) < 1e-9


def test_length_distribution_empty_no_div_zero():
    """空列表 → 不除零（分母兜底 1）· 全 0。"""
    d = mod.length_distribution([])
    assert all(v == 0.0 for v in d.values()), d


# ════════════════════════════════════════════════════════════════
# [C] 对话比
# ════════════════════════════════════════════════════════════════

def test_calc_dialogue_ratio_chinese_quotes():
    """中文弯引号内 CJK 字数 / 总 CJK 字数。"""
    # 引号内 “你好世界” = 4 字（句号不算）; 总 CJK = 11 (“你好世界”4 + “他说然后离开了”7)
    r = mod.calc_dialogue_ratio("他说：“你好世界。”然后离开了。")
    assert abs(r - 4 / 11) < 1e-6, r


def test_calc_dialogue_ratio_no_quotes_zero():
    """无引号 → 0.0 · 无 CJK → 0.0（不除零）。"""
    assert mod.calc_dialogue_ratio("纯叙述没有任何引号内容存在。") == 0.0
    assert mod.calc_dialogue_ratio("abc 123") == 0.0


def test_calc_dialogue_ratio_ascii_quotes_supported():
    """ASCII 双引号也被识别（v2 多引号支持）。"""
    r = mod.calc_dialogue_ratio('他说"你好"然后走了')
    # 引号内“你好”2 字 / 总 7 字（你好他说然后走了）
    assert r > 0, r


# ════════════════════════════════════════════════════════════════
# [D] 拟声段识别
# ════════════════════════════════════════════════════════════════

def test_is_onomatopoeia_whitelist_and_dash():
    """白名单拟声词独立段 + 破折号格式（轰——）判 True。"""
    assert mod.is_onomatopoeia("砰！") is True
    assert mod.is_onomatopoeia("咔嚓") is True
    assert mod.is_onomatopoeia("轰——！") is True   # ONOMATOPOEIA_DASH


def test_is_onomatopoeia_negatives():
    """普通叙述段 / 空段 → False（不豁免）。"""
    assert mod.is_onomatopoeia("他走了过来，看着远方。") is False
    assert mod.is_onomatopoeia("   ") is False
    assert mod.is_onomatopoeia("") is False


# ════════════════════════════════════════════════════════════════
# [E] 人名过滤 _is_valid_name
# ════════════════════════════════════════════════════════════════

def test_is_valid_name_accepts_real_names():
    """合法 2-3 字人名（含洋名）通过。"""
    assert mod._is_valid_name("李明") is True
    assert mod._is_valid_name("海纳斯") is True


def test_is_valid_name_rejects_bad_candidates():
    """长度越界 / 代词 / 高频非名词 / 含功能词 → 拒。"""
    assert mod._is_valid_name("a") is False           # 长度<2
    assert mod._is_valid_name("一二三四") is False     # 长度>3
    assert mod._is_valid_name("他们") is False         # 首字代词
    assert mod._is_valid_name("自己") is False         # _COMMON_NON_NAMES
    assert mod._is_valid_name("的人") is False         # 含功能词「的」
    assert mod._is_valid_name("") is False
    assert mod._is_valid_name("看见") is False         # 首字高频动词「看」


# ════════════════════════════════════════════════════════════════
# [F] 比对打分：cosine_similarity / pct_diff / compare_profiles
# ════════════════════════════════════════════════════════════════

def test_cosine_similarity_identical_orthogonal_zero():
    """相同向量≈1 · 正交向量=0 · 含零向量=0（防除零）。"""
    assert abs(mod.cosine_similarity({"a": 1, "b": 2}, {"a": 1, "b": 2}) - 1.0) < 1e-9
    assert mod.cosine_similarity({"a": 1, "b": 0}, {"a": 0, "b": 1}) == 0.0
    assert mod.cosine_similarity({"a": 0}, {"b": 0}) == 0.0


def test_pct_diff_relative_and_zero_floor():
    """相对差百分比 · 相等=0 · 同为 0 不除零（底 0.01 兜底）。"""
    assert mod.pct_diff(10, 10) == 0.0
    assert mod.pct_diff(10, 20) == 50.0      # |10-20|/20*100
    assert mod.pct_diff(0, 0) == 0.0


def test_compare_profiles_identical_is_grade_A():
    """同一 profile 自比 → final 100 · grade A · 0 flag · 17 维。"""
    prof = mod.analyze_text(
        "他得到了宝物，突破到新境界。\n“真不错。”他笑道。\n他走在路上，看着天空。"
    )
    cmp = mod.compare_profiles(prof, prof)
    assert cmp["final_programmatic_score"] == 100.0, cmp["final_programmatic_score"]
    assert cmp["grade"] == "A"
    assert cmp["flagged_count"] == 0
    assert cmp["total_dimensions"] == 17, cmp["total_dimensions"]


def test_compare_profiles_banned_and_ai_tag_penalty():
    """gen 含禁用词 / AI 对话标签 → 扣分（banned 2/词 ≤10 · ai_tag 3/个 ≤15）。"""
    ref = mod.analyze_text("他走在路上，看着天空，慢慢地想着。\n“好。”他说。")
    gen = mod.analyze_text("他走在路上，看着天空，慢慢地想着。\n“好。”他说。")
    gen["banned_word_hits"] = {"与此同时": 1, "顿时": 1}    # 2 词 → 4 分
    gen["ai_dialogue_tag_hits"] = {"淡淡地说": 1}            # 1 个 → 3 分
    cmp = mod.compare_profiles(ref, gen)
    assert cmp["banned_word_penalty"] == 4, cmp
    assert cmp["ai_tag_penalty"] == 3, cmp
    # 罚分上限：banned ≤10 / ai_tag ≤15
    gen["banned_word_hits"] = {w: 1 for w in mod.BANNED_WORDS}
    gen["ai_dialogue_tag_hits"] = {w: 1 for w in mod.AI_DIALOGUE_TAGS}
    cmp2 = mod.compare_profiles(ref, gen)
    assert cmp2["banned_word_penalty"] == 10, cmp2
    assert cmp2["ai_tag_penalty"] == 15, cmp2


# ════════════════════════════════════════════════════════════════
# [G] 对话 vs 引号化独白
# ════════════════════════════════════════════════════════════════

def test_split_dialogue_vs_monologue_short_is_dialogue():
    """短引号段（带 attribution 动词）算对话，monologue=0。"""
    d, m = mod._split_dialogue_vs_monologue("“你好世界，今天天气不错。”他说道。")
    assert d > 0 and m == 0, (d, m)


def test_split_dialogue_vs_monologue_long_inner_is_monologue():
    """超长引号段（≥50 字·无紧随 attribution 动词）→ 判引号化独白。"""
    long_inner = "“" + "我必须仔细想想这件事情的来龙去脉究竟是怎么回事" * 3 + "……”"
    d, m = mod._split_dialogue_vs_monologue(long_inner)
    assert m > 0 and d == 0, (d, m)


# ════════════════════════════════════════════════════════════════
# [H] 说话人分类 classify_speakers（registry 划分不变量）
# ════════════════════════════════════════════════════════════════

def test_classify_speakers_partition_invariant():
    """active 与 quoted 是 registry 的划分：并集=registry · 交集=∅。"""
    text = (
        "李明先生走进房间。李明先生说道：“我来了。”\n"
        "张伟队长答道：“好的，李明先生。”\n"
        "李明先生又说道：“我们出发吧。”\n"
        "张伟队长点头道：“走。”"
    )
    paras = mod.split_paragraphs(text)
    registry = mod._build_name_registry(text)
    active, quoted = mod.classify_speakers(text, paras)
    assert (active | quoted) == registry, (active, quoted, registry)
    assert (active & quoted) == set(), (active, quoted)
    # 真名被检出（落在 registry / 划分里）
    union = active | quoted
    assert "李明" in union and "张伟" in union, union


def test_build_name_registry_filters_non_names():
    """registry 过滤代词 / 高频非名词（自己 / 他们 不入名册）。"""
    text = "他们走在路上。自己想了想。李明先生说道：“走吧。”"
    registry = mod._build_name_registry(text)
    assert "自己" not in registry and "他们" not in registry, registry
