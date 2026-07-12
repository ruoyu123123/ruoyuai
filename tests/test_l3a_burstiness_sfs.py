"""L3a 滑窗 burstiness + 去题材 SFS 测试 — 治 cluster 级 D 级崩塌（北极星①⑤⑥ · 2026-05-30）。

背景（memory reference-system-validation-method）：旧 style_evaluator 的 SFS 把整 cluster 当
**一个**向量比 → 长文里局部段长崩塌被均值抹平（实测蛊真人单章 B 级 / cluster 级仅 D 级）；
且名词/人名/情节动词等**题材信号**淹没真正的风格信号（虚词/标点/句长节奏）。

升级（全在 style_evaluator.py 内自包含 · 不改 style_analyzer）：
  (a) 滑窗 burstiness：每 ~1500-2000 CJK 一窗，逐窗算句长/段长/功能词指纹 → 窗间方差。
      方差过低 = AI 腔均匀化。并**定位最崩窗**（索引 + 指标 · 逐窗逐维可读不黑箱）。
  (b) 去题材 SFS：只比虚词/标点/句长节奏（+ POS 若 jieba.posseg 可用），对名词/人名/
      情节动词停用（jieba 停用 · 不可用降级功能词白名单）。

影子并行（北极星纪律 2）：env L3A_BURSTINESS_MODE 控制——
  · shadow（默认）：算 L3a 全量挂在 report.l3a 加项 · 不改 sfs_quick/grade 判决（零回归）。
  · active：同样计算附加 + advisory issue 升顶层（仍 advisory · 绝不进 hard_gate）。
  · off：完全不算 L3a（纯旧 SFS 行为）。

测试覆盖：① 滑窗切分纯函数；② 单窗指标；③ burstiness 方差 + 最崩窗定位；④ 去题材 SFS
（同作者高 / 跨题材不被题材信号误判）；⑤ jieba 降级；⑥ mode 标志；⑦ shadow 默认零回归；
⑧ active 升顶层；⑨ advisory gate_level 强制；⑩ 真原文矫枉过正金标准——蛊真人/惊悚乐园
原文当「系统生成」喂校验，真作者 burstiness 不被误判为 AI 均匀化、去题材 SFS 高分。

只测确定性纯函数（程序化评分），不碰 LLM / agent。守纪律：真作者原文当「系统生成」喂校验
确认改动后真作者不被误判（北极星纪律 3 金标准）。
"""
import os
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_se(mode):
    """以指定 L3A_BURSTINESS_MODE reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("L3A_BURSTINESS_MODE", None)
    else:
        os.environ["L3A_BURSTINESS_MODE"] = mode
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（原文/ 下均为纯正文 txt）。"""
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _load_cluster(book, count=6, start=10):
    """拼真原文若干章成一个 cluster（~13-17k CJK · 模拟 cluster 草稿）。"""
    d = _ROOT / "workspace" / "styles" / book / "原文"
    if not d.is_dir():
        return None
    files = sorted(d.glob("第*章.txt"))
    files = [f for f in files if "全本" not in f.name][start:start + count]
    if len(files) < 4:
        return None
    parts = [f.read_text(encoding="utf-8") for f in files]
    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════════
# [A] 滑窗切分 _split_windows 纯函数
# ════════════════════════════════════════════════════════════════

def test_A_split_windows_long_text_multiple():
    """长文（>target）切多窗 · 每窗 CJK 在合理范围（不超 win_max + 末窗不太小）。"""
    para = "他走进房间，看了看四周，慢慢地坐了下来。"  # ~18 cjk
    text = "\n".join(para for _ in range(500))  # ~9000 cjk
    wins = se._split_windows(text, target=1750, win_min=1500, win_max=2000)
    assert len(wins) >= 4, len(wins)
    for w in wins[:-1]:
        # 非末窗不应超过 win_max + 一段裕量
        assert se.count_chinese(w) <= 2000 + 20, se.count_chinese(w)


def test_A_split_windows_short_text_single():
    """短文（<win_min）→ 1 窗（不强切）。"""
    text = "他停下脚步。\n风很大。\n他往前走。"
    wins = se._split_windows(text)
    assert len(wins) == 1, wins


