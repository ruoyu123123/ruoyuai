"""字符 n-gram 风格指纹 SFS 测试 — P1 补 compute_style_only_sfs 字符级维度（北极星①⑤⑥ · 2026-05-31）。

背景（Oxford 2025 · AMNP）：字符/词 n-gram 画像是「最难复刻、最能验真伪」的作者特征。
旧 compute_style_only_sfs 只看 虚词余弦 / 标点余弦 / 句长节奏 JSD / 去题材 POS，**缺字符
n-gram 维度**（中文字符级天然友好）。本 P1 补一维：

  · 作者原文池建 **字符 3-gram + 词 unigram** 频率画像（collections.Counter · 纯 stdlib）；
  · 生成稿算同分布，余弦比距离（复用 _cosine_sim）；词 unigram 仅 jieba 可用时计入。

影子并行（北极星纪律 2 · 回归 0）：env CHARNGRAM_SFS_MODE 控制——
  · shadow（默认）：算字符 n-gram 子分挂 subscores · **不并入** style_only_sfs 加权（零回归）。
  · active：字符 n-gram 子分作为第 5 维并入加权平均。
  · off：完全不算。

测试覆盖：① 纯 CJK 抽取；② 字符 3-gram 提取纯函数（频率归一化/短文降级/top_k）；
③ 词 unigram 提取（jieba 可用/降级）；④ compute_charngram_sfs 纯函数（同文自比满分/
不同节奏区分/全 float）；⑤ mode 标志；⑥ shadow 默认零回归（style_only_sfs 不变 · subscores
全 float 契约不破）；⑦ active 并入加权改分；⑧ off 不算；⑨ 真原文金标准——蛊真人跨章
自比字符 n-gram 高分、跨作者（蛊真人 vs 惊悚乐园）字符 n-gram 可区分（同作者 > 跨作者）。

只测确定性纯函数，不碰 LLM / agent。守纪律：真作者原文当「系统生成」喂校验确认改动后
真作者不被误判（北极星纪律 3 金标准）。
"""
import os
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_se(mode):
    """以指定 CHARNGRAM_SFS_MODE reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("CHARNGRAM_SFS_MODE", None)
    else:
        os.environ["CHARNGRAM_SFS_MODE"] = mode
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（剥离 changes JSON 尾巴 · 复用 chapter_io 分隔符）。"""
    import chapter_io as cio
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
    return raw


# ════════════════════════════════════════════════════════════════
# [A] 纯 CJK 抽取 _cjk_only
# ════════════════════════════════════════════════════════════════

def test_A_cjk_only_strips_non_cjk():
    """_cjk_only 去标点/英文/数字/空白，只留汉字流。"""
    assert se._cjk_only("他说：abc 123，好！") == "他说好"
    assert se._cjk_only("") == ""
    assert se._cjk_only("abc123 ，。！") == ""


# ════════════════════════════════════════════════════════════════
# [B] 字符 3-gram 提取 _char_ngram_freq 纯函数
# ════════════════════════════════════════════════════════════════

def test_B_char_ngram_basic_counts_normalized():
    """字符 3-gram 频率归一化（和≈1）· n-gram 是连续 3 汉字窗。"""
    text = "他走进房间他走进房间"  # CJK=10 → 3-gram 数 = 8
    freq = se._char_ngram_freq(text, n=3)
    assert freq, freq
    # 频率和归一化
    assert abs(sum(freq.values()) - 1.0) < 1e-9, sum(freq.values())
    # 每个 key 都是 3 字
    for g in freq:
        assert len(g) == 3, g
    # 「他走进」重复 2 次 → 应是最高频之一
    assert "他走进" in freq


def test_B_char_ngram_strips_punctuation_first():
    """字符 n-gram 在纯 CJK 流上滑（标点/英文先剥离 · 不混入字组）。"""
    a = se._char_ngram_freq("他，走。进！房？间", n=3)
    b = se._char_ngram_freq("他走进房间", n=3)
    # 去标点后字符流相同 → 画像相同
    assert a == b, (a, b)


def test_B_char_ngram_short_text_empty():
    """文本不足 n 字（纯 CJK < 3）→ 返回 {}（短文不适用 · 不抛错）。"""
    assert se._char_ngram_freq("他走", n=3) == {}
    assert se._char_ngram_freq("abc 12", n=3) == {}  # 无 CJK
    assert se._char_ngram_freq("", n=3) == {}


def test_B_char_ngram_topk_cap():
    """top_k 截断：高频 n-gram 进画像，长尾不进（控向量维度）。"""
    # 构造大量不同 3-gram（每 3 字一组都不同）
    import string
    cjk_pool = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    text = "".join(cjk_pool[i % len(cjk_pool)] for i in range(2000))
    freq = se._char_ngram_freq(text, n=3, top_k=10)
    assert len(freq) <= 10, len(freq)


# ════════════════════════════════════════════════════════════════
# [C] 词 unigram 提取 _word_unigram_freq
# ════════════════════════════════════════════════════════════════

