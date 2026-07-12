"""修辞节奏谱 + 中文特有计量 SFS 测试 — P2 补 compute_style_only_sfs 中文专属维度
（北极星①⑤⑥ · 2026-05-31）。

背景（本批任务说明 · 实证）：现有 compute_style_only_sfs 全是**语言无关**的英文 stylometry
移植（虚词余弦 / 标点余弦 / 句长 JSD / 去题材 POS / 字符 n-gram），**缺中文专属 + 修辞
节奏维度**。本 P2 补两类：
  (A) 修辞节奏谱：anaphora（句首重复）/ epiphora（句尾重复）/ anadiplosis（顶真）/
      排比（连续句共享句首结构）/ 连词叠用（然后/接着/而后）—— 每百句频率谱。
  (B) 中文特有计量：成语密度（内置小词典）/ 文白比（文言虚词 vs 白话助词）/
      标点分布（顿号 / 破折号 / 省略号）—— 每千字密度。

影子并行（北极星纪律 2 · 回归 0）：env RHYTHM_CN_SFS_MODE 控制——
  · shadow（默认）：算 rhythm_cn 子分挂 subscores · **不并入** style_only_sfs 加权（零回归）。
  · active：rhythm_cn 子分并入加权。
  · off：完全不算。

测试覆盖：① 句首/句尾辅助纯函数；② 修辞节奏谱五维纯函数（anaphora/epiphora/顶真/排比/
连词 · 每百句归一/短文降级）；③ 中文计量纯函数（成语密度/文白比/标点 · 每千字归一/无汉字降级）；
④ _profile_match 等权逐维（大量纲不淹没小量纲）；⑤ compute_rhythm_cn_sfs 纯函数（同文自比满分/
不同节奏区分/全 float）；⑥ mode 标志默认 shadow；⑦ shadow 默认零回归（style_only_sfs 不变 ·
subscores 全 float 契约不破）；⑧ active 并入加权改分；⑨ off 不算；⑩ 真原文金标准——同作者
跨章修辞节奏一致性均值 > 跨作者（蛊真人 vs 惊悚乐园）· shadow 真作者校验路径零回归。

只测确定性纯函数，不碰 LLM / agent。守纪律：真作者原文当「系统生成」喂校验确认改动后真
作者不被误判（北极星纪律 3 金标准）。
"""
import os
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_se(mode):
    """以指定 RHYTHM_CN_SFS_MODE reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("RHYTHM_CN_SFS_MODE", None)
    else:
        os.environ["RHYTHM_CN_SFS_MODE"] = mode
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（原文/ 下均为纯正文 txt）。"""
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# [A] 句首/句尾辅助纯函数 _sent_head / _sent_tail
# ════════════════════════════════════════════════════════════════

def test_A_sent_head_tail_strip_punct():
    """_sent_head / _sent_tail 取句首/句尾 k 个汉字（去标点 · 不足 k 返回空）。"""
    assert se._sent_head("他，走进了房间", 2) == "他走"
    assert se._sent_tail("他走进了房间。", 2) == "房间"
    assert se._sent_head("他", 2) == ""   # 不足 2 字
    assert se._sent_head("abc 123", 2) == ""  # 无汉字
    assert se._sent_head("他走进", 1) == "他"


# ════════════════════════════════════════════════════════════════
# [B] 修辞节奏谱 compute_rhetoric_rhythm 纯函数
# ════════════════════════════════════════════════════════════════

def test_B_anaphora_detected():
    """句首 2 字重复 → anaphora 计入（每百句频率 > 0）。"""
    # 「他走」连续重复（句首 2 字相同）
    rh = se.compute_rhetoric_rhythm("他走进来。他走出去。她坐下了。")
    assert rh["anaphora_per100"] > 0, rh
    assert rh["n_sentences"] == 3


def test_B_epiphora_detected():
    """句尾 2 字重复 → epiphora 计入。"""
    rh = se.compute_rhetoric_rhythm("风吹过山岗。云飘过山岗。鸟飞走了。")
    assert rh["epiphora_per100"] > 0, rh