def test_A_split_windows_empty():
    """空文本 / 无 CJK → 空列表（坏输入不抛错）。"""
    assert se._split_windows("") == []
    assert se._split_windows("12345 abcde") == []


def test_A_split_windows_tail_merged_when_tiny():
    """末窗 < win_min 且已有窗 → 并入前窗（不留小尾巴污染方差）。"""
    big = "这是一段比较长的内容用来凑满一个窗口的字数。" * 90  # ~1800 cjk 一段
    text = big + "\n" + "短尾。"  # 末段极短
    wins = se._split_windows(text, target=1750, win_min=1500, win_max=2000)
    # 末窗不会是孤立的「短尾。」
    assert "短尾" in wins[-1]
    assert se.count_chinese(wins[-1]) >= 1500 or len(wins) == 1


# ════════════════════════════════════════════════════════════════
# [B] 单窗指标 _window_metrics
# ════════════════════════════════════════════════════════════════

def test_B_window_metrics_fields():
    """单窗指标含句长/段长/单句成段率/逗号密度/功能词指纹。"""
    text = "他走进房间。\n看了看四周，然后坐下了，叹了口气。\n谁？"
    m = se._window_metrics(text)
    assert m["sentence_mean_len"] > 0
    assert m["paragraph_mean_len"] > 0
    assert 0.0 <= m["single_sentence_para_ratio"] <= 1.0
    assert m["comma_per_1000"] >= 0
    assert "的" in m["function_word_per_1000"]


def test_B_variance_helper():
    """_variance：单值/空 → 0 · 多值正确。"""
    assert se._variance([]) == 0.0
    assert se._variance([5.0]) == 0.0
    # [1,3] 方差 = ((1-2)^2+(3-2)^2)/2 = 1
    assert abs(se._variance([1.0, 3.0]) - 1.0) < 1e-9


# ════════════════════════════════════════════════════════════════
# [C] burstiness 方差 + 最崩窗定位
# ════════════════════════════════════════════════════════════════

def test_C_burstiness_single_window_not_applicable():
    """窗口数 < 2 → applicable=False（短文不算 cluster 级 burstiness）。"""
    b = se.compute_burstiness("他停下来。\n风很大。")
    assert b["applicable"] is False
    assert b["n_windows"] < 2


def test_C_burstiness_homogenized_zero_variance():
    """完全均匀化文本（每段同长同节奏）→ CV≈0（AI 腔检测核心信号）。"""
    para = "他走进房间看了看四周然后慢慢地坐了下来心里想着刚才发生的那些事情感到疲惫。"
    homog = "\n".join(para for _ in range(400))  # ~15k cjk
    b = se.compute_burstiness(homog)
    assert b["applicable"] is True
    assert b["n_windows"] >= 2
    assert b["overall_burstiness_cv"] < 0.04, b["overall_burstiness_cv"]


def test_C_worst_window_localizes_collapse():
    """人为插入一段极短窗 → 最崩窗能定位到段均长偏低的那一窗（逐窗可读不黑箱）。"""
    normal = "这是一段比较正常长度的小说内容用来铺陈场景与人物的动作描写。"  # ~28 cjk 一段
    short = "他停。"  # 3 cjk 极短段
    # 4 个正常长段窗 + 1 个全短段崩塌窗 + 4 个正常长段窗（每窗 ~1800/1500 cjk）
    lines: list[str] = []
    for _ in range(4):
        lines += [normal] * 65
    lines += [short] * 500
    for _ in range(4):
        lines += [normal] * 65
    text = "\n".join(lines)
    b = se.compute_burstiness(text)
    assert b["applicable"], b
    assert b["n_windows"] >= 5, b["n_windows"]
    ww = b["worst_window"]
    # 最崩窗 = 那个全短段窗（段均长 ~3 远低于中位数 ~29）
    assert ww["paragraph_mean_len"] < b["median_paragraph_mean_len"]
    assert ww["deviation_below_median"] > 8.0, ww  # 显著崩塌
    assert "metrics" in ww  # 含该窗逐维指标


# ════════════════════════════════════════════════════════════════
# [D] 去题材 SFS compute_style_only_sfs
# ════════════════════════════════════════════════════════════════

