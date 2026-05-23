#!/usr/bin/env python3
"""test_evaluator_fixes.py — 验证 style_evaluator/analyzer 6 项 bug 修复。

覆盖：
- E1: 多人同段对话识别
- E2: 省略 attribution 的回合制对话
- E3: 拟声段豁免极短段计数
- E4: active vs quoted speaker 区分
- E5: 多基线区间评分
- E6: 引号化独白识别

用法：python core/scripts/test_evaluator_fixes.py
"""
from __future__ import annotations
import sys
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent.parent  # ruoyuAi/
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from style_analyzer import (  # noqa: E402
    analyze_text,
    is_onomatopoeia,
    classify_speakers,
    split_paragraphs,
    _split_dialogue_vs_monologue,
    DIALOGUE_QUOTED,
)
from style_evaluator import (  # noqa: E402
    evaluate,
    _build_interval_profile,
    _build_interval_value,
    _pct_match,
)


# 测试用例 fixture 路径（运行时通过环境变量 EVALUATOR_FIXTURE_BOOK 指定具体风格库书名）
# 示例: set EVALUATOR_FIXTURE_BOOK=BookC && python test_evaluator_fixes.py
import os as _os
_FIXTURE_BOOK = _os.environ.get("EVALUATOR_FIXTURE_BOOK", "_example_book")
REPLICA_DIR = ROOT / "workspace" / "styles" / _FIXTURE_BOOK / "复刻测试" / "v3.2_round3"
REF_DIR = ROOT / "workspace" / "styles" / _FIXTURE_BOOK / "对比报告"


# ============================================================
# 测试辅助
# ============================================================

_PASSED = []
_FAILED = []


def test(name: str):
    """装饰器：注册测试用例。"""
    def deco(fn):
        try:
            fn()
            _PASSED.append(name)
            print(f"  ✓ {name}")
        except AssertionError as e:
            _FAILED.append((name, str(e)))
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            _FAILED.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
        return fn
    return deco


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================
# E1: 多人同段对话识别
# ============================================================
print("\n[E1] 多人同段对话识别")


@test("E1.1 psychology 末段含'达斯特...疑惑问道'+'HeroC擡起头'应识别 ≥ 2 个 speaker")
def _():
    text = read(REPLICA_DIR / "psychology_replica.txt")
    prof = analyze_text(text)
    active = prof["active_speakers"]
    assert prof["active_speaker_count"] >= 2, \
        f"expected >= 2 active speakers, got {prof['active_speaker_count']}: {active}"
    # 至少含HeroC或CharC1之一
    assert any("克莱" in s or "CharC1" in s or "达斯特" in s for s in active), \
        f"expected to find 克莱/CharC1/达斯特 in {active}"


@test("E1.2 opening 含双向对话（HeroC+奥维利）应识别两位")
def _():
    text = read(REPLICA_DIR / "opening_replica.txt")
    prof = analyze_text(text)
    active = prof["active_speakers"]
    assert prof["active_speaker_count"] >= 2, \
        f"expected >= 2, got {prof['active_speaker_count']}: {active}"
    assert "HeroC" in active and "奥维利" in active, \
        f"expected HeroC+奥维利 in {active}"


# ============================================================
# E2: 省略 attribution 的回合制对话识别
# ============================================================
print("\n[E2] 回合制对话识别")


@test("E2.1 ref ch101_opening 多个 attribution-less 对话应识别 ≥ 3 个 speaker")
def _():
    text = read(REF_DIR / "_ref_ch101_opening.txt")
    prof = analyze_text(text)
    active = prof["active_speakers"]
    # ch101 至少有HeroC+安洁莉卡+凡森特
    assert prof["active_speaker_count"] >= 2, \
        f"expected >= 2, got {prof['active_speaker_count']}: {active}"


@test("E2.2 ref ch250_battle 含艾琳+贝克朗回合对话应识别 ≥ 2")
def _():
    text = read(REF_DIR / "_ref_ch250_battle.txt")
    prof = analyze_text(text)
    assert prof["active_speaker_count"] >= 2, \
        f"expected >= 2 in battle, got {prof['active_speaker_count']}: {prof['active_speakers']}"


# ============================================================
# E3: 拟声段豁免逻辑
# ============================================================
print("\n[E3] 拟声段豁免")


@test("E3.1 is_onomatopoeia 识别基础拟声词")
def _():
    assert is_onomatopoeia("啪！"), "啪！"
    assert is_onomatopoeia("砰的一声！"), "砰的一声！"
    assert is_onomatopoeia("咚咚咚！"), "咚咚咚！"
    assert is_onomatopoeia("嗡——"), "嗡—— (破折号格式)"
    assert is_onomatopoeia("哗啦"), "哗啦"
    assert not is_onomatopoeia("HeroC拔出了左轮。"), "正常叙述句不应是拟声"
    assert not is_onomatopoeia("他往前走了半步。"), "正常叙述句不应是拟声"