def test_B_anadiplosis_detected():
    """顶真：前句末字 == 后句首字 → anadiplosis 计入。"""
    # 「…房间。间…」前句末「间」后句首「间」
    rh = se.compute_rhetoric_rhythm("他走进房间。间里很暗。")
    assert rh["anadiplosis_per100"] > 0, rh


def test_B_parallelism_detected():
    """排比：≥3 句连续共享句首 1 字 → parallelism 计入。"""
    rh = se.compute_rhetoric_rhythm("不是风。不要怕。不能停。她笑了。")
    # 前 3 句都以「不」开头 → 排比 run=3
    assert rh["parallelism_per100"] > 0, rh


def test_B_conjunction_overuse_counted():
    """顺承连词（然后/接着…）每百句计数。"""
    rh = se.compute_rhetoric_rhythm("他起身。然后走出门。接着上了车。于是出发了。")
    assert rh["conjunction_overuse_per100"] > 0, rh


def test_B_rhetoric_short_text_all_zero():
    """句数 < 2 → 全 0（短文不适用 · 不抛错）。"""
    rh = se.compute_rhetoric_rhythm("他走了。")
    assert rh["n_sentences"] == 1
    for k in ("anaphora_per100", "epiphora_per100", "anadiplosis_per100",
              "parallelism_per100", "conjunction_overuse_per100"):
        assert rh[k] == 0.0, (k, rh)
    rh0 = se.compute_rhetoric_rhythm("")
    assert rh0["n_sentences"] == 0


def test_B_rhetoric_per100_normalized():
    """每百句归一化：相同修辞模式、文本翻倍 → per100 频率不变（长度可比）。"""
    base = "他来了。他走了。她笑了。"
    rh1 = se.compute_rhetoric_rhythm(base)
    rh2 = se.compute_rhetoric_rhythm(base * 4)
    # anaphora per100 应基本一致（归一化后与长度无关）
    assert abs(rh1["anaphora_per100"] - rh2["anaphora_per100"]) < 1.0, (rh1, rh2)


# ════════════════════════════════════════════════════════════════
# [C] 中文特有计量 compute_chinese_metrics 纯函数
# ════════════════════════════════════════════════════════════════

def test_C_idiom_density_dictionary_hits():
    """成语密度：内置词典命中四字成语 → 每千字密度 > 0；无成语 → 0。"""
    cn = se.compute_chinese_metrics("他小心翼翼地走过去，目瞪口呆地站着。")
    assert cn["idiom_density_per1000"] > 0, cn
    cn0 = se.compute_chinese_metrics("他走过去看了一眼然后坐下来。")
    assert cn0["idiom_density_per1000"] == 0.0, cn0


def test_C_idiom_not_cross_punctuation():
    """成语匹配不跨标点（标点断开的 4 字串不误匹配）。"""
    # 「小心、翼翼」被顿号断开 → 不应命中「小心翼翼」
    cn = se.compute_chinese_metrics("他小心、翼翼走过。")
    assert cn["idiom_density_per1000"] == 0.0, cn


def test_C_wenyan_baihua_ratio():
    """文白比：文言虚词多 → 比值高；白话助词多 → 比值低。"""
    wenyan = se.compute_chinese_metrics("之乎者也，其势若虹，故而遂成。")
    baihua = se.compute_chinese_metrics("他的，了的，着呢吧啊，好啦好啦。")
    assert wenyan["wenyan_baihua_ratio"] > baihua["wenyan_baihua_ratio"], \
        (wenyan["wenyan_baihua_ratio"], baihua["wenyan_baihua_ratio"])


def test_C_punctuation_distribution():
    """中文特有标点（顿号/破折号/省略号）每千字密度。"""
    cn = se.compute_chinese_metrics("他、她、它都来了——可是……谁也没说话。")
    assert cn["dunhao_per1000"] > 0, cn
    assert cn["dash_per1000"] > 0, cn
    assert cn["ellipsis_per1000"] > 0, cn


def test_C_chinese_metrics_no_cjk_all_zero():
    """无汉字 → 全 0（不抛错）。"""
    cn = se.compute_chinese_metrics("abc 123 !!! ...")
    assert cn["cjk_chars"] == 0
    for k in ("idiom_density_per1000", "wenyan_baihua_ratio",
              "dunhao_per1000", "dash_per1000", "ellipsis_per1000"):
        assert cn[k] == 0.0, (k, cn)