def test_D_style_only_same_text_high():
    """同一段文本自比 → 去题材 SFS 接近满分（健全性）。"""
    text = "他淡淡地看了一眼，显然没把这事放在心上。" * 40
    s = se.compute_style_only_sfs(text, text)
    assert s["style_only_sfs"] >= 95.0, s


def test_D_style_only_subscores_present():
    """子项含虚词/标点/句长节奏（POS 视 jieba 可用性）· 逐项可读。"""
    s = se.compute_style_only_sfs("他走进房间。" * 30, "她走出门外。" * 30)
    assert "function_word_cosine" in s["subscores"]
    assert "punctuation_cosine" in s["subscores"]
    assert "sentence_rhythm_jsd" in s["subscores"]
    # POS 子项有无取决于 jieba；其存在性与 jieba_pos_used 一致
    assert ("detopic_pos_cosine" in s["subscores"]) == s["jieba_pos_used"]


def test_D_style_only_all_floats_not_numpy():
    """子项与总分必须是 stdlib float（scipy 的 np.float64 已 cast · JSON 可读）。"""
    s = se.compute_style_only_sfs("他走进房间。" * 30, "她走出门外。" * 30)
    assert type(s["style_only_sfs"]) is float
    for v in s["subscores"].values():
        assert type(v) is float, (v, type(v))


def test_D_detopic_same_topic_different_style_distinguished():
    """同题材词、不同风格节奏 → 去题材后仍能区分（风格信号不被题材掩盖）。"""
    # 两段都讲「蛊虫/方源」（同题材名词），但句长节奏迥异
    short_rhythm = "方源出手。\n蛊虫死了。\n他走了。\n天黑了。\n风停了。" * 20
    long_rhythm = ("方源缓缓抬起手掌，体内的蛊虫顺着经脉涌动而出，"
                   "在他掌心凝聚成一道幽幽的青色光芒，久久不曾散去。") * 20
    s = se.compute_style_only_sfs(short_rhythm, long_rhythm)
    # 节奏差异巨大 → 去题材 SFS 不应是满分（风格不符被识别 · 句长节奏子项拉低）
    assert s["style_only_sfs"] < 90.0, s
    assert s["subscores"]["sentence_rhythm_jsd"] < 80.0, s["subscores"]


# ════════════════════════════════════════════════════════════════
# [E] jieba 降级与 POS 分布
# ════════════════════════════════════════════════════════════════

def test_E_pos_distribution_keeps_function_pos_only():
    """_pos_style_distribution 只保留虚词/结构 POS（保留集前缀），停用内容词。
    jieba 不可用 → 返回 {}（降级 · 不报错）。"""
    if not se._JIEBA_AVAILABLE:
        assert se._pos_style_distribution("他淡淡地看了一眼。") == {}
        return
    dist = se._pos_style_distribution("他淡淡地看了一眼，显然蛊虫钻进了方源的体内。")
    # 返回的 POS flag 头字母都在保留集合内（无 n*/v* 内容词）
    for flag in dist:
        assert flag[0] in se._L3A_STYLE_POS_PREFIXES, flag
    # 分布归一化（和≈1）
    if dist:
        assert abs(sum(dist.values()) - 1.0) < 1e-6, dist


def test_E_style_only_fallback_label_when_no_pos():
    """jieba 不可用时 topic_stopwords 字段标注 fallback（透明降级）。"""
    s = se.compute_style_only_sfs("他走进房间。" * 30, "她走出门外。" * 30)
    if se._JIEBA_AVAILABLE:
        assert s["topic_stopwords_applied"] is True
        assert s["jieba_pos_used"] is True
    else:
        assert s["topic_stopwords_applied"] == "function_word_whitelist_fallback"
        assert s["jieba_pos_used"] is False


# ════════════════════════════════════════════════════════════════
# [F] mode 标志 _l3a_burstiness_mode
# ════════════════════════════════════════════════════════════════

