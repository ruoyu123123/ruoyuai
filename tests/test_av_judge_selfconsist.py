"""av_judge.py 自一致性多数票聚合测试 — 稳单次判别方差（北极星①⑤⑥）。

根因（arxiv 实证）：即便配对相对判别比绝对打分稳，单次 LLM-judge 对网文隐性风格仍失准
（创意写作域约 1/4 难例翻转）——同一对 (A,B) 判两次可能一次「走味」一次「命中」。治法 =
self-consistency：同一配对渲染 N 个独立投票任务（novel-av-judge 逐票独立判别 ·
distill_av_verify 两段式渲染/验收），4 维**各取多数票**做 robust 聚合 + 暴露方差
（透明 · advisory 不黑箱）。

纪律：只测**确定性逻辑**（env 解析 / 多数票聚合 / 方差指标 / 报告平铺）——av_judge 是
  纯确定性库，零模型调用。平票偏命中（保守不误伤真作者）· 永远 advisory 永不 hard_gate
  （N=1 退化单票 = 关聚合逃生口）。

测试覆盖：[A] AV_JUDGE_N_SAMPLES env 解析（默认 3 / 1=关 / 钳上限 / 非法回退）；
  [C] aggregate_verdicts 多数票（严格过半判走味 / 平票偏命中 / 全弃权 None / 方差指标）；
  [F] build_report 平铺方差指标（advisory 不黑箱 · 不 hard_gate）；
  [G] swap 分配 + intent_dim 确定性契约（默认关零回归 · parse 只认 4 维）。
"""
import importlib
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import av_judge as av  # noqa: E402

_DIM_NAMES = ["词汇选择", "句法", "话语连接词", "语用语气"]


# ════════════════════════════════════════════════════════════════
# 样本构造基础设施
# ════════════════════════════════════════════════════════════════

def _reply(verdicts, parse_ok=True):
    """构造一份 verdict 回复 JSON（verdicts: dim→verdict 字符串）。parse_ok=False → 坏 JSON。"""
    if not parse_ok:
        return "not json at all"
    dims = {n: {"verdict": verdicts.get(n, av.MATCH_VERDICT), "reason": f"{n}理由"}
            for n in _DIM_NAMES}
    return "```json\n" + json.dumps({"dimensions": dims}, ensure_ascii=False) + "\n```"


def _parsed(verdicts, parse_ok=True):
    """直接产 parse_av_verdicts 结构（聚合测试用 · 跳过回复构造）。"""
    return av.parse_av_verdicts(_reply(verdicts, parse_ok))


def _reload(n):
    """以指定 AV_JUDGE_N_SAMPLES reload av_judge（env 在函数内读 · reload 求稳）。"""
    if n is None:
        os.environ.pop("AV_JUDGE_N_SAMPLES", None)
    else:
        os.environ["AV_JUDGE_N_SAMPLES"] = str(n)
    importlib.reload(av)
    return av


# ════════════════════════════════════════════════════════════════
# [A] AV_JUDGE_N_SAMPLES env 解析（=投票任务数）
# ════════════════════════════════════════════════════════════════

def test_A_default_is_3():
    """AV_JUDGE_N_SAMPLES 未设 → 默认 3（N≥2 真聚合稳方差 · 质量优先）。"""
    g = _reload(None)
    try:
        assert g._n_samples() == 3
        assert g.AV_JUDGE_N_SAMPLES_DEFAULT == 3
    finally:
        _reload(None)


def test_A_one_is_off():
    """AV_JUDGE_N_SAMPLES=1 → 退回单票（关聚合逃生口）。"""
    try:
        assert _reload("1")._n_samples() == 1
    finally:
        _reload(None)


def test_A_explicit_5():
    try:
        assert _reload("5")._n_samples() == 5
    finally:
        _reload(None)


def test_A_clamps_to_max():
    """超 AV_JUDGE_N_SAMPLES_MAX 钳到上限（防投票任务数失控）。"""
    try:
        g = _reload("99")
        assert g._n_samples() == g.AV_JUDGE_N_SAMPLES_MAX == 7
    finally:
        _reload(None)


def test_A_below_one_clamps_to_one():
    """< 1（0 / 负）钳到 1（关聚合 · 不会变 0 票空跑）。"""
    for v in ("0", "-3"):
        try:
            assert _reload(v)._n_samples() == 1, v
        finally:
            _reload(None)


def test_A_garbage_falls_back_default():
    """空 / 非法值回退默认 3 · 不崩。"""
    for v in ("", "abc", "x", "true", "3.5"):
        try:
            assert _reload(v)._n_samples() == 3, v
        finally:
            _reload(None)


# ════════════════════════════════════════════════════════════════
# [C] aggregate_verdicts 多数票 robust 聚合 + 方差透明
# ════════════════════════════════════════════════════════════════

