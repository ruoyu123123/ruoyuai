# -*- coding: utf-8 -*-
"""A12 · LongBench-Write 六维质量 rubric 测试（mock LLM · 零真 API）。

覆盖任务契约（research/open_source_writing_systems_round2.md A12）：
1. 六维解析（含键名别名容错 · Analysis 非维度键跳过）
2. 缺维度即整体作废重试（≤3 次）
3. 全失败诚实记 rubric_unavailable 不伪造分
4. 长度剥离（judge prompt 明示不考虑长度 · 质量与长度双轨分离）
5. 聚合公式 (mean-1)*25 归一 0-100
6. SFS 闸门零变化锁（默认 off 零调用 · 恒 advisory · SFS 裁判/knockout 不读 rubric）
"""
import inspect
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import distill_rubric as drb  # noqa: E402
import distill_replicate as dr  # noqa: E402


_FULL_REPLY = (
    '评估如下：{"Analysis": "结构完整节奏得当", "Relevance": 4, "Accuracy": 5, '
    '"Coherence": 4, "Clarity": 3, "Breadth and Depth": 4, "Reading Experience": 5}'
)


# ══════════════════ 1. 六维解析 ══════════════════

def test_parse_six_dims_full_reply():
    scores = drb.parse_rubric_json(_FULL_REPLY)
    assert scores == {
        "Relevance": 4, "Accuracy": 5, "Coherence": 4,
        "Clarity": 3, "Breadth and Depth": 4, "Reading Experience": 5,
    }
    # Analysis 非维度键不进 scores
    assert "Analysis" not in scores


def test_parse_dim_key_aliases_and_string_digit():
    """键名别名容错（Breadth & Depth / ReadingExperience）+ 字符串数字分值安全转 int。"""
    reply = ('{"Analysis": "x", "relevance": 3, "Accuracy": "4", "Coherence": 5, '
             '"Clarity": 2, "Breadth & Depth": 3, "ReadingExperience": 4}')
    scores = drb.parse_rubric_json(reply)
    assert scores is not None
    assert scores["Breadth and Depth"] == 3
    assert scores["Reading Experience"] == 4
    assert scores["Accuracy"] == 4


def test_parse_invalid_values_void_whole_result():
    """越界(7) / bool / 非整 float / 非 JSON → 整体作废返 None（绝不部分采信）。"""
    base = ('{{"Relevance": {v}, "Accuracy": 5, "Coherence": 4, "Clarity": 3, '
            '"Breadth and Depth": 4, "Reading Experience": 5}}')
    assert drb.parse_rubric_json(base.format(v=7)) is None       # 越界
    assert drb.parse_rubric_json(base.format(v="true")) is None  # bool
    assert drb.parse_rubric_json(base.format(v=3.5)) is None     # 非整 float
    assert drb.parse_rubric_json("这次没有输出 JSON") is None      # 无 JSON
    assert drb.parse_rubric_json("") is None
    # 整数值 float（4.0）安全转 int 不作废
    assert drb.parse_rubric_json(base.format(v=4.0))["Relevance"] == 4


# ══════════════════ 2. 缺维度即整体作废重试 ══════════════════

def test_missing_dim_voids_and_retries_then_ok():
    """第 1 次缺 Clarity → 整体作废重试；第 2 次六维齐 → status=ok · attempts=2。"""
    missing = ('{"Analysis": "x", "Relevance": 4, "Accuracy": 5, "Coherence": 4, '
               '"Breadth and Depth": 4, "Reading Experience": 5}')  # 缺 Clarity
    replies = iter([missing, _FULL_REPLY])

    def call_fn(system, user):
        return next(replies)

    r = drb.run_rubric_judge(call_fn, "正文" * 100)
    assert r["status"] == "ok"
    assert r["attempts"] == 2
    assert r["scores"]["Clarity"] == 3
    assert len(r["attempts_log"]) == 1  # 第 1 次作废留痕
    assert "作废" in r["attempts_log"][0]["error"]


# ══════════════════ 3. 全失败诚实记 rubric_unavailable ══════════════════

def test_all_attempts_fail_honest_unavailable():
    """3 次全缺维/异常 → rubric_unavailable · scores/aggregate 均 None（不伪造分）。"""
    calls = {"n": 0}

    def call_fn(system, user):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("gen-model 挂了")  # 调用异常也按一次作废计
        return '{"Relevance": 4}'  # 缺其余五维

    r = drb.run_rubric_judge(call_fn, "正文")
    assert r["status"] == "rubric_unavailable"
    assert r["scores"] is None
    assert r["aggregate_0_100"] is None
    assert r["attempts"] == 3
    assert calls["n"] == 3
    assert len(r["attempts_log"]) == 3
    assert r["gate_level"] == "advisory"


