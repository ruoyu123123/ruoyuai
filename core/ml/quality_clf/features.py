# 🔴 2026-06-29 NN训练:质量AI腔判别
"""features.py — 确定性风格计量特征（stdlib·零依赖·人感 vs AI腔的"统计指纹"）

【定位】
本模块抽取一段中文小说正文的**确定性风格计量特征**（stylometric features），
不依赖任何 ML 库（纯 stdlib），data_prep.py / eval.py / 集成 scanner 共用。

【为什么要这层】（调研接地·见 README §研究结论）
NN encoder 容易学到「题材/作者身份」的捷径（shortcut learning），而不是真的学
「人感 vs AI腔」。题材无关的统计特征（句长爆发度 burstiness / AI套话密度 / 标点
分布 / 词汇多样性）是**抗捷径**的补充信号——arXiv:2503.00258「content-expression
解耦」证明在表达维度（而非内容维度）上检测能跨语言/跨题材泛化。

这些特征：
  ① 写进 data_prep 的 jsonl（每个样本带 `feats`），供 train.py 选择性融合到
     encoder 的 [CLS] 向量后（concat → MLP），也供纯特征基线（LogReg/SVM）对照。
  ② 与现有 semantic_slop_scanner / function_word_fingerprint_scanner 同源——
     AI套话词表、禁用词表对齐 CLAUDE.md「反 AI 腔调守卫」。

【北极星⑤纪律】这些只是 advisory 信号，不是判决。作者风格档是第一权威——
某作者本来就爱用破折号/长句，本模块只测「分布」，由下游分类器结合 encoder 综合判，
绝不单独某个特征硬卡。

【用法】
    from features import extract_features, FEATURE_ORDER, feature_vector
    feats = extract_features(text)              # -> dict[str, float]
    vec   = feature_vector(text)                # -> list[float]（按 FEATURE_ORDER）
"""
from __future__ import annotations
import math
import re
from collections import Counter

# ============ 词表（对齐 CLAUDE.md 反 AI 腔调守卫 + semantic_slop_scanner）============

# AI 结构套话（"与此同时"类连接词/过渡词·AI 爱用来"缝合"）
AI_TRANSITION = [
    "与此同时", "值得一提的是", "不仅如此", "然而", "事实上", "总的来说",
    "综上所述", "换句话说", "也就是说", "更重要的是", "需要注意的是",
    "总而言之", "由此可见", "不难发现", "众所周知", "首先", "其次", "最后",
    "另一方面", "与之相对", "正因如此", "毫无疑问",
]

# 禁用词（情绪/动作套话·CLAUDE.md 第6条 + 反 AI 腔）
FORBIDDEN_WORDS = [
    "顿时", "紧锁", "显然", "似乎", "此刻", "淡淡", "心中一凛", "眼中闪过一丝",
    "微微挑眉", "仿佛", "嘴角勾起一抹", "深吸一口气", "缓缓地说", "沉吟片刻",
    "不容置疑", "波涛汹涌", "嘴角勾起", "眼中闪过", "心中一惊", "不由得",
    "情不自禁", "下意识", "鬼使神差", "莫名其妙",
]

# 对话标签变体（semantic_slop B+8·同义词循环嫌疑）
SPEECH_TAGS = [
    "说道", "问道", "答道", "回道", "应道", "喊道", "叫道", "笑道", "怒道",
    "沉声道", "低声道", "轻声道", "冷声道", "开口道", "解释道", "补充道",
    "反问道", "追问道", "叹道", "喃喃道", "附和道", "冷笑道", "苦笑道",
]

# 抽象大词（金句体常客）
ABSTRACT_NOUNS = [
    "命运", "人生", "世界", "真相", "选择", "时间", "孤独", "恐惧", "希望",
    "生命", "死亡", "自由", "未来", "过去", "人性", "欲望", "信仰", "宿命",
    "代价", "意义", "本质", "灵魂", "存在", "救赎", "黑暗", "光明",
]

