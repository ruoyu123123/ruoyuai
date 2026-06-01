"""distill_finalize_verify strict 闸门轴错配回归测试 — 守护 2026-05-30 修 #5。

旧 bug：strict 可估算维 estimable=[0,1,3,4] 含 emotion(index1)。但本脚本 _estimate_emotion
产**情绪极性/valence**(pos/(pos+neg)·0全负1全正)，参考侧 emotion_curve_normalized 由
arc_aggregator 产**情绪强度/intensity+节奏**(pacing 快0.8/中0.5/慢0.3 + dim33 节拍强度)。
cluster_evaluator.score_emotion_curve 对两条正交轴做 cosine → 无测量学意义 → 随机误拦/误放
贴合作者节奏的 skill。修复(a)：把 emotion(index1) 移出 strict estimable，与 continuity(2)/
voice_pack(5) 一致。2026-06-01 修 #6 再移出 arc(0)（金标准三重 FAIL·见下），只留 kicker(3)/scene(4)。

本测试断言：
  · STRICT_ESTIMABLE_IDX 恰为 (3,4)，不含 emotion(1) 与 arc(0)；
  · 索引顺序与 cluster_evaluator.DIM_LABELS 对齐（emotion 在 index1、kicker/scene 在 3/4）；
  · emotion/arc 维不通过但 kicker/scene 全过 → strict_ok=True（旧 bug 会误拦）；
  · kicker/scene 任一不过 → strict_ok=False（hard 维仍守住）；
  · schema 异常（非 6 维）退化为全维都算、不抛 IndexError。

只测确定性纯函数 strict_gate_decision，不碰 LLM/subprocess/agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_finalize_verify as dfv
import cluster_evaluator as ce


def _row(dim, score):
    """造一行 cluster_evaluator scores_by_dim 格式的维度记录"""
    return {"dim": dim, "score": round(score, 3), "passes": score >= 0.7, "detail": {}}


def _six_dim_report(scores):
    """按 DIM_LABELS 顺序造 6 维报告。scores 长度必须 6。"""
    assert len(scores) == 6
    return {"scores_by_dim": [_row(ce.DIM_LABELS[i], scores[i]) for i in range(6)]}


# ---- 索引对齐：守护「emotion 在 index1、可估算 3 维在 0/3/4」 ----

def test_strict_estimable_idx_excludes_emotion_and_arc():
    """strict 可估算维恰为 kicker(3)/scene(4)，不含 emotion(1) 与 arc(0)。"""
    assert dfv.STRICT_ESTIMABLE_IDX == (3, 4), dfv.STRICT_ESTIMABLE_IDX
    assert 1 not in dfv.STRICT_ESTIMABLE_IDX, "emotion(index1) 不能进 strict（valence/intensity 轴错配）"
    assert 0 not in dfv.STRICT_ESTIMABLE_IDX, "arc(index0) 不能进 strict（shape 从 estimate emotion 拟合·金标准三重 FAIL·2026-06-01 修#6）"


def test_dim_labels_order_assumption_holds():
    """硬锁 cluster_evaluator.DIM_LABELS 顺序——索引假设的事实依据。
    一旦上游重排维度顺序，此测试先红，提醒同步 STRICT_ESTIMABLE_IDX。"""
    assert ce.DIM_LABELS[0] == "arc 形状"
    assert ce.DIM_LABELS[1] == "emotion_curve 偏离"
    assert ce.DIM_LABELS[3] == "钩子分布"
    assert ce.DIM_LABELS[4] == "场景概述比"


# ---- 核心回归：emotion 不过不应误拦 ----

def test_emotion_and_arc_fail_does_not_block_when_estimable_pass():
    """核心回归：emotion(1)/arc(0) 因轴错配/同源不可靠恒可能低分，但 kicker/scene 全过 → strict 放行。
    旧 bug（estimable 含 index1/index0）会因 emotion/arc 低分误拦贴合作者风格的 skill。"""
    # arc=0.0(低·estimate-shape 不可比) emotion=0.1(低) continuity=0.5 kicker=0.8 scene=0.75 voice=0.5
    report = _six_dim_report([0.0, 0.1, 0.5, 0.8, 0.75, 0.5])
    strict_ok, estimable = dfv.strict_gate_decision(report)
    assert strict_ok is True, "emotion/arc 低分不应拦截（已移出 strict）"
    # 只抽 2 维（kicker/scene），不含 emotion/arc 维标签
    assert len(estimable) == 2
    dims = {r["dim"] for r in estimable}
    assert ce.DIM_LABELS[1] not in dims, dims
    assert ce.DIM_LABELS[0] not in dims, dims


def test_continuity_voice_fail_does_not_block():
    """continuity(2)/voice(5) 恒中性也已在 strict 外——它们低分不应拦。"""
    report = _six_dim_report([0.9, 0.9, 0.0, 0.8, 0.75, 0.0])
    strict_ok, _ = dfv.strict_gate_decision(report)
    assert strict_ok is True


# ---- hard 维仍守住 ----

def test_arc_fail_does_not_block():
    """arc(0) 不过但 kicker/scene 过 → strict_ok=True（arc 2026-06-01 移出·estimate-shape 金标准三重 FAIL）。"""
    report = _six_dim_report([0.0, 0.9, 0.9, 0.8, 0.75, 0.9])
    strict_ok, _ = dfv.strict_gate_decision(report)
    assert strict_ok is True


def test_kicker_fail_blocks():
    """kicker(3) 不过 → strict_ok=False。"""
    report = _six_dim_report([0.9, 0.9, 0.9, 0.4, 0.75, 0.9])
    strict_ok, _ = dfv.strict_gate_decision(report)
    assert strict_ok is False


def test_scene_fail_blocks():
    """scene(4) 不过 → strict_ok=False。"""
    report = _six_dim_report([0.9, 0.9, 0.9, 0.8, 0.5, 0.9])
    strict_ok, _ = dfv.strict_gate_decision(report)
    assert strict_ok is False


def test_all_estimable_pass():
    """kicker/scene 全过（arc/emotion/其余随意）→ strict_ok=True。"""
    report = _six_dim_report([0.0, 0.0, 0.0, 0.71, 0.71, 0.0])
    strict_ok, estimable = dfv.strict_gate_decision(report)
    assert strict_ok is True
    assert len(estimable) == 2


# ---- 边界：schema 异常不崩 ----

def test_none_report_not_strict_ok():
    """report=None → 无可估算维 → strict_ok=False（不放行空报告）。"""
    strict_ok, estimable = dfv.strict_gate_decision(None)
    assert strict_ok is False
    assert estimable == []


def test_empty_report_not_strict_ok():
    """空 scores_by_dim → strict_ok=False。"""
    strict_ok, estimable = dfv.strict_gate_decision({"scores_by_dim": []})
    assert strict_ok is False
    assert estimable == []


def test_non_six_dim_degrades_to_all_dims():
    """schema 非 6 维（如未来扩/缩维或 fallback）→ 退化为全维都算，不抛 IndexError。"""
    # 3 维全过
    report = {"scores_by_dim": [_row("a", 0.9), _row("b", 0.8), _row("c", 0.75)]}
    strict_ok, estimable = dfv.strict_gate_decision(report)
    assert strict_ok is True
    assert len(estimable) == 3
    # 3 维有一个不过 → False
    report2 = {"scores_by_dim": [_row("a", 0.9), _row("b", 0.4), _row("c", 0.75)]}
    strict_ok2, _ = dfv.strict_gate_decision(report2)
    assert strict_ok2 is False
