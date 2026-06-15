#!/usr/bin/env python3
"""build_manifest._build_hard_constraints 北极星⑤/④ 修复测试（2026-06-15 Workflow 审计 confirmed）。

修 3 个 confirmed bug：
- Bug1(北极星⑤)：风格量化项(对话占比/句长/逗句比/字数)原无 advisory 标记混进字面 hard_constraints·
  被 critical_summary 框「必满足」=把 advisory 当 hard_gate。→ 全标 advisory 前缀。
- Bug2(北极星⑤c)：对话占比 max(0.3,..) 地板把低对话作者(描写型 mean=0.1)强行抬到 30%·覆盖作者基线。
  → relax-only max(0.0,..)。
- Bug3(北极星④)：freestyle_v27 仍注入每章字数目标硬约束(与 dcas_word_target=None 矛盾·间接锁字数)。
  → 标 advisory(splitter 按字数切·仅参考)。
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
    """🔴 Bug3：字数目标/章节字数标 advisory(freestyle splitter 按字数切)·非硬锁。"""
    hc = _hc({"quantitative": {"chapter_words": {"mean": 3500}}})
    wt = [x for x in hc if "字数目标" in x]
    cw = [x for x in hc if "章节字数" in x]
    assert wt and "advisory" in wt[0], f"字数目标应标 advisory: {wt}"
    assert cw and "advisory" in cw[0], f"章节字数应标 advisory: {cw}"


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