SENT_END = "。！？…"
CJK = re.compile(r"[一-鿿]")
SENT_SPLIT = re.compile(r"(?<=[。！？…])")
PARA_SPLIT = re.compile(r"\n\s*\n")


def _cjk_len(s: str) -> int:
    return sum(1 for ch in s if CJK.match(ch))


def _sentences(text: str) -> list[str]:
    raw = SENT_SPLIT.split(text.replace("\n", ""))
    return [s.strip() for s in raw if s.strip()]


def _paragraphs(text: str) -> list[str]:
    # 段落 = 按任意换行切（网文原文单换行即一段·\n+ 通吃单/双换行）
    return [p.strip() for p in re.split(r"\n+", text) if p.strip()]


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _count_any(text: str, terms: list[str]) -> int:
    return sum(text.count(t) for t in terms)


def extract_features(text: str) -> dict[str, float]:
    """抽取一段正文的确定性风格特征。返回 {name: float}。

    所有密度类特征都按"每千字"归一化（消除长度混淆——调研警告：长度本身会成捷径，
    分类器若发现人样本更长就作弊，故密度归一 + train 侧 length-match 双保险）。
    """
    text = text or ""
    n_cjk = _cjk_len(text)
    sents = _sentences(text)
    paras = _paragraphs(text)
    per_k = 1000.0 / n_cjk if n_cjk else 0.0  # 每千字系数

    # —— 句长统计（burstiness：人类句长方差大）——
    sent_lens = [_cjk_len(s) for s in sents if _cjk_len(s) > 0]
    if sent_lens:
        mean_sl = sum(sent_lens) / len(sent_lens)
        var_sl = sum((x - mean_sl) ** 2 for x in sent_lens) / len(sent_lens)
        std_sl = math.sqrt(var_sl)
        burstiness = _safe_div(std_sl, mean_sl)  # 变异系数·人类更高
        max_sl = max(sent_lens)
        min_sl = min(sent_lens)
    else:
        mean_sl = std_sl = burstiness = max_sl = min_sl = 0.0

    # —— 段长统计 ——
    para_lens = [_cjk_len(p) for p in paras if _cjk_len(p) > 0]
    if para_lens:
        mean_pl = sum(para_lens) / len(para_lens)
        var_pl = sum((x - mean_pl) ** 2 for x in para_lens) / len(para_lens)
        std_pl = math.sqrt(var_pl)
        para_burstiness = _safe_div(std_pl, mean_pl)
    else:
        mean_pl = std_pl = para_burstiness = 0.0

    # —— 单句独行段占比（爽文节奏 vs AI 均匀长段）——
    single_sent_paras = sum(1 for p in paras if len(_sentences(p)) <= 1)
    single_sent_ratio = _safe_div(single_sent_paras, len(paras))

    # —— 对话占比 ——
    dialogue_paras = sum(1 for p in paras
                         if any(q in p for q in ('"', '"', "「", "『")))
    dialogue_ratio = _safe_div(dialogue_paras, len(paras))

    # —— 标点分布（每千字）——
    comma = (text.count("，") + text.count("、")) * per_k
    period = text.count("。") * per_k
    ellipsis = (text.count("…") + text.count("......")) * per_k
    dash = (text.count("—") + text.count("——")) * per_k
    exclaim = text.count("！") * per_k
    question = text.count("？") * per_k

    # —— 词汇多样性（char bigram TTR·AI 更重复）——
    bigrams = [text[i:i + 2] for i in range(len(text) - 1)
               if CJK.match(text[i]) and CJK.match(text[i + 1])]
    ttr_bigram = _safe_div(len(set(bigrams)), len(bigrams))
    # 单字 TTR
    chars = [ch for ch in text if CJK.match(ch)]
    ttr_char = _safe_div(len(set(chars)), len(chars))

    # —— AI 套话 / 禁用词 / 抽象大词 密度（每千字）——
    ai_transition_density = _count_any(text, AI_TRANSITION) * per_k
    forbidden_density = _count_any(text, FORBIDDEN_WORDS) * per_k
    abstract_noun_density = _count_any(text, ABSTRACT_NOUNS) * per_k

    # —— 对话标签变体数（同义词循环嫌疑）+ 密度 ——
    tag_counts = Counter()
    for t in SPEECH_TAGS:
        c = text.count(t)
        if c:
            tag_counts[t] = c
    speech_tag_variety = float(len(tag_counts))
    speech_tag_density = sum(tag_counts.values()) * per_k

    # —— "不是…而是" / "不仅…而且" 否定排比密度 ——
    neg_parallel = (len(re.findall(r"不是[^。！？\n]{1,30}而是", text))
                    + len(re.findall(r"不仅[^。！？\n]{1,30}(?:而且|还|更)", text)))
    neg_parallel_density = neg_parallel * per_k

    # —— "的"字密度（AI 偏好长定语前置·feedback_inverted_modifier_sentence_mold）——
    de_density = text.count("的") * per_k

    return {
        "cjk_len": float(n_cjk),
        "mean_sentence_len": round(mean_sl, 4),
        "std_sentence_len": round(std_sl, 4),
        "sentence_burstiness": round(burstiness, 4),
        "max_sentence_len": float(max_sl),
        "min_sentence_len": float(min_sl),
        "mean_para_len": round(mean_pl, 4),
        "para_burstiness": round(para_burstiness, 4),
        "single_sent_para_ratio": round(single_sent_ratio, 4),
        "dialogue_ratio": round(dialogue_ratio, 4),
        "comma_per_k": round(comma, 4),
        "period_per_k": round(period, 4),
        "ellipsis_per_k": round(ellipsis, 4),
        "dash_per_k": round(dash, 4),
        "exclaim_per_k": round(exclaim, 4),
        "question_per_k": round(question, 4),
        "ttr_bigram": round(ttr_bigram, 4),
        "ttr_char": round(ttr_char, 4),
        "ai_transition_per_k": round(ai_transition_density, 4),
        "forbidden_word_per_k": round(forbidden_density, 4),
        "abstract_noun_per_k": round(abstract_noun_density, 4),
        "speech_tag_variety": speech_tag_variety,
        "speech_tag_per_k": round(speech_tag_density, 4),
        "neg_parallel_per_k": round(neg_parallel_density, 4),
        "de_char_per_k": round(de_density, 4),
    }


