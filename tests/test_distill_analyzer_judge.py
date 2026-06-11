#!/usr/bin/env python3
"""novel-distill-analyzer judge 注册 + agent .md 契约测试（阶段3 phase-1·北极星⑤不裁维度）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import judge_runner as jr  # noqa: E402


def test_distill_analyzer_registered():
    spec = jr.AGENT_SPECS.get("novel-distill-analyzer")
    assert spec is not None, "novel-distill-analyzer 未注册"
    assert spec.failure_policy == "block"   # surface JSON 是蒸馏链源头
    # 🔴 唯一 needs_author_profile=False：它产作者档不是消费
    assert spec.needs_author_profile is False
    assert spec.output_template == "蒸馏进度/cluster_{key}_surface.json"


def test_required_keys_only_3_toplevel_not_48dims():
    """🔴 北极星⑤：required_keys 只 3 个顶层结构键·绝不逐项列 48 dim（弱模型凑键产空壳=
    惊悚乐园流水账覆辙）。"""
    spec = jr.AGENT_SPECS["novel-distill-analyzer"]
    assert spec.required_keys == ("quantitative", "qualitative_dims", "golden_paragraphs")
    assert len(spec.required_keys) == 3, "required_keys 超 3 个 → 可能在硬卡维度"
    # 确认没有把 dim16/dim18 等**编号**维度列进结构校验（qualitative_dims 是合法顶层容器键·
    # 查 dim+数字 才是逐维硬卡）
    import re
    for k in spec.required_keys:
        assert not re.search(r"dim\d", k), f"required_keys 含逐维 {k}（违北极星⑤不裁维度）"


def test_agent_md_exists_and_contracts():
    p = jr.load_agent_system_prompt("novel-distill-analyzer")
    assert len(p) > 500
    # 48 维输出结构
    assert "qualitative_dims" in p and "golden_paragraphs" in p and "anti_patterns" in p
    # 四硬契约 + 绝不裁维度纪律
    assert "四硬契约" in p
    assert "提示不是约束" in p          # 枚举是提示不是约束
    assert "原文逐字" in p or "逐字摘录" in p   # 黄金段落不脑补
    # cluster 衔接（must_fix#2：arc_aggregator 消费 continuity）
    assert "continuity" in p


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