# ══════════════════ 4. 长度剥离（质量与长度双轨分离） ══════════════════

def test_length_stripped_from_judge_prompt():
    """judge prompt 必须明示「不考虑长度是否达标」· 且不带任何目标字数要求。"""
    user = drb.build_rubric_prompt("他推开门。" * 50, task_brief="复刻测试")
    assert "不考虑" in user and "长度" in user
    assert "双轨分离" in user
    # 不给 judge 塞目标字数（长度另有确定性度量）
    assert "目标字数" not in user and "±10%" not in user
    # 六维定义齐全
    for dim in drb.RUBRIC_DIMENSIONS:
        assert dim in user


def test_aggregate_independent_of_text_length():
    """同一组分数聚合值与文本长度无关（分数只来自 judge JSON·长度不进公式）。"""
    scores = {"Relevance": 4, "Accuracy": 5, "Coherence": 4,
              "Clarity": 3, "Breadth and Depth": 4, "Reading Experience": 5}
    assert drb.aggregate_score(scores) == drb.aggregate_score(dict(scores))
    # 公式无任何文本/长度输入面
    assert "text" not in inspect.signature(drb.aggregate_score).parameters


# ══════════════════ 5. 聚合公式 (mean-1)*25 ══════════════════

def test_aggregate_formula_bounds_and_midpoint():
    all5 = {d: 5 for d in drb.RUBRIC_DIMENSIONS}
    all1 = {d: 1 for d in drb.RUBRIC_DIMENSIONS}
    all3 = {d: 3 for d in drb.RUBRIC_DIMENSIONS}
    assert drb.aggregate_score(all5) == 100.0
    assert drb.aggregate_score(all1) == 0.0
    assert drb.aggregate_score(all3) == 50.0
    # 非整均值：mean=(4+5+4+3+4+5)/6=25/6 → (25/6-1)*25=79.17
    mixed = {"Relevance": 4, "Accuracy": 5, "Coherence": 4,
             "Clarity": 3, "Breadth and Depth": 4, "Reading Experience": 5}
    assert drb.aggregate_score(mixed) == pytest.approx(79.17, abs=0.01)


# ══════════════════ 6. SFS 闸门零变化锁 ══════════════════

def test_default_off_zero_llm_calls(monkeypatch):
    """DISTILL_RUBRIC_MODE 未设/off/非法值 → 门控直接返回 off · 绝不发 judge 调用。"""
    def must_not_call(system, user):
        raise AssertionError("off 模式绝不允许发 judge 调用")

    for v in (None, "off", "", "banana"):
        if v is None:
            monkeypatch.delenv("DISTILL_RUBRIC_MODE", raising=False)
        else:
            monkeypatch.setenv("DISTILL_RUBRIC_MODE", v)
        r = drb.run_rubric_judge_gated(must_not_call, "正文")
        assert r["status"] == "off"
        assert r["gate_level"] == "advisory"


def test_gated_active_runs_judge(monkeypatch):
    """DISTILL_RUBRIC_MODE=on → 门控入口真跑 judge（mock 一次成功）。"""
    monkeypatch.setenv("DISTILL_RUBRIC_MODE", "on")
    r = drb.run_rubric_judge_gated(lambda s, u: _FULL_REPLY, "正文")
    assert r["status"] == "ok"
    assert r["aggregate_0_100"] == pytest.approx(79.17, abs=0.01)


def test_sfs_gate_zero_change_lock():
    """回归锁：SFS 裁判链（_score_draft_sfs / _knockout_accept / draft_refine_loop）与
    style_evaluator 均不读 rubric——六维只是旁证观测，SFS 仍是唯一出货闸（北极星⑤）。"""
    for fn in (dr._score_draft_sfs, dr._knockout_accept, dr.draft_refine_loop):
        assert "rubric" not in inspect.getsource(fn), \
            f"{fn.__name__} 不得消费 rubric（SFS 闸门判据零变化）"
    import style_evaluator  # noqa: E402
    assert "distill_rubric" not in inspect.getsource(style_evaluator), \
        "style_evaluator（SFS 唯一权威）不得依赖 rubric 模块"
    # rubric 产出恒 advisory · 无 verdict/hard_gate 字段（不冒充判决）
    ok = drb.run_rubric_judge(lambda s, u: _FULL_REPLY, "正文")
    bad = drb.run_rubric_judge(lambda s, u: "{}", "正文")
    for r in (ok, bad):
        assert r["gate_level"] == "advisory"
        assert "verdict" not in r and "hard_gate" not in r