# 固定顺序（向量化用·训练/推理必须一致）。
# 注意：cjk_len 是长度本身，默认**不进**模型特征向量（防长度捷径），仅留作元数据。
FEATURE_ORDER = [
    "mean_sentence_len", "std_sentence_len", "sentence_burstiness",
    "max_sentence_len", "min_sentence_len",
    "mean_para_len", "para_burstiness", "single_sent_para_ratio",
    "dialogue_ratio",
    "comma_per_k", "period_per_k", "ellipsis_per_k", "dash_per_k",
    "exclaim_per_k", "question_per_k",
    "ttr_bigram", "ttr_char",
    "ai_transition_per_k", "forbidden_word_per_k", "abstract_noun_per_k",
    "speech_tag_variety", "speech_tag_per_k", "neg_parallel_per_k",
    "de_char_per_k",
]

N_FEATURES = len(FEATURE_ORDER)


def feature_vector(text: str) -> list[float]:
    """按 FEATURE_ORDER 返回特征向量（不含 cjk_len）。"""
    f = extract_features(text)
    return [float(f.get(k, 0.0)) for k in FEATURE_ORDER]


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) > 1:
        txt = open(sys.argv[1], encoding="utf-8").read()
    else:
        txt = sys.stdin.read()
    print(json.dumps(extract_features(txt), ensure_ascii=False, indent=2))