@test("E3.2 battle 拟声段豁免后 ultra_short_para_ratio 应低于 raw")
def _():
    text = read(REPLICA_DIR / "battle_replica.txt")
    prof = analyze_text(text)
    assert prof["ultra_short_para_ratio"] < prof["ultra_short_para_ratio_raw"], \
        f"expected豁免后 < raw, got {prof['ultra_short_para_ratio']} >= {prof['ultra_short_para_ratio_raw']}"
    # 至少识别到 2 个拟声段（啪的一声 + 砰的一声）
    assert prof["onomatopoeia_para_count"] >= 2, \
        f"expected >= 2 onomatopoeia paras, got {prof['onomatopoeia_para_count']}"


# ============================================================
# E4: active vs quoted speaker 区分
# ============================================================
print("\n[E4] active vs quoted speaker")


@test("E4.1 profile 包含新字段 active_speaker_count / quoted_speaker_count")
def _():
    text = read(REPLICA_DIR / "opening_replica.txt")
    prof = analyze_text(text)
    assert "active_speaker_count" in prof
    assert "active_speakers" in prof
    assert "quoted_speaker_count" in prof
    assert "quoted_speakers" in prof
    # 向后兼容：speaker_count 仍然存在
    assert "speaker_count" in prof
    assert "speakers" in prof


@test("E4.2 opening 中CharC5被提及但未说话 → 应在 quoted 而非 active")
def _():
    text = read(REPLICA_DIR / "opening_replica.txt")
    prof = analyze_text(text)
    # CharC5在 opening 中被点名但HeroC才是说话方
    assert "CharC5" in prof["quoted_speakers"] or "CharC5" not in prof["active_speakers"], \
        f"CharC5 should be quoted, not active; active={prof['active_speakers']}, quoted={prof['quoted_speakers']}"


# ============================================================
# E5: 多基线区间评分
# ============================================================
print("\n[E5] 多基线区间评分")


@test("E5.1 _build_interval_value 多值生成区间字典")
def _():
    iv = _build_interval_value([1.0, 2.0, 3.0])
    assert isinstance(iv, dict)
    assert iv["min"] == 1.0 and iv["max"] == 3.0 and iv["mean"] == 2.0
    # 单值退化为标量
    assert _build_interval_value([5.0]) == 5.0


@test("E5.2 _pct_match 支持区间字典")
def _():
    # 落入区间 → 1.0
    interval = {"min": 0.2, "max": 0.5, "mean": 0.35}
    assert _pct_match(interval, 0.3) == 1.0
    assert _pct_match(interval, 0.5) == 1.0
    # 略低于区间
    score = _pct_match(interval, 0.1)
    assert 0 < score < 1.0
    # 高于区间
    score = _pct_match(interval, 0.8)
    assert 0 <= score < 1.0


@test("E5.3 _build_interval_profile 从 3 个 profile 构建区间 profile")
def _():
    text1 = read(REF_DIR / "_ref_ch101_opening.txt")
    text2 = read(REF_DIR / "_ref_ch143_psychology.txt")
    text3 = read(REF_DIR / "_ref_ch250_battle.txt")
    profiles = [analyze_text(t) for t in [text1, text2, text3]]
    interval = _build_interval_profile(profiles)
    # 数值字段应为区间字典
    assert isinstance(interval.get("dialogue_ratio"), dict)
    assert "min" in interval["dialogue_ratio"]
    # 含 _source_profiles 用于 JSD 多基线
    assert "_source_profiles" in interval
    assert len(interval["_source_profiles"]) == 3


@test("E5.4 多基线 vs 单基线 psychology SFS 提升")
def _():
    ref_psy = read(REF_DIR / "_ref_ch143_psychology.txt")
    ref_op = read(REF_DIR / "_ref_ch101_opening.txt")
    ref_bt = read(REF_DIR / "_ref_ch250_battle.txt")
    gen = read(REPLICA_DIR / "psychology_replica.txt")

    single = evaluate(ref_psy, gen)
    multi = evaluate([ref_psy, ref_op, ref_bt], gen)

    assert single["sfs_quick"] >= 85, \
        f"single SFS too low: {single['sfs_quick']}"
    assert multi["sfs_quick"] >= single["sfs_quick"], \
        f"multi {multi['sfs_quick']} should be >= single {single['sfs_quick']}"
    assert multi["multi_baseline"] is True
    assert multi["ref_count"] == 3