def test_F_mode_default_shadow_and_values():
    """L3A_BURSTINESS_MODE 默认 active（2026-05-31 放量·n_windows≥5门控防小尺寸误判）· 非法回退 active · {shadow,off} 原样。"""
    sx = _reload_se(None)
    try:
        assert sx._l3a_burstiness_mode() == "active"
        sx = _reload_se("shadow")
        assert sx._l3a_burstiness_mode() == "shadow"
        sx = _reload_se("off")
        assert sx._l3a_burstiness_mode() == "off"
        sx = _reload_se("garbage")
        assert sx._l3a_burstiness_mode() == "active"
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [G] evaluate 集成：shadow 零回归 / active 升顶层 / off 不算
# ════════════════════════════════════════════════════════════════

_REF = "他淡淡地看了一眼，显然没把这事放在心上。" * 30
_GEN = "她淡淡地笑了笑，显然早就料到这个结果。" * 30


def test_G_shadow_zero_regression_on_sfs_quick():
    """shadow（默认）：sfs_quick / grade 与 off 完全一致（附加 l3a 不改判决 · 零回归核心）。"""
    sx = _reload_se("shadow")
    try:
        rep_shadow = sx.evaluate(_REF, _GEN)
        sx_off = _reload_se("off")
        rep_off = sx_off.evaluate(_REF, _GEN)
        assert rep_shadow["sfs_quick"] == rep_off["sfs_quick"]
        assert (rep_shadow["programmatic_score"]["grade"]
                == rep_off["programmatic_score"]["grade"])
        # shadow 附加 l3a 加项；off 不算
        assert "l3a" in rep_shadow
        assert "l3a" not in rep_off
        # shadow 不把 advisory 升顶层（保默认零侵入）
        assert "advisory_issues" not in rep_shadow
    finally:
        _reload_se(None)


def test_G_active_surfaces_advisory_top_level():
    """active：advisory issue 升 report 顶层（消费方可见）· 仍不改 sfs_quick。"""
    sx = _reload_se("active")
    try:
        # 用均匀化文本触发 L3A_LOW_BURSTINESS advisory
        para = "他走进房间看了看四周然后慢慢地坐了下来心里想着那些事情感到疲惫。"
        homog = "\n".join(para for _ in range(400))
        rep_active = sx.evaluate(_REF, homog)
        rep_shadow_off = _reload_se("off").evaluate(_REF, homog)
        # active 顶层有 advisory_issues
        assert "advisory_issues" in rep_active
        # sfs_quick 不被 active 改（与 off 同输入同分 · 判决不变）
        assert rep_active["sfs_quick"] == rep_shadow_off["sfs_quick"]
    finally:
        _reload_se(None)


def test_G_all_issues_gate_level_advisory():
    """北极星⑤强制：L3a 产出的所有 issue gate_level == advisory（绝不黑箱判决/hard_gate）。"""
    para = "他走进房间看了看四周然后慢慢地坐了下来心里想着那些事情感到疲惫。"
    homog = "\n".join(para for _ in range(400))
    # 跨题材 + 均匀化 → 同时触发多条 advisory
    l3a = se.compute_l3a("方源缓缓抬手，蛊虫涌出。" * 50, homog)
    assert l3a["advisory_issues"], "应至少触发一条 advisory"
    for it in l3a["advisory_issues"]:
        assert it["gate_level"] == "advisory", it
    # note 显式声明顾问非法官
    assert "advisory" in l3a["note"]


def test_G_no_hard_gate_codes_introduced():
    """L3a 的 code 绝不进 audit_hub.HARD_GATE_CODES（新 code 必须留在 advisory 域）。"""
    try:
        sys.path.insert(0, str(_ROOT / "core" / "scripts"))
        import audit_hub  # noqa: E402
    except Exception:
        return  # 无 audit_hub 环境则跳过
    l3a_codes = {"L3A_LOW_BURSTINESS", "L3A_WINDOW_COLLAPSE", "L3A_STYLE_ONLY_LOW"}
    assert l3a_codes.isdisjoint(set(audit_hub.HARD_GATE_CODES)), \
        "L3a code 不得进 hard_gate（顾问非法官 · 北极星⑤）"


# ════════════════════════════════════════════════════════════════
# [H] 真原文矫枉过正金标准（北极星纪律 3）：真作者喂校验不被误判
# ════════════════════════════════════════════════════════════════

