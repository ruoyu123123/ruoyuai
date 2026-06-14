"""D4 promise_payoff_scanner 测试（2026-06-14·承诺-兑现缺口·advisory·默认 shadow）。

守护（北极星⑤·与 hard_gate FORESHADOWING_NOT_PAID 严格区分）：
  1. 开篇强承诺(≥2钩)结尾无兑现 → emit PROMISE_PAYOFF_GAP advisory；
  2. 开篇承诺+结尾兑现 → 不 emit；弱承诺(<2) → 不 emit（误报控制）；
  3. 🔴 PROMISE_PAYOFF_GAP 绝不进 HARD_GATE_CODES（与主代理 B-4 制度锁呼应）。
零依赖（stdlib）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import promise_payoff_scanner as pp  # noqa: E402


def test_hook_not_paid_emits_gap():
    """开篇强承诺(≥2钩)结尾无兑现 → emit PROMISE_PAYOFF_GAP advisory。"""
    text = ("他究竟是谁，为什么危险临头。\n\n中间过场平淡推进。\n\n"
            "中段继续铺陈。\n\n结尾平淡收束没有任何线索。")
    r = pp.scan_promise_payoff(text)
    codes = [i["code"] for i in r["issues"]]
    assert "PROMISE_PAYOFF_GAP" in codes
    assert all(i["gate_level"] == "advisory" for i in r["issues"])


def test_paid_no_gap():
    """开篇承诺 + 结尾兑现 → 不 emit。"""
    text = ("他究竟是谁，为什么危险临头。\n\n中间推进。\n\n"
            "线索浮现。\n\n原来真相终于揭开，答案水落石出。")
    r = pp.scan_promise_payoff(text)
    assert not any(i["code"] == "PROMISE_PAYOFF_GAP" for i in r["issues"])


def test_weak_promise_no_gap():
    """开篇弱承诺(<2钩) → 不 emit（误报控制）。"""
    text = "平淡开篇只有一个谜团。\n\n中间过场。\n\n推进铺陈。\n\n结尾平淡收束。"
    r = pp.scan_promise_payoff(text)
    assert not any(i["code"] == "PROMISE_PAYOFF_GAP" for i in r["issues"])


def test_short_draft_no_crash():
    """段落不足 → 不崩·空 issues。"""
    r = pp.scan_promise_payoff("太短")
    assert r["issues"] == []


def test_promise_payoff_gap_never_hard_gate():
    """🔴 制度锁：PROMISE_PAYOFF_GAP 绝不进 HARD_GATE_CODES（与 B-4 呼应）。"""
    import audit_hub
    assert "PROMISE_PAYOFF_GAP" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("PROMISE_PAYOFF_GAP", "error") == "advisory"
    assert audit_hub._gate_level_for("PROMISE_PAYOFF_GAP", "warning") == "advisory"
