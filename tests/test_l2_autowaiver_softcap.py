"""L2-0 auto-waiver soft-cap 止血回归测试（2026-05-30）。

【守护的修复】audit_hub._check_auto_waiver 命中 ≥3 次后旧逻辑直接整条 issue
waived=True ＝「完全关掉该检测」（无穷增益二元跳变、关了回不来、无衰减，矫枉过正
反向震荡源）。止血改为「降一档严格度」（_apply_auto_calibration_softcap）：
保留检测【存在性】，severity 沿阶梯下降一档（fatal→error→warning→info），到 info
不再下降，永不彻底关闭。

本测试断言：
  · 降一档不删除 issue（检测存在性保留 · 北极星⑤顾问非法官）；
  · info 是地板，永不消失；
  · 永不设 waived=True（不再整条豁免）；
  · 永不触碰 hard_gate（调用方过滤 + 函数内部纯 advisory 降档）；
  · 【advisory 锁死守卫】本件新增的任何 code / 元数据，绝不污染 HARD_GATE_CODES，
    结构上保证此降档机制不可能黑箱升级为 hard_gate 判决（呼应北极星⑤）。

只测确定性纯函数，不碰 LLM / agent / 文件系统。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import audit_hub as ah


def _mk_issue(code="NARRATIVE_x", severity="error", gate_level="advisory"):
    """构造一条与 audit_hub 内部一致 shape 的 issue dict。"""
    return {
        "dimension": "叙事", "severity": severity,
        "gate_level": gate_level, "code": code,
        "desc": "测试用 issue", "source": "narrative_scanner",
        "fix_hint": "", "waived": False, "waive_reason": "",
    }


def _mk_match(code="NARRATIVE_x", stype="adjust_threshold"):
    return {
        "code": code, "suggestion_type": stype,
        "suggestion": "检测项被累计豁免，建议复核阈值或降级默认 severity。",
        "waived_count": 5,
    }


# ---------- 降一档：保留检测存在性 ----------

def test_softcap_downgrades_error_to_warning():
    """error 命中 → 降到 warning（降一档），issue 仍在、未 waived。"""
    issue = _mk_issue(severity="error")
    changed = ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert changed is True
    assert issue["severity"] == "warning"
    # 检测存在性保留：issue dict 没被删、没被标 waived
    assert issue.get("waived") is False
    assert "_auto_calibration_softcap" in issue
    assert issue["_auto_calibration_softcap"]["from_severity"] == "error"
    assert issue["_auto_calibration_softcap"]["to_severity"] == "warning"


def test_softcap_downgrades_warning_to_info():
    """warning → info（降一档），仍保留为可审计的 info（不删）。"""
    issue = _mk_issue(severity="warning")
    changed = ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert changed is True
    assert issue["severity"] == "info"
    assert issue.get("waived") is False


def test_softcap_downgrades_fatal_to_error():
    """fatal → error（降一档）。soft-cap 永远只降一格，不一步到 info。"""
    issue = _mk_issue(severity="fatal")
    changed = ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert changed is True
    assert issue["severity"] == "error"


def test_softcap_only_one_notch_not_full_kill():
    """核心：一次 soft-cap 只降一档（error 不会一步跌到 info=近乎关闭）。

    这是与旧「整条 waived 完全关检测」的本质区别 —— 渐进、有衰减、可逆。
    """
    issue = _mk_issue(severity="error")
    ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert issue["severity"] == "warning"  # 不是 info、不是被删、不是 waived


# ---------- info 地板：永不彻底消失 ----------

def test_softcap_info_stays_info_floor():
    """info 已是最低档：盖标记但不再下降、返回 False（无实际降档）。"""
    issue = _mk_issue(severity="info")
    changed = ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert changed is False
    assert issue["severity"] == "info"  # 永不消失到地板以下
    assert issue.get("waived") is False
    # 即便没降，也留审计标记（可追溯命中了 calibration）
    assert "_auto_calibration_softcap" in issue


def test_softcap_never_sets_waived():
    """回归守卫：soft-cap 任何路径都不把 issue 标 waived（不再整条豁免）。"""
    for sev in ("fatal", "error", "warning", "info"):
        issue = _mk_issue(severity=sev)
        ah._apply_auto_calibration_softcap(issue, _mk_match())
        assert issue.get("waived") is False, f"{sev} 不应被 waived"


# ---------- 元数据可审计性 ----------

def test_softcap_records_audit_metadata():
    """降档必须留可审计痕迹：原/新 severity + suggestion 摘要 + waived_count。"""
    issue = _mk_issue(severity="error")
    ah._apply_auto_calibration_softcap(issue, _mk_match())
    meta = issue["_auto_calibration_softcap"]
    assert set(meta.keys()) >= {"from_severity", "to_severity", "suggestion", "waived_count"}
    assert meta["waived_count"] == 5
    assert len(meta["suggestion"]) <= 120  # 摘要截断，不爆报告


# ---------- 北极星⑤：advisory 锁死守卫（结构性保证不可黑箱） ----------

def test_hard_gate_codes_not_polluted_by_softcap_change():
    """守卫：本件 L2-0 改动绝不向 HARD_GATE_CODES 新增任何 code。

    审计基线（15 个，2026-05-29 与 audit_hub.HARD_GATE_CODES 对齐 · CLAUDE.md 第十一节）。
    任何让此集合变大/混入新 code 的改动都会让本测试红——结构上保证降档机制
    不可能黑箱升级成 hard_gate 判决（北极星⑤顾问非法官）。
    """
    expected = {
        "LOCKED_FACT_CONFLICT", "FUTURE_KNOWLEDGE_LEAK", "FORESHADOWING_NOT_PAID",
        "SECRET_NOT_REVEALED", "UNKNOWN_CHARACTER_DETECTED", "CHANGES_MISSING",
        "MANIFEST_MISSING", "FILE_NOT_FOUND", "ITEM_HOLDER_ABSENT",
        "ITEM_NOT_YET_INTRODUCED", "PROPAGATION_DEBT_CREATED", "STYLE_单段超长",
        "CHAPTER_END_FORBIDDEN_SCREENPLAY", "CHAPTER_END_FORBIDDEN_TRANSITION",
        "LOCKED_FACT_CROSS_SCENE_CONFLICT",
    }
    assert ah.HARD_GATE_CODES == expected, (
        "HARD_GATE_CODES 被污染！L2-0 soft-cap 是纯 advisory 机制，"
        "绝不可新增/改动 hard_gate code（北极星⑤）。差异: "
        f"多出={ah.HARD_GATE_CODES - expected} 缺少={expected - ah.HARD_GATE_CODES}"
    )


def test_softcap_metadata_marker_is_not_a_hard_gate_code():
    """守卫：soft-cap 写入的元数据 marker 字符串绝不混进 HARD_GATE_CODES。

    防御「无意中把内部标记当 code 注册成 hard_gate」这类黑箱回归。
    """
    assert "_auto_calibration_softcap" not in ah.HARD_GATE_CODES


def test_softcap_does_not_change_gate_level():
    """降档只动 severity，不动 gate_level —— advisory 永远还是 advisory（不偷偷升 hard_gate）。"""
    issue = _mk_issue(severity="error", gate_level="advisory")
    ah._apply_auto_calibration_softcap(issue, _mk_match())
    assert issue["gate_level"] == "advisory"


# ---------- _check_auto_waiver 命中逻辑未被破坏（≥3 次后仍命中 → 降档而非关闭） ----------

def test_check_auto_waiver_still_matches_adjust_threshold():
    """adjust_threshold 类不依赖 scene_type，直接命中（≥3 次累积建议）。"""
    suggestions = [_mk_match(code="NARRATIVE_x", stype="adjust_threshold")]
    match = ah._check_auto_waiver("NARRATIVE_x", set(), suggestions)
    assert match is not None


def test_check_auto_waiver_scene_adaptation_needs_scene_hit():
    """add_scene_adaptation 类需 scene_type 命中才生效（hint 不在集合 → 不命中）。"""
    s = _mk_match(code="NARRATIVE_x", stype="add_scene_adaptation")
    s["scene_type_hint"] = "solo_atmospheric"
    assert ah._check_auto_waiver("NARRATIVE_x", set(), [s]) is None
    assert ah._check_auto_waiver("NARRATIVE_x", {"solo_atmospheric"}, [s]) is not None


def test_recurring_match_downgrades_but_keeps_detection_alive():
    """端到端意图：同一 code ≥3 次命中后，检测【仍存在】（只降档，回不来 → 改成可逆降档）。

    模拟「命中 → 降一档」连续两轮：error→warning→info，issue 始终在、始终未 waived，
    且永不跌破 info 地板。对照旧逻辑（命中即 waived 整条消失）。
    """
    issue = _mk_issue(severity="error")
    match = _mk_match()
    # 第 1 轮命中
    ah._apply_auto_calibration_softcap(issue, match)
    assert issue["severity"] == "warning" and issue.get("waived") is False
    # 第 2 轮命中（模拟下一次 audit 再命中）
    ah._apply_auto_calibration_softcap(issue, match)
    assert issue["severity"] == "info" and issue.get("waived") is False
    # 第 3 轮：已到地板，不再下降，检测项依然在（detection 存在性永久保留）
    ah._apply_auto_calibration_softcap(issue, match)
    assert issue["severity"] == "info" and issue.get("waived") is False
