"""av_judge.py 自一致性重采样测试 — Rating Roulette · 稳 LLM-judge 方差（北极星①⑤⑥ · 2026-05-31）。

根因（本批任务说明 · arxiv 实证）：av_judge 原本**单次**配对判别。即便配对相对判别比绝对打分稳，
单次 LLM-judge 对网文隐性风格仍失准（创意写作域约 1/4 难例翻转）——同一对 (A,B) 跑两次可能一次判
「走味」一次判「命中」，单次方差大。治法 = Rating Roulette / self-consistency：同 judge model 跑
N 次重采样（temperature 微抖），4 维**各取多数票**做 robust 聚合 + 暴露方差（透明 · advisory 不黑箱）。

纪律：只测**确定性逻辑**（env 解析 / temperature 抖动序列 / 多数票聚合 / 方差指标 / 报告平铺），
  LLM 调用**全 mock**（mock call_gen_model · 不实跑 gen-model 需 API）。无需 logprob（黑箱可用）。
  平票偏命中（保守不误伤真作者）· 永远 advisory 永不 hard_gate（N=1 退化单次 = 零回归）。

测试覆盖：[A] AV_JUDGE_N_SAMPLES env 解析（默认 3 / 1=关 / 钳上限 / 非法回退）；
  [B] temperature 抖动序列（确定性 · 第 0 次基准 · ±step 交替 · 钳区间）；
  [C] aggregate_verdicts 多数票（严格过半判走味 / 平票偏命中 / 全弃权 None / 方差指标）；
  [D] self_consistency_judge（N 次调用 / 温度抖动并恢复 / 全失败降级 / 部分失败仍聚合 / N=1 退化）；
  [E] pairwise_drift_count 走自一致性（best-of-N 复用 · 方差透明字段）；
  [F] build_report 平铺方差指标（advisory 不黑箱 · 不 hard_gate）。
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
# mock 基础设施
# ════════════════════════════════════════════════════════════════

class _P:
    """mock profile（带可写 temperature · 验抖动用）。"""
    def __init__(self, name="active", model="m", temperature=0.8):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = None
        self.api_key = "sk-test"
        self.base_url = "http://localhost/v1"


class _Loader:
    """mock loader：get_callable_profiles 返回给定 profile 列表。"""
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles

    def get_active_profile(self):
        return self._profiles[0]


def _reply(verdicts, parse_ok=True):
    """构造一份 LLM 回复 JSON（verdicts: dim→verdict 字符串）。parse_ok=False → 坏 JSON。"""
    if not parse_ok:
        return "not json at all"
    dims = {n: {"verdict": verdicts.get(n, av.MATCH_VERDICT), "reason": f"{n}理由"}
            for n in _DIM_NAMES}
    return "```json\n" + json.dumps({"dimensions": dims}, ensure_ascii=False) + "\n```"


def _parsed(verdicts, parse_ok=True):
    """直接产 parse_av_verdicts 结构（聚合测试用 · 跳过 LLM 回复构造）。"""
    return av.parse_av_verdicts(_reply(verdicts, parse_ok))


def _reload(n):
    """以指定 AV_JUDGE_N_SAMPLES reload av_judge（env 在函数内读 · reload 求稳）。"""
    if n is None:
        os.environ.pop("AV_JUDGE_N_SAMPLES", None)
    else:
        os.environ["AV_JUDGE_N_SAMPLES"] = str(n)
    importlib.reload(av)
    return av


def _mock_replies(replies):
    """返回一个 mock call_gen_model：每次调用按序吐 replies 里的一条（可含异常对象）。"""
    state = {"i": 0}

    def mock(loader, system, user, tag=""):
        r = replies[state["i"]]
        state["i"] += 1
        if isinstance(r, Exception):
            raise r
        return r, _P(), 0.1

    return mock, state


# ════════════════════════════════════════════════════════════════
# [A] AV_JUDGE_N_SAMPLES env 解析
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
    """AV_JUDGE_N_SAMPLES=1 → 退回单次单采样（关聚合 · 零回归逃生口）。"""
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
    """超 AV_JUDGE_N_SAMPLES_MAX 钳到上限（防 token / 时延失控）。"""
    try:
        g = _reload("99")
        assert g._n_samples() == g.AV_JUDGE_N_SAMPLES_MAX == 7
    finally:
        _reload(None)


def test_A_below_one_clamps_to_one():
    """< 1（0 / 负）钳到 1（关聚合 · 不会变 0 次空跑）。"""
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
# [B] temperature 抖动序列（确定性 · Rating Roulette 微抖）
# ════════════════════════════════════════════════════════════════

def test_B_first_sample_uses_base_temp():
    """第 0 次重采样用基准 temperature（保留单次可复现性）。"""
    temps = av._jittered_temperatures(0.8, 3)
    assert temps[0] == 0.8


def test_B_jitter_alternates_around_base():
    """之后 ±step 交替抖（i=1 +step / i=2 -step / i=3 +2step ...）。"""
    step = av.AV_JUDGE_TEMP_JITTER_STEP
    temps = av._jittered_temperatures(0.8, 5)
    assert temps == [0.8,
                     round(0.8 + step, 3), round(0.8 - step, 3),
                     round(0.8 + 2 * step, 3), round(0.8 - 2 * step, 3)]


def test_B_jitter_clamped_to_range():
    """抖动钳到 [0, 1.5]（防温度越界）。"""
    temps = av._jittered_temperatures(1.4, 6)
    assert all(0.0 <= t <= 1.5 for t in temps), temps
    lo = av._jittered_temperatures(0.05, 6)
    assert all(t >= 0.0 for t in lo), lo


def test_B_n1_single_temp():
    """N=1 → 只一个基准温度（不抖）。"""
    assert av._jittered_temperatures(0.8, 1) == [0.8]


def test_B_none_base_defaults():
    """base=None → 用 0.8 兜底（profile 无温度时不崩）。"""
    temps = av._jittered_temperatures(None, 2)
    assert temps[0] == 0.8


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
    """N 次全判走味 → drift · agreement=1.0 · 不算 flipped（无分歧）。"""
    s = [_parsed({"语用语气": av.DRIFT_VERDICT}) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    assert "语用语气" in agg["drift_dims"]
    assert agg["dimensions"]["语用语气"]["agreement"] == 1.0
    assert agg["dimensions"]["语用语气"]["flipped"] is False
    assert "语用语气" not in agg["unstable_dims"]


def test_C_flipped_marks_unstable():
    """同一维 N 次里走味和命中都出现过 → flipped=True · 进 unstable_dims（方差透明）。"""
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
    """某维 N 次全缺（弃权）→ verdict=None / drift=False（不臆造走味）。"""
    # 回复全缺 句法（只给 词汇选择）
    reply = json.dumps({"dimensions": {"词汇选择": {"verdict": av.DRIFT_VERDICT}}},
                       ensure_ascii=False)
    s = [av.parse_av_verdicts(reply) for _ in range(3)]
    agg = av.aggregate_verdicts(s)
    assert agg["dimensions"]["句法"]["verdict"] is None
    assert agg["dimensions"]["句法"]["drift"] is False
    assert agg["dimensions"]["句法"]["votes"]["abstain"] == 3


def test_C_n_valid_excludes_unparsed():
    """parse_ok=False 的采样不计入 n_valid（坏 JSON 不污染聚合）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}), _parsed({}, parse_ok=False),
         _parsed({"词汇选择": av.DRIFT_VERDICT})]
    agg = av.aggregate_verdicts(s)
    assert agg["n_samples"] == 3
    assert agg["n_valid_samples"] == 2  # 坏 JSON 那条不算
    # 2 个有效采样都判 词汇 走味 → drift
    assert "词汇选择" in agg["drift_dims"]