def test_C_majority_drift_strictly_over_half():
    """某维 drift 票严格过半 → 聚合判走味（2/3 走味 → drift）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert "词汇选择" in agg["drift_dims"]
    assert agg["dimensions"]["词汇选择"]["drift"] is True
    assert agg["dimensions"]["词汇选择"]["votes"] == {"drift": 2, "match": 1, "abstain": 0}


def test_C_minority_drift_not_flagged():
    """drift 票未过半（1/3）→ 聚合判命中（不误报）。"""
    s = [_parsed({"句法": av.DRIFT_VERDICT}), _parsed({}), _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert "句法" not in agg["drift_dims"]
    assert agg["dimensions"]["句法"]["drift"] is False


def test_C_tie_conservative_match():
    """平票（2 走味 2 命中）→ 偏命中（保守 · 不误伤真作者 · 北极星⑤）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}), _parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({}), _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert agg["dimensions"]["词汇选择"]["drift"] is False
    assert agg["drift_dims"] == []
    assert agg["dimensions"]["词汇选择"]["votes"] == {"drift": 2, "match": 2, "abstain": 0}


def test_C_unanimous_drift():
    """N 票全判走味 → drift · agreement=1.0 · 不算 flipped（无分歧）。"""
    s = [_parsed({"语用语气": av.DRIFT_VERDICT}) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    assert "语用语气" in agg["drift_dims"]
    assert agg["dimensions"]["语用语气"]["agreement"] == 1.0
    assert agg["dimensions"]["语用语气"]["flipped"] is False
    assert "语用语气" not in agg["unstable_dims"]


def test_C_flipped_marks_unstable():
    """同一维 N 票里走味和命中都出现过 → flipped=True · 进 unstable_dims（方差透明）。"""
    s = [_parsed({"句法": av.DRIFT_VERDICT}), _parsed({}), _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert agg["dimensions"]["句法"]["flipped"] is True
    assert "句法" in agg["unstable_dims"]


def test_C_agreement_value():
    """agreement = 多数派 / 有效票（2 走味 1 命中 → 0.667）· 越低越不稳。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}), _parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert agg["dimensions"]["词汇选择"]["agreement"] == round(2 / 3, 3)
    assert agg["agreement_by_dim"]["词汇选择"] == round(2 / 3, 3)


def test_C_mean_agreement_top_level():
    """顶层 mean_agreement = 各有判别维度 agreement 均值（一眼看这次稳不稳）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({"词汇选择": av.DRIFT_VERDICT, "句法": av.DRIFT_VERDICT}),
         _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert 0.0 < agg["mean_agreement"] <= 1.0
    # 全一致的对照：mean_agreement=1.0
    s2 = [_parsed({n: av.MATCH_VERDICT for n in _DIM_NAMES}) for _ in range(3)]
    assert av.aggregate_verdicts(s2)["mean_agreement"] == 1.0


def test_C_all_abstain_dim_none():
    """某维 N 票全缺（弃权）→ verdict=None / drift=False（不臆造走味）。"""
    # 回复全缺 句法（只给 词汇选择）
    reply = json.dumps({"dimensions": {"词汇选择": {"verdict": av.DRIFT_VERDICT}}},
                       ensure_ascii=False)
    s = [av.parse_av_verdicts(reply) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    assert agg["dimensions"]["句法"]["verdict"] is None
    assert agg["dimensions"]["句法"]["drift"] is False
    assert agg["dimensions"]["句法"]["votes"]["abstain"] == 3


def test_C_n_valid_excludes_unparsed():
    """parse_ok=False 的票不计入 n_valid（坏 JSON 不污染聚合）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}), _parsed({}, parse_ok=False),
         _parsed({"词汇选择": av.DRIFT_VERDICT})]
    agg = av.aggregate_verdicts(s)
    assert agg["n_samples"] == 3
    assert agg["n_valid_samples"] == 2  # 坏 JSON 那条不算
    # 2 个有效票都判 词汇 走味 → drift
    assert "词汇选择" in agg["drift_dims"]


def test_C_empty_samples_no_crash():
    """空票列表不崩 · parse_ok=False · 4 维齐全 · drift_dims 空。"""
    agg = av.aggregate_verdicts([])
    assert agg["parse_ok"] is False
    assert set(agg["dimensions"]) == set(_DIM_NAMES)
    assert agg["drift_dims"] == []
    assert agg["n_valid_samples"] == 0


def test_C_sample_drift_dims_transparency():
    """sample_drift_dims 留每票原始 drift（复盘可核单票判别 · 不黑箱）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({"句法": av.DRIFT_VERDICT}),
         _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert agg["sample_drift_dims"] == [["词汇选择"], ["句法"], []]


# ════════════════════════════════════════════════════════════════
# [F] build_report 平铺方差指标（advisory 不黑箱 · 永不 hard_gate）
# ════════════════════════════════════════════════════════════════

def test_F_report_carries_variance_metrics():
    """聚合结果进 build_report → 方差指标平铺进报告（n_samples / mean_agreement / unstable_dims）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({"词汇选择": av.DRIFT_VERDICT}), _parsed({})]
    agg = av.aggregate_verdicts(s)
    r = av.build_report("active", agg, "a.txt", "b.txt")
    assert r["n_samples"] == 3
    assert r["n_valid_samples"] == 3
    assert "mean_agreement" in r
    assert r["agreement_by_dim"]["词汇选择"] == round(2 / 3, 3)


def test_F_consistency_note_when_unstable():
    """有分歧维度 → 报告带 consistency_note（提示人工复核 · 透明不黑箱）。"""
    s = [_parsed({"句法": av.DRIFT_VERDICT}), _parsed({}), _parsed({})]
    agg = av.aggregate_verdicts(s)
    r = av.build_report("active", agg, "a.txt", "b.txt")
    assert "consistency_note" in r
    assert "复核" in r["consistency_note"]


def test_F_report_still_advisory_with_aggregation():
    """聚合后报告仍 advisory · code AV_TRAIT_DRIFT（北极星⑤ · 稳方差≠强判）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    for mode in ("shadow", "active"):
        r = av.build_report(mode, agg, "a.txt", "b.txt")
        assert r["gate_level"] == "advisory"
        assert r["issue_code"] == "AV_TRAIT_DRIFT"


def test_F_aggregated_active_reports_majority_drift_as_advisory():
    """active：聚合后多数票走味维度作 advisory 待裁决项上报（仍可豁免）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT, "句法": av.DRIFT_VERDICT}) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    r = av.build_report("active", agg, "a.txt", "b.txt")
    assert r["verdict"] == "drift"
    assert len(r["issues"]) == 2
    for iss in r["issues"]:
        assert iss["gate_level"] == "advisory"


def test_F_code_not_in_hard_gate():
    """AV_TRAIT_DRIFT 绝不进 audit_hub.HARD_GATE_CODES（自一致性聚合不改这条铁律）。"""
    try:
        import audit_hub  # noqa: E402
    except Exception:
        return
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    assert av.ISSUE_CODE not in codes


# ════════════════════════════════════════════════════════════════
# [G] G2-CYCLIC swap 分配 + intent_dim 确定性契约（默认关零回归）
# ════════════════════════════════════════════════════════════════

def test_G_position_swap_default_off():
    """AV_JUDGE_POSITION_SWAP 未设 → off → _swap_assignment 全 False（投票任务全原向）。"""
    g = _reload(None)
    try:
        assert g._position_swap_on() is False
        assert g._swap_assignment(3, g._position_swap_on()) == [False, False, False]
    finally:
        _reload(None)


def test_G_n1_never_swaps_even_when_on():
    """N=1 即便 swap_on 也退化为 0 个 swap（单票无从对称硬纪律）。"""
    assert av._swap_assignment(1, swap_on=True) == [False]
    assert av._swap_assignment(1, swap_on=False) == [False]


def test_G_intent_dim_default_off_4_dims():
    """build_av_judge_prompt 默认 include_intent_dim=False → 仍 4 维 · 不含「作者思维」。"""
    p = av.build_av_judge_prompt("AUTH", "REP")
    assert "作者思维" not in p
    # 输出 JSON schema 仍只列 4 维键名
    for n in _DIM_NAMES:
        assert f'"{n}"' in p
    assert "4 个解耦特质维度" in p


def test_G_intent_dim_on_adds_fifth_dim():
    """include_intent_dim=True → 输出含「作者思维」第 5 维 · 4 维键名仍齐全（不挤掉原维）。"""
    p = av.build_av_judge_prompt("AUTH", "REP", include_intent_dim=True)
    assert "作者思维" in p
    assert "5 个解耦特质维度" in p
    for n in _DIM_NAMES:
        assert f'"{n}"' in p


def test_G_intent_dim_ask_no_author_rationale_leak():
    """INTENT_DIM 的 ask/说明不含已知 author_decision_principles 聚合文案（切断复述捷径 · R3 P0-IR-1）。"""
    _name, desc, ask = av.INTENT_DIM
    blob = desc + ask
    # 已知 B1-B3 聚合会出现的 rationale 关键词绝不能泄进中性维度定义
    for leak in ("母题", "胜利代价藏悲凉", "the_why", "gap_filled", "per_scene_rationale"):
        assert leak not in blob, leak


def test_G_parse_still_only_4_dims_with_intent_on():
    """include_intent_dim=True 时 judge 回了 5 维，parse_av_verdicts 仍只认 4 维（intent 维不进聚合判决）。"""
    reply = json.dumps({"dimensions": {
        **{n: {"verdict": av.MATCH_VERDICT} for n in _DIM_NAMES},
        "作者思维": {"verdict": av.DRIFT_VERDICT, "reason": "决策走向不像"},
    }}, ensure_ascii=False)
    parsed = av.parse_av_verdicts(reply)
    assert set(parsed["dimensions"]) == set(_DIM_NAMES)  # 只 4 维
    assert "作者思维" not in parsed["dimensions"]
    assert parsed["drift_dims"] == []  # 第 5 维 drift 不污染 4 维判决
