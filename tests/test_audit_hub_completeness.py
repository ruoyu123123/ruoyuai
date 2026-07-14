# -*- coding: utf-8 -*-
"""🔴 audit_hub 并发上限 + 审计完整性透明化回归锁（G3·2026-07-14）。

② 并发过载：cluster 模式满载 ~180 个 scanner 若 ThreadPoolExecutor(max_workers=len(tasks))
   全部并发起飞，NN daemon 被打到排队超时（exit 99），超时 scanner 的 issue 不进 verdict。
   修：max_workers=min(32, (os.cpu_count() or 8)+4)（CPython ThreadPoolExecutor 自身默认启发式）。

③ 审计完整性不可见：scanner 超时/失败时其 issue 不进 verdict，但 verdict 仍报「完整」。
   修：报告顶层 surface audit_complete + audit_completeness（列出失败 scanner + 原因）。
   🔴 北极星⑤：信息透明化·不改 verdict、不阻断、不新增 hard_gate。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import audit_hub  # noqa: E402


# ═══════════════ ② 并发上限：不按 len(tasks) 满开 ═══════════════

def test_pool_width_uses_cpython_default_heuristic():
    """max_workers 表达式采用 CPython 默认启发式 min(32, cpu+4)·不发明魔数·不 len(tasks)。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "max_workers = min(32, (os.cpu_count() or 8) + 4)" in src, \
        "并发上限应为 CPython ThreadPoolExecutor 默认启发式 min(32, cpu+4)"
    assert "ThreadPoolExecutor(max_workers=max_workers)" in src
    # 回归：绝不回退到 len(tasks) 满开
    assert "ThreadPoolExecutor(max_workers=len(tasks))" not in src, \
        "禁止按 task 数满开线程池（NN daemon 会被打到超时丢结果）"


def test_pool_width_capped_at_32_regardless_of_cpu():
    import os
    max_workers = min(32, (os.cpu_count() or 8) + 4)
    assert 1 <= max_workers <= 32


# ═══════════════ ③ 审计完整性透明化 ═══════════════

def test_completeness_all_ok():
    """全部 scanner ok → complete=True·无 failed_scanners·无 note。"""
    status = [
        {"scanner": "validate_chapter", "exit_code": 0, "ok": True},
        {"scanner": "validate_style", "exit_code": 1, "ok": True},
        {"scanner": "coherence", "exit_code": 0, "ok": True},
    ]
    c = audit_hub._compute_audit_completeness(status)
    assert c["complete"] is True
    assert c["scanners_failed"] == 0
    assert c["scanners_ok"] == 3
    assert c["failed_scanners"] == []
    assert "note" not in c


def test_completeness_flags_timeout_scanner():
    """NN scanner exit 99（超时）→ complete=False·列出失败 scanner + reason=timeout。"""
    status = [
        {"scanner": "validate_chapter", "exit_code": 0, "ok": True},
        {"scanner": "coherence", "exit_code": 99, "ok": False},
        {"scanner": "spatial_continuity", "exit_code": 99, "ok": False},
    ]
    c = audit_hub._compute_audit_completeness(status)
    assert c["complete"] is False
    assert c["scanners_failed"] == 2
    assert c["scanners_ok"] == 1
    names = {f["scanner"] for f in c["failed_scanners"]}
    assert names == {"coherence", "spatial_continuity"}
    assert all(f["reason"] == "timeout" for f in c["failed_scanners"])
    # note 必须列出失败 scanner 名（主代理/用户一眼可见）
    assert "coherence" in c["note"] and "spatial_continuity" in c["note"]
    assert "未计入 verdict" in c["note"]


def test_completeness_maps_exit_reasons():
    """退出码 → 人类可读原因映射（99=timeout / 98=exec_error / 2=cli_contract / 其它=nonzero_exit）。"""
    status = [
        {"scanner": "a", "exit_code": 99, "ok": False},
        {"scanner": "b", "exit_code": 98, "ok": False},
        {"scanner": "c", "exit_code": 2, "ok": False},
        {"scanner": "d", "exit_code": 3, "ok": False},
    ]
    reasons = {f["scanner"]: f["reason"]
               for f in audit_hub._compute_audit_completeness(status)["failed_scanners"]}
    assert reasons == {"a": "timeout", "b": "exec_error",
                       "c": "cli_contract", "d": "nonzero_exit"}