def test_C_empty_samples_no_crash():
    """空采样列表不崩 · parse_ok=False · 4 维齐全 · drift_dims 空。"""
    agg = av.aggregate_verdicts([])
    assert agg["parse_ok"] is False
    assert set(agg["dimensions"]) == set(_DIM_NAMES)
    assert agg["drift_dims"] == []
    assert agg["n_valid_samples"] == 0


def test_C_sample_drift_dims_transparency():
    """sample_drift_dims 留每次采样原始 drift（复盘可核单次判别 · 不黑箱）。"""
    s = [_parsed({"词汇选择": av.DRIFT_VERDICT}),
         _parsed({"句法": av.DRIFT_VERDICT}),
         _parsed({})]
    agg = av.aggregate_verdicts(s)
    assert agg["sample_drift_dims"] == [["词汇选择"], ["句法"], []]


# ════════════════════════════════════════════════════════════════
# [D] self_consistency_judge（N 次调用 · 温度抖动恢复 · 失败降级 · N=1 退化）
# ════════════════════════════════════════════════════════════════

def test_D_runs_n_samples():
    """跑 N 次重采样 → 每次调一次 call_gen_model（N=3 → 3 次）。"""
    mock, state = _mock_replies([_reply({"词汇选择": av.DRIFT_VERDICT}) for _ in range(3)])
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        agg = av.self_consistency_judge(_Loader([_P()]), "A", "B", n_samples=3)
    finally:
        av.call_gen_model = orig
    assert state["i"] == 3
    assert agg["n_valid_samples"] == 3
    assert "词汇选择" in agg["drift_dims"]


