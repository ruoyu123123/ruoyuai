#!/usr/bin/env python3
"""批 2 剩余 4 bug 修复测试（arc_aggregator + character_arc_aggregator·2026-06-15 审计）。

triage 排除批2文件由主代理手动修(verify 铁证 + 明确 fix)：
- arc_aggregator L89 pacing 子串匹配按 dict 序·短 label「中」「快」先于「中快」「极快」→ 误判。→ longest-first
- arc_aggregator L589 climax 硬编码分母 10·cluster arc 变长(2/3/4 章)→ 位置失真甚至 >100%。→ arc 实际长度
- character_arc_aggregator L268 chapter 不强制 int·str/int 混入 → sorted/减法 TypeError 整角色崩。→ int 守卫
- character_arc_aggregator L222 short name(split[0]) key 与 continuity 完整名不一致 → 数据分裂+覆盖检查失效。→ 完整 name

chapter int/short name/climax 的端到端不崩由 test_cross_cluster_contract full_18 覆盖(跑这两个 aggregator)。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import arc_aggregator as a  # noqa: E402


def test_pacing_longest_first():
    """🔴 arc_aggregator pacing 最长优先匹配：原按 dict 插入顺序·短 label「中」「快」排在含它的
    「中快」「极快」前 → 中快误命中「中」(0.5)·应 0.65。修后 longest-first。"""
    assert a.parse_pacing_curve_to_values("Ch1中快", 1) == [0.65], "中快应 0.65(非短匹配 0.5)"
    assert a.parse_pacing_curve_to_values("Ch1极快", 1) == [0.95], "极快应 0.95(非短匹配 0.8)"
    assert a.parse_pacing_curve_to_values("Ch1中", 1) == [0.5], "中保持 0.5(短 label 正常)"
    assert a.parse_pacing_curve_to_values("Ch1快", 1) == [0.8], "快保持 0.8"
    # 多 chunk 复合曲线
    vals = a.parse_pacing_curve_to_values("Ch1中快→Ch2极快→Ch3中", 3)
    assert vals == [0.65, 0.95, 0.5], f"复合曲线 longest-first: {vals}"


# 注：climax 位置分母修复(_arc_len 用 chapters_count/chapter_range·非硬编码 10)由
# aggregate_summary(project) 读 arc_templates files·需完整 project 难单测·且
# average_climax_position_pct 写 arc_summary.json 当前无消费方(verify 标低影响诊断字段) →
# 由 test_cross_cluster_contract full_18 端到端验 arc_aggregator 不崩 + verify 铁证 fix 对覆盖。
# pacing 修复(影响 emotion_curve 下游 Reagan/climax)单测如上(高价值·明确可单测)。


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