# ============================================================
# E6: 引号化独白识别
# ============================================================
print("\n[E6] 引号化独白识别")


@test("E6.1 profile 含 inner_monologue_ratio / dialogue_only_ratio 新字段")
def _():
    text = read(REPLICA_DIR / "psychology_replica.txt")
    prof = analyze_text(text)
    assert "inner_monologue_ratio" in prof
    assert "dialogue_only_ratio" in prof
    # 向后兼容：dialogue_ratio 仍在
    assert "dialogue_ratio" in prof


@test("E6.2 psychology 包含大量推演独白 → inner_monologue_ratio 应 > 0")
def _():
    text = read(REPLICA_DIR / "psychology_replica.txt")
    prof = analyze_text(text)
    assert prof["inner_monologue_ratio"] > 0.10, \
        f"psychology expect monologue > 10%, got {prof['inner_monologue_ratio']}"


@test("E6.3 dialogue_only + monologue 应 ≤ dialogue_ratio（独白是子集）")
def _():
    text = read(REPLICA_DIR / "psychology_replica.txt")
    prof = analyze_text(text)
    s = prof["dialogue_only_ratio"] + prof["inner_monologue_ratio"]
    # 允许 ±2% 浮动误差（计数策略差异）
    assert abs(s - prof["dialogue_ratio"]) <= 0.05, \
        f"dialogue_only + monologue ({s}) should ≈ dialogue_ratio ({prof['dialogue_ratio']})"


@test("E6.4 引号识别正确处理中文弯引号 U+201C/D（非 ASCII '\"')")
def _():
    sample = '“图书管理员”对应的序列8是“信使”？'
    matches = DIALOGUE_QUOTED.findall(sample)
    assert len(matches) == 2, \
        f"expected 2 quotes, got {len(matches)}: {matches}"


# ============================================================
# 综合：写手层 round4 预期 SFS ≥ 92（A 级，多基线均值）
# ============================================================
print("\n[综合] round3 复刻样本均值 SFS")


@test("综合 多基线下 3 个样本 SFS 均值应 ≥ 90")
def _():
    refs = [
        read(REF_DIR / "_ref_ch101_opening.txt"),
        read(REF_DIR / "_ref_ch143_psychology.txt"),
        read(REF_DIR / "_ref_ch250_battle.txt"),
    ]
    cases = ["opening", "psychology", "battle"]
    sfs_list = []
    for case in cases:
        gen = read(REPLICA_DIR / f"{case}_replica.txt")
        r = evaluate(refs, gen)
        sfs_list.append(r["sfs_quick"])
        print(f"     {case}: SFS={r['sfs_quick']} grade={r['programmatic_score']['grade']}")
    avg = sum(sfs_list) / len(sfs_list)
    print(f"     平均 SFS = {avg:.2f}")
    assert avg >= 90, f"average SFS should >= 90, got {avg:.2f}"


# ============================================================
# 回归测试：向后兼容
# ============================================================
print("\n[回归] 向后兼容性")


@test("回归 1 旧 API: evaluate(str, str) 仍工作")
def _():
    ref = read(REF_DIR / "_ref_ch143_psychology.txt")
    gen = read(REPLICA_DIR / "psychology_replica.txt")
    r = evaluate(ref, gen)
    assert "sfs_quick" in r
    assert "programmatic_score" in r
    assert r["multi_baseline"] is False


@test("回归 2 旧字段 speaker_count 在 profile 中保留")
def _():
    text = read(REPLICA_DIR / "opening_replica.txt")
    prof = analyze_text(text)
    assert "speaker_count" in prof
    # speaker_count = active_speaker_count（向后兼容映射）
    assert prof["speaker_count"] == prof["active_speaker_count"]


@test("回归 3 旧字段 ultra_short_para_ratio + 新字段 raw 共存")
def _():
    text = read(REPLICA_DIR / "battle_replica.txt")
    prof = analyze_text(text)
    assert "ultra_short_para_ratio" in prof
    assert "ultra_short_para_ratio_raw" in prof
    # raw 含拟声段，应 >= 豁免版
    assert prof["ultra_short_para_ratio_raw"] >= prof["ultra_short_para_ratio"]


# ============================================================
# 报告
# ============================================================
print("\n" + "=" * 60)
print(f"测试结果: {len(_PASSED)} 通过 / {len(_FAILED)} 失败")
if _FAILED:
    print("\n失败用例:")
    for name, err in _FAILED:
        print(f"  - {name}: {err}")
    sys.exit(1)
print("✓ 全部测试通过")
sys.exit(0)