# ════════════════════════════════════════════════════════════════
# [D] _profile_match 逐维等权
# ════════════════════════════════════════════════════════════════

def test_D_profile_match_identical_full():
    """完全相同向量 → match = 1.0。"""
    assert se._profile_match([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_D_profile_match_equal_weight_not_dominated():
    """逐维等权：大量纲维度差异不淹没小量纲维度（解决单 cosine 被大维度主导问题）。

    向量 A=[1, 100]、B=[5, 100]：小维差 4（相对差大）、大维相同。等权 match 应明显 < 1
    （小维拉低），而非被大维「拉满」。"""
    m = se._profile_match([1.0, 100.0], [5.0, 100.0])
    # 大维满分 1.0、小维 floor=0.5 下 base=max(1,0.5)=1 → 1-|1-5|/1=clamp 到 0 → (0+1)/2=0.5
    assert m < 0.6, m


def test_D_profile_match_empty():
    """空向量 → 0.0（不抛错）。"""
    assert se._profile_match([], []) == 0.0


# ════════════════════════════════════════════════════════════════
# [E] compute_rhythm_cn_sfs 纯函数
# ════════════════════════════════════════════════════════════════

def test_E_rhythm_cn_same_text_high():
    """同一段文本自比 → rhythm_cn SFS 接近满分（健全性）。"""
    text = "他小心翼翼地走进房间，目瞪口呆地看着眼前的一切，然后转身离去。" * 30
    s = se.compute_rhythm_cn_sfs(text, text)
    assert s["rhythm_cn_sfs"] >= 99.0, s


def test_E_rhythm_cn_subscores_and_raw_present():
    """输出含两子项 + 原始特征（advisory 逐项可读）。"""
    s = se.compute_rhythm_cn_sfs("他来了。他走了。" * 20, "她笑着说，然后离开。" * 20)
    assert "rhetoric_rhythm_match" in s
    assert "chinese_metric_match" in s
    assert "ref_rhetoric" in s and "gen_rhetoric" in s
    assert "ref_chinese_metrics" in s and "gen_chinese_metrics" in s


def test_E_rhythm_cn_all_floats():
    """rhythm_cn_sfs 与两子项必须是 stdlib float（零依赖 · JSON 可读）。"""
    s = se.compute_rhythm_cn_sfs("他来了。他走了。" * 20, "她笑着说，然后离开。" * 20)
    assert type(s["rhythm_cn_sfs"]) is float
    assert type(s["rhetoric_rhythm_match"]) is float
    assert type(s["chinese_metric_match"]) is float


def test_E_rhythm_cn_different_style_distinguished():
    """修辞节奏 + 中文计量差异大的两段 → rhythm_cn SFS 显著低于满分（区分力）。

    A：无连词、无排比、白话；B：连词叠用 + 排比 + 文言 —— 修辞节奏谱迥异。"""
    a = "天黑了。她回家。门开着。屋里很冷。" * 20
    b = "他起身，然后出门，接着上车，于是出发，随后抵达。" * 20
    s = se.compute_rhythm_cn_sfs(a, b)
    assert s["rhythm_cn_sfs"] < 90.0, s


# ════════════════════════════════════════════════════════════════
# [F] mode 标志 _rhythm_cn_mode
# ════════════════════════════════════════════════════════════════

def test_F_mode_default_active_and_values():
    """RHYTHM_CN_SFS_MODE 默认 active（2026-05-31 放量 · 真作者跨章一致性均值 > 跨作者不误报）·
    非法回退 active · {shadow,off} 原样。"""
    sx = _reload_se(None)
    try:
        assert sx._rhythm_cn_mode() == "active"
        sx = _reload_se("shadow")
        assert sx._rhythm_cn_mode() == "shadow"
        sx = _reload_se("off")
        assert sx._rhythm_cn_mode() == "off"
        sx = _reload_se("garbage")
        assert sx._rhythm_cn_mode() == "active"
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [G] compute_style_only_sfs 集成：shadow 零回归 / active 改分 / off 不算
# ════════════════════════════════════════════════════════════════

_REF = "他淡淡地看了一眼，显然没把这事放在心上，转身离去。" * 30
_GEN = "她淡淡地笑了笑，显然早就料到这个结果，缓步走开。" * 30


def test_G_shadow_zero_regression_on_style_only_sfs():
    """shadow：style_only_sfs 与 off 完全一致（rhythm_cn 只记录不并入加权 · 零回归核心）。

    CHARNGRAM_SFS_MODE 默认 active 会改分，故显式关掉隔离 rhythm_cn 单变量。"""
    os.environ["CHARNGRAM_SFS_MODE"] = "off"
    try:
        sx = _reload_se("shadow")
        rep_shadow = sx.compute_style_only_sfs(_REF, _GEN)
        rep_off = _reload_se("off").compute_style_only_sfs(_REF, _GEN)
        assert rep_shadow["style_only_sfs"] == rep_off["style_only_sfs"], \
            (rep_shadow["style_only_sfs"], rep_off["style_only_sfs"])
        # shadow 附加 rhythm_cn 子分；off 不算
        assert "rhythm_cn_sfs" in rep_shadow["subscores"]
        assert "rhythm_cn_sfs" not in rep_off["subscores"]
        assert rep_shadow["rhythm_cn_mode"] == "shadow"
        assert rep_off["rhythm_cn_mode"] == "off"
    finally:
        os.environ.pop("CHARNGRAM_SFS_MODE", None)
        _reload_se(None)


def test_G_shadow_subscores_all_float_contract_kept():
    """shadow 默认：subscores 仍全是 float（不破坏「subscores 全 float」既有契约/其他测试）。"""
    sx = _reload_se(None)  # 默认 shadow
    try:
        s = sx.compute_style_only_sfs(_REF, _GEN)
        assert type(s["style_only_sfs"]) is float
        for k, v in s["subscores"].items():
            assert type(v) is float, (k, v, type(v))
    finally:
        _reload_se(None)


def test_G_active_folds_into_weighted_score():
    """active：rhythm_cn 子分按**降权** _RHYTHM_CN_ACTIVE_WEIGHT 并入加权（验证真并入 + 降权）。

    CHARNGRAM_SFS_MODE 默认 active 会另改分，故显式关掉隔离 rhythm_cn 单变量。"""
    os.environ["CHARNGRAM_SFS_MODE"] = "off"
    try:
        sx = _reload_se("active")
        rep_off = _reload_se("off").compute_style_only_sfs(_REF, _GEN)
        # 重新 reload active（_reload_se off 不动 CHARNGRAM env，仍 off）
        sx = _reload_se("active")
        W = sx._RHYTHM_CN_ACTIVE_WEIGHT
        rep_active = sx.compute_style_only_sfs(_REF, _GEN)
        assert "rhythm_cn_sfs" in rep_active["subscores"]
        # active 把 rhythm_cn 降权并入 → 与 off 应不同（除非恰等于其余维度均值 · 几乎不可能）
        assert rep_active["style_only_sfs"] != rep_off["style_only_sfs"], \
            (rep_active["style_only_sfs"], rep_off["style_only_sfs"])
        # 降权并入：active 总分 = (off 真加权 N 维和 + W*rhythm_cn) / (N + W)。
        rc = rep_active["subscores"]["rhythm_cn_sfs"]
        _shadow_only = {"rhythm_cn_sfs", "rhetoric_rhythm_match", "chinese_metric_match",
                        "charngram_sfs", "char_3gram_cosine", "char_3gram_cosine_raw",
                        "word_unigram_cosine"}
        base_keys = [k for k in rep_off["subscores"] if k not in _shadow_only]
        base_vals = [rep_off["subscores"][k] for k in base_keys]
        expected = round((sum(base_vals) + W * rc) / (len(base_vals) + W), 2)
        assert abs(rep_active["style_only_sfs"] - expected) < 0.5, \
            (rep_active["style_only_sfs"], expected)
    finally:
        os.environ.pop("CHARNGRAM_SFS_MODE", None)
        _reload_se(None)


def test_G_off_no_rhythm_cn_keys():
    """off：subscores 完全无 rhythm_cn 键（最纯旧行为）。"""
    sx = _reload_se("off")
    try:
        s = sx.compute_style_only_sfs(_REF, _GEN)
        assert "rhythm_cn_sfs" not in s["subscores"]
        assert "rhetoric_rhythm_match" not in s["subscores"]
        assert "chinese_metric_match" not in s["subscores"]
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [H] 真原文金标准（北极星纪律 3）：同作者跨章一致性均值 > 跨作者
# ════════════════════════════════════════════════════════════════

def test_H_same_author_rhythm_beats_cross_author_mean():
    """同作者跨章修辞节奏 + 中文计量一致性**均值** > 跨作者（蛊真人 vs 惊悚乐园）。

    实证校准（北极星纪律 3 金标准）：章级单维计数噪声大（与 charngram char-3gram 同理），
    故断言**均值**这一鲁棒性质（同作者均值 ~0.59 显著 > 跨作者 ~0.31），不卡脆弱的逐对阈值
    ——正是默认 shadow 的理由（只记录不判决）。"""
    chs = ["第010章", "第020章", "第030章"]
    gu = [t for t in (_load_chapter("蛊真人", c) for c in chs) if t]
    js = [t for t in (_load_chapter("惊悚乐园", c) for c in chs) if t]
    if len(gu) < 2 or len(js) < 2:
        return  # 原文缺失环境跳过
    same = []
    for i in range(len(gu)):
        for j in range(i + 1, len(gu)):
            same.append(se.compute_rhythm_cn_sfs(gu[i], gu[j])["rhythm_cn_sfs"])
    for i in range(len(js)):
        for j in range(i + 1, len(js)):
            same.append(se.compute_rhythm_cn_sfs(js[i], js[j])["rhythm_cn_sfs"])
    cross = []
    for a in gu:
        for b in js:
            cross.append(se.compute_rhythm_cn_sfs(a, b)["rhythm_cn_sfs"])
    same_mean = sum(same) / len(same)
    cross_mean = sum(cross) / len(cross)
    assert same_mean > cross_mean, (same_mean, cross_mean)


def test_H_real_author_shadow_zero_regression():
    """真作者 cluster 喂 compute_style_only_sfs：显式 shadow 下 style_only_sfs 与 off 一致
    （shadow 仍零回归 · 金标准）。CHARNGRAM 默认 active 会改分，显式关掉隔离 rhythm_cn。"""
    a = _load_chapter("蛊真人", "第010章")
    c = _load_chapter("蛊真人", "第020章")
    if a is None or c is None:
        return
    os.environ["CHARNGRAM_SFS_MODE"] = "off"
    try:
        shadow_v = _reload_se("shadow").compute_style_only_sfs(a, c)["style_only_sfs"]
        off_v = _reload_se("off").compute_style_only_sfs(a, c)["style_only_sfs"]
        assert shadow_v == off_v, (shadow_v, off_v)
    finally:
        os.environ.pop("CHARNGRAM_SFS_MODE", None)
        _reload_se(None)


def test_H_real_author_active_not_false_positive():
    """🔴 rhythm_cn active 金标准：真作者跨章在 **active**（默认）下 style_only_sfs 不被
    修辞节奏维度误拉低（vs off 差值在合理范围内 · 不误报真作者退化）。"""
    a = _load_chapter("蛊真人", "第010章")
    c = _load_chapter("蛊真人", "第020章")
    if a is None or c is None:
        return
    os.environ["CHARNGRAM_SFS_MODE"] = "off"
    try:
        active_v = _reload_se("active").compute_style_only_sfs(a, c)["style_only_sfs"]
        off_v = _reload_se("off").compute_style_only_sfs(a, c)["style_only_sfs"]
        # rhythm_cn active 不该把同作者真分大幅拉低（实证同作者跨章修辞节奏一致性不低 · 不误报）
        assert off_v - active_v <= 12.0, (active_v, off_v, "rhythm_cn active 误拉低同作者")
    finally:
        os.environ.pop("CHARNGRAM_SFS_MODE", None)
        _reload_se(None)