def test_D_temperature_jittered_and_restored():
    """每次重采样改写 profile.temperature（抖动），跑完恢复原温度（不污染 loader）。"""
    prof = _P(temperature=0.8)
    seen_temps = []

    def mock(loader, system, user, tag=""):
        seen_temps.append(prof.temperature)
        return _reply({}), prof, 0.1

    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        av.self_consistency_judge(_Loader([prof]), "A", "B", n_samples=3)
    finally:
        av.call_gen_model = orig
    # 调用时看到抖动温度（第 0 次基准 · 后续抖）
    assert seen_temps[0] == 0.8
    assert len(set(seen_temps)) > 1, seen_temps  # 真抖了
    # 跑完恢复
    assert prof.temperature == 0.8


def test_D_majority_vote_smooths_single_noise():
    """关键：单次噪声被多数票平滑——3 次里 1 次误判走味，聚合不判走味（治方差）。"""
    replies = [_reply({"句法": av.DRIFT_VERDICT}),  # 1 次误判
               _reply({}), _reply({})]              # 2 次命中
    mock, _ = _mock_replies(replies)
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        agg = av.self_consistency_judge(_Loader([_P()]), "A", "B", n_samples=3)
    finally:
        av.call_gen_model = orig
    assert agg["drift_dims"] == []  # 1/3 噪声被平滑
    assert "句法" in agg["unstable_dims"]  # 但方差被暴露（透明）


def test_D_all_fail_degrades_with_error():
    """N 次全失败 → error 非空 + drift_dims 空（调用方降级 · advisory 不中断）。"""
    boom = av.GenModelExhaustedError([("p", "x")])
    mock, _ = _mock_replies([boom, boom, boom])
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        agg = av.self_consistency_judge(_Loader([_P()]), "A", "B", n_samples=3)
    finally:
        av.call_gen_model = orig
    assert agg["error"]
    assert agg["drift_dims"] == []


def test_D_partial_fail_still_aggregates():
    """部分采样失败仍用剩余有效采样聚合（鲁棒 · sample_failures 记录失败）。"""
    replies = [_reply({"语用语气": av.DRIFT_VERDICT}),
               RuntimeError("boom"),
               _reply({"语用语气": av.DRIFT_VERDICT})]
    mock, _ = _mock_replies(replies)
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        agg = av.self_consistency_judge(_Loader([_P()]), "A", "B", n_samples=3)
    finally:
        av.call_gen_model = orig
    assert agg["error"] is None
    assert agg["n_valid_samples"] == 2
    assert "语用语气" in agg["drift_dims"]  # 2/2 有效采样判走味
    assert agg.get("sample_failures") and len(agg["sample_failures"]) == 1


def test_D_n1_degrades_to_single_call():
    """N=1 → 退化单次调用（零回归 · 等同改造前单采样行为）。"""
    mock, state = _mock_replies([_reply({"词汇选择": av.DRIFT_VERDICT})])
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        agg = av.self_consistency_judge(_Loader([_P()]), "A", "B", n_samples=1)
    finally:
        av.call_gen_model = orig
    assert state["i"] == 1
    assert agg["n_samples"] == 1
    assert "词汇选择" in agg["drift_dims"]


