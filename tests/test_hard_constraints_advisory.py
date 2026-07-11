#!/usr/bin/env python3
"""build_manifest hard constraints 的北极星④/⑤契约测试。

风格量化项保持 advisory；低对话作者基线不被通用地板抬高；机械章节字数目标不注入
writer，只有作者档定义的字数区间可作为 advisory。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402


class _FakeScanner:
    """最小 DatabaseScanner 替身：只需 has_style_profile + load。"""

    def __init__(self, style):
        self._style = style

    def has_style_profile(self):
        return bool(self._style)

    def load(self, key, default=None):
        if key == "作者风格":
            return self._style
        return default if default is not None else {}


def _hc(style):
    s = _FakeScanner(style)
    return bm._build_hard_constraints(s, {"tier1_due_count": 0, "must_reveal_this_ch": 0})


def test_dialogue_ratio_no_0_3_floor():
    """🔴 Bug2：低对话作者(mean=0.1)不再被 0.3 地板抬高·relax-only(0.1-0.15→clamp 0%)。"""
    hc = _hc({"quantitative": {"dialogue_ratio": {"mean": 0.1}}})
    dia = [x for x in hc if "对话占比" in x]
    assert dia, "应有对话占比项"
    assert "≥ 30%" not in dia[0], f"低对话作者(0.1)不该被 0.3 地板抬到 30%: {dia[0]}"
    assert "≥ 0%" in dia[0], f"relax-only 应得 ≥ 0%: {dia[0]}"
    assert "advisory" in dia[0], f"对话占比应标 advisory: {dia[0]}"
    # 高对话作者(0.5)仍正常下发(0.5-0.15=0.35→35%)·relax 不影响高对话
    hc2 = _hc({"quantitative": {"dialogue_ratio": {"mean": 0.5}}})
    dia2 = [x for x in hc2 if "对话占比" in x][0]
    assert "≥ 35%" in dia2, f"高对话作者(0.5)应得 ≥35%: {dia2}"


def test_style_quant_marked_advisory():
    """🔴 Bug1：风格量化项(句长/逗句比)标 advisory·writer 可区分非 hard_gate。"""
    hc = _hc({"quantitative": {
        "sentence_length": {"mean": 31, "std": 10},
        "punctuation_density_per_1000": {"comma_period_ratio": 2.5},
    }})
    sl = [x for x in hc if "句长均值" in x]
    cpr = [x for x in hc if "逗句比" in x]
    assert sl and "advisory" in sl[0], f"句长应标 advisory: {sl}"
    assert cpr and "advisory" in cpr[0], f"逗句比应标 advisory: {cpr}"


def test_word_target_advisory_freestyle():
    """🔴 2026-07-05 纯 freestyle 契约：机械「字数目标」整体清除·作者档章节字数仅 advisory。

    历史：Bug3 修法曾把「字数目标 {words_per_chapter} 字」标 advisory 保留注入；本轮
    纯 freestyle 升级（与 gen_writer expand/FREESTYLE_MIN_CJK 清除同批）把该机械目标
    从 hard_constraints 整体删除——字数由内容密度自然涌现，splitter 按字数切在下游承接
    （北极星④⑤）。防复活锁：任何来源的「字数目标」字样不得回到 hard_constraints；
    作者档 chapter_words 派生的「章节字数 low-high」区间是唯一字数信号且必标 advisory。
    """
    hc = _hc({"quantitative": {"chapter_words": {"mean": 3500}}})
    wt = [x for x in hc if "字数目标" in x]
    assert not wt, f"机械字数目标已整体清除(纯 freestyle)·不得复活: {wt}"
    cw = [x for x in hc if "章节字数" in x]
    assert cw and "advisory" in cw[0], f"作者档章节字数应存在且标 advisory: {cw}"
    assert "3000-4000" in cw[0], f"章节字数区间应为 mean±500: {cw[0]}"
    # 无作者档 → 不注入任何字数项（不回退到 words_per_chapter 机械默认）
    hc0 = _hc({})
    assert not [x for x in hc0 if "字数" in x], f"无作者档不得注入任何字数约束: {hc0}"
    # 源码级防复活锁：build_manifest 不得再引用 进度.words_per_chapter 机械目标
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    assert "words_per_chapter" not in src, "words_per_chapter 机械字数目标不得复活"
    assert "字数目标" not in src, "「字数目标」注入不得复活"


def test_plot_constraints_stay_hard():
    """剧情硬约束(Tier-1回收/secrets/locked_facts)保持·不被误标 advisory(真 hard_gate 邻域)。"""
    s = _FakeScanner({})
    hc = bm._build_hard_constraints(s, {"tier1_due_count": 2, "must_reveal_this_ch": 1})
    locked = [x for x in hc if "locked_facts" in x]
    assert locked and "advisory" not in locked[0], f"locked_facts 应保持硬约束: {locked}"
    tier1 = [x for x in hc if "Tier-1" in x]
    assert tier1 and "advisory" not in tier1[0], f"Tier-1 回收应保持硬约束: {tier1}"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