def test_cli_contract_exit2_surfaced_as_incomplete():
    """🔴 与 argv 契约债联动：scanner argparse 拒 argv（exit 2）→ 审计报告如实标不完整。
    正是本 G3 bug 的可观测化——曾经 spatial exit 2 被静默丢，现在 surface 出来。"""
    status = [{"scanner": "spatial_continuity", "exit_code": 2, "ok": False}]
    c = audit_hub._compute_audit_completeness(status)
    assert c["complete"] is False
    assert c["failed_scanners"][0]["reason"] == "cli_contract"


def test_completeness_never_changes_verdict_or_adds_hard_gate():
    """🔴 北极星⑤边界：完整性字段是 META·不含 verdict/gate_level·不引入 hard_gate。"""
    status = [{"scanner": "x", "exit_code": 99, "ok": False}]
    c = audit_hub._compute_audit_completeness(status)
    blob = str(c)
    assert "verdict" not in c
    assert "hard_gate" not in blob
    assert "gate_level" not in c


def test_audit_incomplete_line_empty_when_complete():
    """审计跑全 → 告警行为空串（不打印）。缺 audit_completeness 的裸 dict 也按跑全处理。"""
    ok_status = [{"scanner": "validate_chapter", "exit_code": 0, "ok": True}]
    report = {"audit_completeness": audit_hub._compute_audit_completeness(ok_status)}
    assert audit_hub._audit_incomplete_line(report) == ""
    assert audit_hub._audit_incomplete_line({}) == ""


def test_audit_incomplete_line_lists_failed_scanners():
    """告警行列出失败 scanner 名 + 原因（chapter/cluster 控制台共用同一真相源）。"""
    status = [
        {"scanner": "coherence", "exit_code": 99, "ok": False},
        {"scanner": "validate_chapter", "exit_code": 0, "ok": True},
    ]
    report = {"audit_completeness": audit_hub._compute_audit_completeness(status)}
    line = audit_hub._audit_incomplete_line(report)
    assert "审计不完整" in line
    assert "1/2" in line
    assert "coherence(timeout)" in line


def test_cluster_console_prints_incomplete_line_source_lock():
    """🔴 回归锁：cluster 模式控制台迷你摘要必须回显审计不完整行——
    _audit_incomplete_line 在 chapter(_print_summary) 与 cluster(main) 两个出口都被调用，
    且旧的「N 个校验器执行异常」兜底口径已清除（单一真相源）。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert src.count("_audit_incomplete_line(report)") >= 2, \
        "chapter 摘要与 cluster 迷你摘要都必须打印审计不完整告警"
    assert "个校验器执行异常" not in src, "旧兜底口径应清除，统一走 _audit_incomplete_line"


def test_summary_line_prints_incomplete_warning(capsys):
    """_print_summary 对不完整审计打印醒目告警（人类可读输出也不静默）。"""
    report = {
        "chapter": 9000,
        "verdict": "pass",
        "summary": {"fatal": 0, "error": 0, "warning": 0, "info": 0, "waived": 0, "total": 0},
        "waived_issues": [], "auto_fixed": [], "pending_agent": [],
        "scanner_status": [
            {"scanner": "coherence", "exit_code": 99, "ok": False},
            {"scanner": "validate_chapter", "exit_code": 0, "ok": True},
        ],
        "audit_complete": False,
        "audit_completeness": audit_hub._compute_audit_completeness([
            {"scanner": "coherence", "exit_code": 99, "ok": False},
            {"scanner": "validate_chapter", "exit_code": 0, "ok": True},
        ]),
    }
    audit_hub._print_summary(report, Path("dummy.json"))
    out = capsys.readouterr().out
    assert "审计不完整" in out
    assert "coherence" in out
    assert "timeout" in out