def test_D_uses_env_when_n_none():
    """n_samples=None → 读 env AV_JUDGE_N_SAMPLES（默认 3）。"""
    g = _reload(None)  # 默认 3
    try:
        mock, state = _mock_replies([_reply({}) for _ in range(3)])
        orig = g.call_gen_model
        g.call_gen_model = mock
        try:
            g.self_consistency_judge(_Loader([_P()]), "A", "B")  # 不传 n_samples
        finally:
            g.call_gen_model = orig
        assert state["i"] == 3
    finally:
        _reload(None)


# ════════════════════════════════════════════════════════════════
# [E] pairwise_drift_count 走自一致性（best-of-N 复用 · 方差透明）
# ════════════════════════════════════════════════════════════════

def test_E_pairwise_uses_self_consistency():
    """pairwise_drift_count 内部走 N 次重采样聚合（best-of-N 拿稳过方差的走味计数）。"""
    g = _reload(None)  # 默认 3 次
    try:
        # 3 次里 2 次判 词汇 走味 → 聚合判走味
        replies = [_reply({"词汇选择": av.DRIFT_VERDICT}),
                   _reply({"词汇选择": av.DRIFT_VERDICT}), _reply({})]
        mock, state = _mock_replies(replies)
        orig = g.call_gen_model
        g.call_gen_model = mock
        try:
            out = g.pairwise_drift_count(_Loader([_P()]), "A", "B")
        finally:
            g.call_gen_model = orig
        assert state["i"] == 3  # 真跑 3 次
        assert out["drift_count"] == 1
        assert out["drift_dims"] == ["词汇选择"]
        assert out["error"] is None
        # 方差透明字段透出给调用方
        assert out["n_valid_samples"] == 3
        assert 0.0 < out["mean_agreement"] <= 1.0
    finally:
        _reload(None)


def test_E_pairwise_all_fail_degrades():
    """全失败 → drift_count=None + error（gen_writer 据此退化到纯 SFS 排序 · 不阻断）。"""
    boom = av.GenModelExhaustedError([("p", "x")])
    mock, _ = _mock_replies([boom, boom, boom])
    orig = av.call_gen_model
    av.call_gen_model = mock
    try:
        out = av.pairwise_drift_count(_Loader([_P()]), "A", "B")
    finally:
        av.call_gen_model = orig
    assert out["drift_count"] is None
    assert out["error"]


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
# [G] G2-CYCLIC swap + intent_dim 零回归契约（在既有测试套里加断言 · 防回归）
# ════════════════════════════════════════════════════════════════

def test_G_default_self_consistency_no_swap_zero_regression():
    """默认（AV_JUDGE_POSITION_SWAP 未设）self_consistency_judge 全 swap=False → 现有 active 行为零回归。

    断言：默认 _position_swap_on()=False · 聚合 n_swapped_samples=0 · 既有 drift 判别不变。
    """
    g = _reload(None)
    try:
        assert g._position_swap_on() is False  # 默认 off
        replies = [_reply({"词汇选择": av.DRIFT_VERDICT}) for _ in range(3)]
        mock, state = _mock_replies(replies)
        orig = g.call_gen_model
        g.call_gen_model = mock
        try:
            agg = g.self_consistency_judge(_Loader([_P()]), "AUTH", "REP", n_samples=3)
        finally:
            g.call_gen_model = orig
        assert state["i"] == 3
        assert agg["n_swapped_samples"] == 0          # 默认 0 个 swap
        assert all(d["swapped"] is False for d in agg["sample_drift_detail"])
        assert "词汇选择" in agg["drift_dims"]          # 判别行为不变
    finally:
        _reload(None)


def test_G_n1_never_swaps_even_when_on():
    """N=1 即便 swap_on 也退化为 0 个 swap（零回归硬纪律）。"""
    assert av._swap_assignment(1, swap_on=True) == [False]
    assert av._swap_assignment(1, swap_on=False) == [False]


def test_G_intent_dim_default_off_4_dims():
    """build_av_judge_prompt 默认 include_intent_dim=False → 仍 4 维 · 不含「作者思维」（零回归）。"""
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
