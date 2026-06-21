"""思维层探针永不 hard_gate 制度锁回归测试（2026-06-14 · GATEKEEPER B-4 · 北极星⑤）。

零成本最强护栏——守护三件事：
  1. 思维层探针 code（D1-D8 派生·作者思维/意图/读者心理/因果/弧线形状）一律 advisory，
     永不在 HARD_GATE_CODES：_gate_level_for 对它们 × 4 severity 全返 advisory；
  2. 双闸两路径一致——scanner 自报 gate_level='hard_gate' 也被拦回 advisory：
     _parse_issues_list_scanner（L476 守卫）+ _parse_violations_scanner（B-2 补的 L587 守卫）；
     且白名单内真 hard_gate 不被误伤；
  3. POV_HEAD_HOPPING 不在 HARD_GATE_CODES（pov_consistency 是顾问非门禁）。

物理依据：思维层探针 LLM/人评一致性天花板 ≈ 0.71-0.74，探针自身噪声可能 ≥ 真实效应，
故显著性结论永远 advisory（详见 audit_hub.py HARD_GATE_CODES 上方制度约束注释）。

零依赖（stdlib）·复用 test_audit_hub_aggregation 沙箱范式·不碰 LLM/agent。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import audit_hub as ah  # noqa: E402


# 预期的思维层探针 code（D1-D8 派生）。当前多数尚未在代码中引入——
# 本断言是制度护栏：未来任何人把这些 code 误加进 HARD_GATE_CODES 立刻红灯。
THINKING_PROBE_CODES = [
    "PROMISE_PAYOFF_GAP",      # D4 承诺-兑现缺口
    "ARC_SHAPE_MISMATCH",      # 弧线形状（已被金标准证伪 → 永不 hard_gate）
    "INTENT_RECOVERY_LOW",     # 意图可复原性低
    "INTENT_ANCHOR_MISSING",   # D5 角色动机锚点缺失
    "READER_TENSION_DRIFT",    # 读者张力漂移
    "CAUSALITY_GAP",           # 因果链断裂
    "PPP_INCOMPLETE",          # Promise/Progress/Payoff 不完整
    # R20 W9 Batch-Z·P0（2026-06-21）3 件 STRONG 全 advisory：
    "SFS_POORLY_CALIBRATED_FOR_AUTHOR",   # SFS / av_judge 自身校准探针
    "CHARACTER_KTH_ORDER_BELIEF_DRIFT",   # OSCToM K-order(K=2) 嵌套信念
    "CHARACTER_STATE_DRIFT_DETECTED",     # NKW 时态分离 stable_identity drift
]


def test_thinking_probe_codes_never_in_hard_gate():
    """组1：思维层探针 code 都不在白名单，且 _gate_level_for × 4 severity 全 advisory。"""
    for code in THINKING_PROBE_CODES:
        assert code not in ah.HARD_GATE_CODES, f"{code} 误入 HARD_GATE_CODES 白名单"
        for sev in ("fatal", "error", "warning", "info"):
            gl = ah._gate_level_for(code, sev)
            assert gl == "advisory", f"{code}@{sev} 应 advisory，实得 {gl}"


def test_self_reported_hardgate_blocked_issues_list():
    """组2：_parse_issues_list_scanner——探针自报 gate_level='hard_gate' 被 L476 双闸拦回 advisory。"""
    out = json.dumps({"issues": [
        {"code": "PROMISE_PAYOFF_GAP", "severity": "error",
         "gate_level": "hard_gate", "msg": "钩了没兑现"}
    ]})
    issues = ah._parse_issues_list_scanner(out, "promise_payoff_scanner", "剧情")
    assert len(issues) == 1
    assert issues[0]["gate_level"] == "advisory", "探针自报 hard_gate 未被拦回（issues_list 路径）"


def test_self_reported_hardgate_blocked_violations():
    """组2b：_parse_violations_scanner——探针自报顶层 hard_gate 被 B-2 补的 L587 守卫拦回 advisory。"""
    out = json.dumps({"gate_level": "hard_gate",
                      "violations": [{"severity": "major"}], "verdict": "fail"})
    issues = ah._parse_violations_scanner(out, "ppp_scanner", "PROMISE_PAYOFF_GAP", "剧情")
    assert len(issues) == 1
    assert issues[0]["gate_level"] == "advisory", "探针自报顶层 hard_gate 未被拦回（B-2 violations 漏洞）"


def test_violations_real_hardgate_not_harmed():
    """组2c：对照——白名单内 code 顶层 hard_gate 不被误伤（守卫只拦非白名单越权升格）。"""
    out = json.dumps({"gate_level": "hard_gate",
                      "violations": [{"severity": "major"}], "verdict": "fail"})
    issues = ah._parse_violations_scanner(
        out, "locked_fact_scanner", "LOCKED_FACT_CROSS_SCENE_CONFLICT", "剧情")
    assert len(issues) == 1
    assert issues[0]["gate_level"] == "hard_gate", "白名单内真 hard_gate 被误降级"


def test_pov_head_hopping_not_hard_gate():
    """组3：POV_HEAD_HOPPING 不在 HARD_GATE_CODES（pov_consistency 顾问非门禁）。"""
    assert "POV_HEAD_HOPPING" not in ah.HARD_GATE_CODES
    assert ah._gate_level_for("POV_HEAD_HOPPING", "warning") == "advisory"
    assert ah._gate_level_for("POV_HEAD_HOPPING", "error") == "advisory"