def test_H_gu_zhenren_real_cluster_burstiness_not_flagged():
    """蛊真人真原文 cluster 当「系统生成」喂 burstiness → CV 不低于 AI 均匀化阈值
    （真作者 burstiness 健康 · 绝不误判为 AI 腔均匀化 · 金标准核心）。"""
    cluster = _load_cluster("蛊真人", count=6, start=10)
    if cluster is None:
        return  # CI 无样本则跳过
    b = se.compute_burstiness(cluster)
    assert b["applicable"], b
    assert b["n_windows"] >= 4, b["n_windows"]
    # 真作者 CV 实测 0.06-0.11，远高于 0.04 阈值 → 不触发 L3A_LOW_BURSTINESS
    assert b["overall_burstiness_cv"] >= 0.04, b["overall_burstiness_cv"]


def test_H_jingsong_real_cluster_burstiness_not_flagged():
    """惊悚乐园真原文 cluster：burstiness 健康（实测 CV 0.16-0.20 · 对话多方差大）。"""
    cluster = _load_cluster("惊悚乐园", count=6, start=10)
    if cluster is None:
        return
    b = se.compute_burstiness(cluster)
    assert b["applicable"], b
    assert b["overall_burstiness_cv"] >= 0.04, b["overall_burstiness_cv"]


def test_H_real_author_no_low_burstiness_advisory():
    """真作者 cluster 喂 compute_l3a → 不产生 L3A_LOW_BURSTINESS advisory（金标准）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        cluster = _load_cluster(book, count=6, start=10)
        if cluster is None:
            continue
        found = True
        l3a = se.compute_l3a(cluster, cluster)  # 自比：ref=gen=真原文
        codes = [i["code"] for i in l3a["advisory_issues"]]
        assert "L3A_LOW_BURSTINESS" not in codes, (book, codes)
    assert found or _load_cluster("蛊真人") is None


def test_H_real_author_self_style_only_high():
    """真作者两段原文互比 → 去题材风格 SFS 高分（同作者风格一致 · 不被误判文风不符）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_chapter(book, "第010章")
        c = _load_chapter(book, "第020章")
        if a is None or c is None:
            continue
        found = True
        s = se.compute_style_only_sfs(a, c)
        # 同作者不同章 → 去题材 SFS 应较高（不触发 L3A_STYLE_ONLY_LOW < 60）
        assert s["style_only_sfs"] >= 60.0, (book, s)
    assert found or _load_chapter("蛊真人", "第010章") is None


def test_H_cross_genre_not_falsely_penalized_on_topic():
    """跨题材（蛊真人 仙侠 vs 惊悚乐园 都市）：去题材后题材信号不该淹没——
    POS/虚词子项不因题材不同而崩（差异应主要落在真实风格节奏维度，不是题材维度）。"""
    gu = _load_chapter("蛊真人", "第010章")
    js = _load_chapter("惊悚乐园", "第010章")
    if gu is None or js is None:
        return
    cross = se.compute_style_only_sfs(gu, js)
    # 去题材后虚词指纹仍高（中文功能词跨题材稳定 · 证明题材信号被剥离）
    assert cross["subscores"]["function_word_cosine"] >= 80.0, cross["subscores"]
    if cross["jieba_pos_used"]:
        # 去题材 POS 分布跨题材也应较稳（不因名词/题材不同而崩）
        assert cross["subscores"]["detopic_pos_cosine"] >= 80.0, cross["subscores"]


def test_H_same_author_beats_cross_author():
    """同作者 SFS > 跨作者 SFS（去题材后真实风格差异仍被保留 · 区分力没被洗掉）。"""
    gu_a = _load_chapter("蛊真人", "第010章")
    gu_b = _load_chapter("蛊真人", "第020章")
    js_a = _load_chapter("惊悚乐园", "第010章")
    if gu_a is None or gu_b is None or js_a is None:
        return
    same = se.compute_style_only_sfs(gu_a, gu_b)["style_only_sfs"]
    cross = se.compute_style_only_sfs(gu_a, js_a)["style_only_sfs"]
    assert same > cross, (same, cross)
