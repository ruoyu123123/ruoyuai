# -*- coding: utf-8 -*-
"""R7 W2 Batch-E·judge_runner style-bias swap-check 单测·mock LLM·确定性。

覆盖：
  · _bias_mode env 解析 / 非法回落
  · make_swapped_user 反转文件块序 / head-tail 保留 / 单块 fallback
  · _bias_signature / _bias_delta 数值差异 + verdict 不同 / 缺签名
  · style_bias_swap_check off / shadow / active 路径 + 异常降级
  · run_judge 集成：不破坏现有 advisory pipeline · _style_bias_check 仅在 eligible/non-off 才挂
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import judge_runner as jr  # noqa: E402
import llm_transport as lt  # noqa: E402
from gen_model_loader import Profile  # noqa: E402


def _profile(name="p1"):
    return Profile(name=name, model="m", base_url="https://x.test/v1",
                   api_key="sk-test", temperature=0.8, max_tokens=None)


# ── _bias_mode env ─────────────────────────────────────────────────────
def test_bias_mode_default_off():
    bak = os.environ.pop("JUDGE_STYLE_BIAS_SWAP_MODE", None)
    try:
        assert jr._bias_mode() == "off"
    finally:
        if bak is not None:
            os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = bak


def test_bias_mode_active_recognized():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "active"
    try:
        assert jr._bias_mode() == "active"
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


def test_bias_mode_invalid_falls_back_off():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "bogus"
    try:
        assert jr._bias_mode() == "off"
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


# ── make_swapped_user ──────────────────────────────────────────────────
def test_swap_no_files_appends_hint():
    s = "# 输入契约\nfoo: 1\n\n现在执行你的职责，输出最终 JSON。"
    out = jr.make_swapped_user(s)
    assert "位置偏校验提示" in out


def test_swap_single_file_no_reorder():
    s = ("# 输入契约\nx: 1\n\n【文件: A】\nAAA\n\n"
         "现在执行你的职责，输出最终 JSON。")
    out = jr.make_swapped_user(s)
    assert "单文件块无可反转" in out
    assert "AAA" in out


def test_swap_reverses_multiple_file_chunks():
    s = ("# 输入契约\nx: 1\n\n【文件: A】\nAAA\n\n【文件: B】\nBBB\n\n"
         "【文件: C】\nCCC\n\n现在执行你的职责，输出最终 JSON。")
    out = jr.make_swapped_user(s)
    # A 在 B 之前的位置应反转：C 应早于 A 出现
    pos_a = out.find("【文件: A】")
    pos_c = out.find("【文件: C】")
    assert 0 <= pos_c < pos_a
    # 输入契约还在头·尾巴提示还在
    assert out.startswith("# 输入契约")
    assert "现在执行你的职责" in out
    assert "位置偏校验提示" in out


# ── _bias_signature / _bias_delta ──────────────────────────────────────
def test_signature_extracts_verdict_and_counts():
    sig = jr._bias_signature({"verdict": "PASS", "violations": [1, 2, 3]})
    assert sig["verdict"] == "pass"
    assert sig["violation_count"] == 3


def test_signature_empty_input():
    assert jr._bias_signature({}) == {}
    assert jr._bias_signature(None) == {}


def test_signature_picks_first_grade_key():
    sig = jr._bias_signature({"overall_grade": "A"})
    assert sig["overall_grade"] == "a"


def test_delta_identical_signatures_zero():
    a = {"verdict": "pass", "violation_count": 2}
    delta = jr._bias_delta(a, dict(a))
    assert delta["asymmetry"] == 0.0
    assert delta["style_bias_suspected"] is False


def test_delta_verdict_flip_high_asymmetry():
    a = {"verdict": "pass", "violation_count": 1}
    b = {"verdict": "fail", "violation_count": 1}
    delta = jr._bias_delta(a, b)
    assert delta["asymmetry"] >= 0.5
    assert delta["style_bias_suspected"] is True


def test_delta_empty_returns_zero():
    delta = jr._bias_delta({}, {"verdict": "pass"})
    assert delta["asymmetry"] == 0.0


# ── style_bias_swap_check ──────────────────────────────────────────────
def test_swap_check_off_mode_returns_skeleton():
    os.environ.pop("JUDGE_STYLE_BIAS_SWAP_MODE", None)
    out = jr.style_bias_swap_check(
        "novel-voice-checker", "sys", "user", {"verdict": "pass"}, [_profile()])
    assert out["mode"] == "off"
    assert out["style_bias_suspected"] is False
    assert "swapped_signature" not in out


def test_swap_check_ineligible_agent_no_call():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "active"
    try:
        # summarizer 不在 eligible 名单 → 直接返回
        out = jr.style_bias_swap_check(
            "novel-summarizer", "sys", "user", {"verdict": "pass"}, [_profile()],
            _generate_fn=lambda *a, **k: 1 / 0)   # 若被调用必崩 → 没崩证明没调用
        assert out["agent_eligible"] is False
        assert out["style_bias_suspected"] is False
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


def test_swap_check_shadow_does_not_call():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "shadow"
    try:
        out = jr.style_bias_swap_check(
            "novel-voice-checker", "sys", "user", {"verdict": "pass"}, [_profile()],
            _generate_fn=lambda *a, **k: 1 / 0)   # shadow 不应调用
        assert out["mode"] == "shadow"
        assert out["agent_eligible"] is True
        assert out["style_bias_suspected"] is False
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


def test_swap_check_active_invokes_and_compares():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "active"
    try:
        baseline = {"verdict": "pass", "violations": [1]}
        # 反转后变 fail → 高 asymmetry → suspected
        swap_reply = json.dumps({"verdict": "fail", "violations": [1, 2, 3, 4]},
                                ensure_ascii=False)
        calls = []

        def fn(profiles, system, user, **kw):
            calls.append((system[:40], user[:40]))
            return lt.GenResult(text=swap_reply, profile=_profile(),
                                finish_reason="stop")
        out = jr.style_bias_swap_check(
            "novel-voice-checker", "sys", "user-with-files", baseline, [_profile()],
            required_keys=("verdict",), _generate_fn=fn)
        assert out["mode"] == "active"
        assert len(calls) == 1, "active 必须调一次 swap LLM"
        assert out["style_bias_suspected"] is True
        assert "_advisory" in out
        assert out["baseline_signature"]["verdict"] == "pass"
        assert out["swapped_signature"]["verdict"] == "fail"
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


def test_swap_check_active_exception_degrades_gracefully():
    os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"] = "active"
    try:
        def boom(*a, **k):
            raise RuntimeError("network down")
        out = jr.style_bias_swap_check(
            "novel-voice-checker", "sys", "user", {"verdict": "pass"}, [_profile()],
            _generate_fn=boom)
        assert out["mode"] == "active"
        assert out["style_bias_suspected"] is False
        assert "swap-check 调用失败" in out.get("_note", "")
    finally:
        del os.environ["JUDGE_STYLE_BIAS_SWAP_MODE"]


# ── run_judge 集成（off=默认零行为变化，但仍挂 eligible 元数据）──────────────
def _setup_project(tmp):
    proj = Path(tmp) / "proj"
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"句长均值": 31}, ensure_ascii=False), encoding="utf-8")
    return proj


def _setup_agents(tmp):
    d = Path(tmp) / "agents"
    d.mkdir()
    for n in jr.AGENT_SPECS:
        (d / f"{n}.md").write_text(
            f"---\nname: {n}\n---\n你是 {n}。", encoding="utf-8")
    return d


def _mk_gen(script):
    def fn(profiles, system, user, **kw):
        if not script:
            raise lt.TransportExhausted([("p1", "耗尽")])
        return lt.GenResult(text=script.pop(0), profile=_profile(),
                            finish_reason="stop")
    return fn


def test_run_judge_off_mode_still_marks_eligible_meta():
    """off 模式下·eligible agent 仍挂 _style_bias_check 元数据（mode=off）·不破现有契约。"""
    os.environ.pop("JUDGE_STYLE_BIAS_SWAP_MODE", None)
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        out = jr.run_judge(
            "novel-voice-checker", proj, params={},
            agents_dir=agents,
            _generate_fn=_mk_gen([json.dumps({"violations": []},
                                             ensure_ascii=False)]))
        assert out.ok
        meta = out.data.get("_style_bias_check")
        assert meta is not None
        assert meta["mode"] == "off"
        assert meta["agent_eligible"] is True
        assert meta["style_bias_suspected"] is False


def test_run_judge_ineligible_agent_no_meta_attached():
    """non-eligible 且 off → 不挂元数据·完全零回归。"""
    os.environ.pop("JUDGE_STYLE_BIAS_SWAP_MODE", None)
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        out = jr.run_judge(
            "novel-reflector", proj, params={},
            agents_dir=agents,
            _generate_fn=_mk_gen([json.dumps({"entries": []},
                                              ensure_ascii=False)]))
        assert out.ok
        assert "_style_bias_check" not in out.data