def test_C_word_unigram_jieba_or_fallback():
    """词 unigram：jieba 可用 → 归一化非空 dict（只含 CJK 词）；不可用 → {}（降级）。"""
    text = "他缓缓走进了那间昏暗的房间。" * 5
    wu = se._word_unigram_freq(text)
    if se._JIEBA_AVAILABLE:
        assert wu, wu
        assert abs(sum(wu.values()) - 1.0) < 1e-6, sum(wu.values())
        # 纯标点/英文 token 不进画像
        for w in wu:
            assert se.CHINESE_CHAR.search(w), w
    else:
        assert wu == {}


def test_C_word_unigram_no_cjk_empty():
    """无 CJK 词 → {}（jieba 可用时也应为空 · 纯标点/英文不计）。"""
    wu = se._word_unigram_freq("abc 123 ... !!!")
    assert wu == {}


# ════════════════════════════════════════════════════════════════
# [D] compute_charngram_sfs 纯函数
# ════════════════════════════════════════════════════════════════

def test_D_charngram_same_text_high():
    """同一段文本自比 → 字符 n-gram SFS 接近满分（健全性）。"""
    text = "他淡淡地看了一眼，显然没把这事放在心上，转身离去。" * 30
    s = se.compute_charngram_sfs(text, text)
    assert s["charngram_sfs"] >= 99.0, s


def test_D_charngram_subscores_present():
    """子项含 char_3gram_cosine（词 unigram 视 jieba 可用性）· 逐项可读。"""
    s = se.compute_charngram_sfs("他走进房间他出门去。" * 30, "她走出门外她进屋里。" * 30)
    assert "char_3gram_cosine" in s["subscores"]
    assert s["char_ngram_n"] == 3
    # 词 unigram 子项有无与 word_unigram_used 一致
    assert ("word_unigram_cosine" in s["subscores"]) == s["word_unigram_used"]


def test_D_charngram_all_floats():
    """charngram_sfs 与子项必须是 stdlib float（零依赖 · JSON 可读）。"""
    s = se.compute_charngram_sfs("他走进房间他出门去。" * 30, "她走出门外她进屋里。" * 30)
    assert type(s["charngram_sfs"]) is float
    for v in s["subscores"].values():
        assert type(v) is float, (v, type(v))


def test_D_charngram_different_text_distinguished():
    """完全不同字组的两段 → 字符 n-gram SFS 显著低于满分（区分力）。"""
    a = "他缓缓抬起手掌灵气涌动而出。" * 30
    b = "公司财报数据电脑屏幕键盘鼠标。" * 30
    s = se.compute_charngram_sfs(a, b)
    assert s["charngram_sfs"] < 60.0, s


# ════════════════════════════════════════════════════════════════
# [E] mode 标志 _charngram_mode
# ════════════════════════════════════════════════════════════════

def test_E_mode_default_shadow_and_values():
    """CHARNGRAM_SFS_MODE 默认 shadow（零回归）· 非法回退 shadow · {active,off} 原样。"""
    sx = _reload_se(None)
    try:
        assert sx._charngram_mode() == "shadow"
        sx = _reload_se("active")
        assert sx._charngram_mode() == "active"
        sx = _reload_se("off")
        assert sx._charngram_mode() == "off"
        sx = _reload_se("garbage")
        assert sx._charngram_mode() == "shadow"
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [F] compute_style_only_sfs 集成：shadow 零回归 / active 改分 / off 不算
# ════════════════════════════════════════════════════════════════

_REF = "他淡淡地看了一眼，显然没把这事放在心上，转身离去。" * 30
_GEN = "她淡淡地笑了笑，显然早就料到这个结果，缓步走开。" * 30


def test_F_shadow_zero_regression_on_style_only_sfs():
    """shadow（默认）：style_only_sfs 与 off 完全一致（字符 n-gram 只记录不并入加权 · 零回归核心）。"""
    sx = _reload_se("shadow")
    try:
        rep_shadow = sx.compute_style_only_sfs(_REF, _GEN)
        rep_off = _reload_se("off").compute_style_only_sfs(_REF, _GEN)
        assert rep_shadow["style_only_sfs"] == rep_off["style_only_sfs"], \
            (rep_shadow["style_only_sfs"], rep_off["style_only_sfs"])
        # shadow 附加字符 n-gram 子分；off 不算
        assert "charngram_sfs" in rep_shadow["subscores"]
        assert "charngram_sfs" not in rep_off["subscores"]
        assert rep_shadow["charngram_mode"] == "shadow"
        assert rep_off["charngram_mode"] == "off"
    finally:
        _reload_se(None)


def test_F_shadow_subscores_all_float_contract_kept():
    """shadow 默认：subscores 仍全是 float（不破坏「subscores 全 float」既有契约/L3a 测试）。"""
    sx = _reload_se(None)  # 默认 shadow
    try:
        s = sx.compute_style_only_sfs(_REF, _GEN)
        assert type(s["style_only_sfs"]) is float
        for k, v in s["subscores"].items():
            assert type(v) is float, (k, v, type(v))
    finally:
        _reload_se(None)


