"""style_evaluator 维度7『禁用词扣分』作者档优先回归测试 — 守护 2026-05-30 修 #4。

背景（北极星①贴合作者风格）：style_analyzer 把禁用词分两类——
  · AI_STRUCTURAL_BANNED（与此同时/值得一提的是…）：任何作者都不用 → 永远硬扣；
  · CRAFT_SIGNATURE_BANNED（顿时/淡淡/显然/缓缓地说…）：可能是某作者签名笔法。
validate_style 写作端在有作者档时已把 craft 命中降 WARN（复刻作者优先），但 SFS 维度7
旧实现对全集（14 craft + 4 AI）单边求和硬扣（banned_score = 1 - hit*0.05，ref 钉死 0）。
→ 源作者高频用「淡淡/显然」时，忠实复刻反被 SFS 扣分，与「贴合作者风格」终极目标冲突。

修复后断言：
  · 有作者档 + 工艺签名词频率≈ref → 维度7 不被拉低（接近满分）；
  · 有作者档 + AI 结构套话 → 维度7 仍被硬扣（接近 0）；
  · 无作者档 → 维度7 保持旧的全集单边硬扣行为（向后兼容）；
  · baseline 风格 JSON 提供时自动推断为有作者档。

只测确定性纯函数（程序化评分），不碰 LLM / agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se
from style_analyzer import (
    analyze_text,
    AI_STRUCTURAL_BANNED,
    CRAFT_SIGNATURE_BANNED,
)


def _dim7(report: dict) -> dict:
    for d in report["programmatic_score"]["dimensions"]:
        if d["name"] == "禁用词扣分":
            return d
    raise AssertionError("未找到维度7『禁用词扣分』")


# 源作者：高频使用工艺签名词「淡淡」「显然」（视为该作者签名笔法）
REF_CRAFT_HEAVY = "他淡淡地看了一眼，显然没把这事放在心上。" * 30
# 忠实复刻：同样高频用淡淡/显然（频率≈ref）
GEN_CRAFT_FAITHFUL = "她淡淡地笑了笑，显然早就料到这个结果。" * 30
# AI 套话：用与此同时/值得一提的是（结构性机器腔）
GEN_AI_SLOP = "与此同时他走了出去。值得一提的是天还没亮。" * 30
# 干净文本：无任何禁用词
GEN_CLEAN = "风从巷口灌进来。他攥紧手里的纸条。脚步声越来越近了。" * 30


def test_author_profile_faithful_craft_not_penalized():
    """有作者档时，忠实复刻高频工艺签名词 → 维度7 不被拉低（频率≈ref 得高分）。"""
    rep = se.evaluate(REF_CRAFT_HEAVY, GEN_CRAFT_FAITHFUL, has_author_profile=True)
    d7 = _dim7(rep)
    # 复刻频率与参考频率接近 → 工艺子项满分；无 AI 套话 → AI 子项满分 → 维度7 高分
    assert d7["score"] >= 90.0, d7
    assert rep["has_author_profile"] is True


def test_author_profile_ai_slop_still_penalized():
    """有作者档也不放行 AI 结构套话 → 维度7 仍被硬扣（与此同时/值得一提的是）。"""
    rep = se.evaluate(REF_CRAFT_HEAVY, GEN_AI_SLOP, has_author_profile=True)
    d7 = _dim7(rep)
    # gen 大量 AI 套话 → AI 子项 0；且 craft 频率远离 ref（gen 无 craft）→ craft 子项也低
    assert d7["score"] <= 30.0, d7


def test_author_profile_better_than_no_profile_for_signature_words():
    """同一段忠实复刻签名词的文本：有作者档分应显著高于无作者档（这就是 bug 的核心）。"""
    rep_author = se.evaluate(REF_CRAFT_HEAVY, GEN_CRAFT_FAITHFUL,
                             has_author_profile=True)
    rep_none = se.evaluate(REF_CRAFT_HEAVY, GEN_CRAFT_FAITHFUL,
                           has_author_profile=False)
    s_author = _dim7(rep_author)["score"]
    s_none = _dim7(rep_none)["score"]
    # 无作者档：全集单边硬扣（gen 60 处 craft 命中）→ 被扣到 0
    assert s_none <= 5.0, rep_none
    # 有作者档：不被拉低
    assert s_author >= 90.0, rep_author
    assert s_author > s_none + 50.0


def test_no_author_profile_preserves_legacy_single_sided_penalty():
    """无作者档时维度7 保持旧行为：禁用词命中即单边硬扣（1 - hit*0.05）。"""
    gen = "他顿时心中一凛。" * 10  # craft 命中：顿时 + 心中一凛 各 10
    rep = se.evaluate(REF_CRAFT_HEAVY, gen, has_author_profile=False)
    d7 = _dim7(rep)
    g_profile = analyze_text(gen)
    hit = sum(g_profile.get("banned_word_hits", {}).values())
    assert hit > 0
    expected = max(0.0, 1.0 - hit * 0.05) * 100
    assert abs(d7["score"] - expected) < 0.5, (d7, expected)


def test_no_author_profile_clean_text_full_score():
    """无禁用词时维度7 满分（两个分支都应满分）。"""
    rep_none = se.evaluate(REF_CRAFT_HEAVY, GEN_CLEAN, has_author_profile=False)
    rep_author = se.evaluate(REF_CRAFT_HEAVY, GEN_CLEAN, has_author_profile=True)
    # 无作者档：0 命中 → 满分
    assert _dim7(rep_none)["score"] == 100.0, rep_none
    # 有作者档：AI 子项满分；craft 子项 = gen 频率(0) vs ref 频率(高) → 偏离，但不该是 0
    # （这里 ref craft 频率高、gen 为 0 → craft 子项会扣，但 AI 子项满分 → 维度7 ≈ 50）
    d7a = _dim7(rep_author)
    assert d7a["score"] >= 40.0, d7a


def test_baseline_auto_infers_author_profile():
    """提供 baseline 风格 JSON 时自动推断 has_author_profile=True（默认 None）。"""
    baseline = {"quantitative": {"dialogue_ratio": {"mean": 0.3}}}
    rep = se.evaluate(REF_CRAFT_HEAVY, GEN_CRAFT_FAITHFUL, baseline=baseline)
    assert rep["has_author_profile"] is True
    # 自动推断生效 → 忠实复刻签名词不被拉低
    assert _dim7(rep)["score"] >= 90.0, rep


def test_no_baseline_no_flag_defaults_no_author_profile():
    """无 baseline 且未显式传 flag → 默认无作者档（向后兼容旧行为）。"""
    rep = se.evaluate(REF_CRAFT_HEAVY, GEN_CRAFT_FAITHFUL)
    assert rep["has_author_profile"] is False
    # 默认走全集单边硬扣 → 复刻 craft 词被扣到 0
    assert _dim7(rep)["score"] <= 5.0, rep


def test_craft_freq_helper_normalizes_by_chars():
    """_craft_freq_per_1000 必须按字数归一化（不同长度文本可比）。"""
    short = analyze_text("他淡淡地说。" * 5)    # 5 处淡淡，短文本
    long = analyze_text("他淡淡地说。" * 5 + "无关内容。" * 200)  # 同 5 处淡淡，长文本
    f_short = se._craft_freq_per_1000(short, [short])
    f_long = se._craft_freq_per_1000(long, [long])
    # 同样 5 处命中，长文本字数多 → 每千字频率应更低
    assert f_short > f_long, (f_short, f_long)
    assert f_long >= 0.0


def test_two_word_classes_are_disjoint():
    """前置不变量：AI 结构套话与工艺签名词两类互斥（修复逻辑依赖此分类）。"""
    assert set(AI_STRUCTURAL_BANNED).isdisjoint(set(CRAFT_SIGNATURE_BANNED))