def test_F_active_folds_into_weighted_score():
    """active：字符 n-gram 子分并入加权第 5 维 → 与 off 同输入分数可不同（验证真并入）。

    用一对字符 n-gram 差异明显、但其余维度接近的样本 → active 分应被字符 n-gram 子分拉动。"""
    sx = _reload_se("active")
    try:
        rep_active = sx.compute_style_only_sfs(_REF, _GEN)
        rep_off = _reload_se("off").compute_style_only_sfs(_REF, _GEN)
        # active 含字符 n-gram 子分
        assert "charngram_sfs" in rep_active["subscores"]
        # active 把 charngram 并入加权 → 与 off 应不同（除非 charngram 子分恰等于其余维度均值，
        # 本对样本字组差异明显故几乎不可能恰等）
        assert rep_active["style_only_sfs"] != rep_off["style_only_sfs"], \
            (rep_active["style_only_sfs"], rep_off["style_only_sfs"])
        # 并入第 5 维：active 总分 = (off 的真加权 N 维和 + charngram) / (N+1)。
        # off 的 subscores 含其它影子特征子分（如 rhythm_cn 默认 shadow → 记录但不并入加权），
        # 必须一并排除，只留真正进加权的基础维（fw/punc/rhythm/pos）。
        cg = rep_active["subscores"]["charngram_sfs"]
        _shadow_only = {"charngram_sfs", "char_3gram_cosine", "word_unigram_cosine",
                        "rhythm_cn_sfs", "rhetoric_rhythm_match", "chinese_metric_match"}
        base_keys = [k for k in rep_off["subscores"] if k not in _shadow_only]
        base_vals = [rep_off["subscores"][k] for k in base_keys]
        expected = round((sum(base_vals) + cg) / (len(base_vals) + 1), 2)
        assert abs(rep_active["style_only_sfs"] - expected) < 0.5, \
            (rep_active["style_only_sfs"], expected)
    finally:
        _reload_se(None)


def test_F_off_no_charngram_keys():
    """off：subscores 完全无字符 n-gram 键（最纯旧四维行为）。"""
    sx = _reload_se("off")
    try:
        s = sx.compute_style_only_sfs(_REF, _GEN)
        assert "charngram_sfs" not in s["subscores"]
        assert "char_3gram_cosine" not in s["subscores"]
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [G] 真原文金标准（北极星纪律 3）：跨章自比高 · 跨作者可区分
# ════════════════════════════════════════════════════════════════

def test_G_real_author_self_charngram_high():
    """真作者两段原文互比 → 字符 n-gram SFS 明显高于跨作者基线（同作者字组笔迹一致）。

    实测校准（北极星纪律 3）：同作者 ch10v20 rollup——蛊真人 54.2 / 惊悚乐园 49.0；
    跨作者（蛊真人 vs 惊悚乐园）仅 35-38（char_3gram cosine 几乎为 0）。取 45.0 阈值干净
    分开二者（同作者下界 49 > 45 > 跨作者上界 38 · 不卡刀刃 · 守金标准）。char 3-gram 在
    章级对内容敏感（同作者 cosine 仅 14-26），词 unigram 撑住（82-84）——正是默认 shadow 的理由。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_chapter(book, "第010章")
        c = _load_chapter(book, "第020章")
        if a is None or c is None:
            continue
        found = True
        s = se.compute_charngram_sfs(a, c)
        assert s["charngram_sfs"] >= 45.0, (book, s)
    assert found or _load_chapter("蛊真人", "第010章") is None


def test_G_same_author_beats_cross_author_charngram():
    """同作者字符 n-gram SFS > 跨作者（字组笔迹区分力 · 跨作者应可区分 · 金标准核心）。"""
    gu_a = _load_chapter("蛊真人", "第010章")
    gu_b = _load_chapter("蛊真人", "第020章")
    js_a = _load_chapter("惊悚乐园", "第010章")
    if gu_a is None or gu_b is None or js_a is None:
        return
    same = se.compute_charngram_sfs(gu_a, gu_b)["charngram_sfs"]
    cross = se.compute_charngram_sfs(gu_a, js_a)["charngram_sfs"]
    assert same > cross, (same, cross)


def test_G_real_author_shadow_zero_regression():
    """真作者 cluster 喂 compute_style_only_sfs：shadow 默认下 style_only_sfs 与 off 一致
    （真作者校验路径零回归 · 金标准）。"""
    a = _load_chapter("蛊真人", "第010章")
    c = _load_chapter("蛊真人", "第020章")
    if a is None or c is None:
        return
    sx = _reload_se(None)  # 默认 shadow
    try:
        shadow_v = sx.compute_style_only_sfs(a, c)["style_only_sfs"]
        off_v = _reload_se("off").compute_style_only_sfs(a, c)["style_only_sfs"]
        assert shadow_v == off_v, (shadow_v, off_v)
    finally:
        _reload_se(None)
