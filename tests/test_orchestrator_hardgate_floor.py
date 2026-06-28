#!/usr/bin/env python3
"""orchestrator C04/C05 hard_gate 质量地板 + 修复重验闭环 + C03 content_check advisory
（🔴 2026-06-27 接线 W2）。

覆盖：
  · _residual_hard_gate_issues 单测——只认 audit_hub.HARD_GATE_CODES·豁免/advisory/无报告 处理。
  · C04 质量地板：seed 残留 hard_gate → 有界修复用尽 → raise·停 step·不进切章下一步。
  · C04 修复闭环：validator 修掉（reaudit clean）→ 放行·进下一步。
  · 🔴 纯 advisory 残留 → 不触发 FAIL（北极星护栏命门·最重要回归锁）。
  · C05 reverify：exit2 派单后有界修-验·真 hard_gate 用尽 raise·advisory exit2 早返不 raise。
  · 向后兼容：未声明 hard_gate_reverify → exit2 单次 dispatch·有残留也不拦（floor 不激活）。
  · C03 content_check advisory（cluster-save-state validate 步形态）：inert→记 waiver 放行·
    bypass→不记·填满载荷→不记。

zero-dep：tests/run_tests.py importlib 直调无参 test_*；也兼容 pytest。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402
import plan_step_gates as gates  # noqa: E402
from test_orchestrator import _Sandbox, _FakeDispatch  # noqa: E402


# 一个真在 audit_hub.HARD_GATE_CODES 内的 code（权威清单·硬）与一个不在的（advisory）
_HARD_CODE = "STYLE_单段超长"
_ADVISORY_CODE = "SEMANTIC_SLOP"


def _issue(code, gate_level="hard_gate", waived=False):
    return {"code": code, "gate_level": gate_level, "waived": waived,
            "desc": f"{code} 测试 issue"}


# ════════════════════════════════════════════════════════════════════
# 单测：_residual_hard_gate_issues（只认 HARD_GATE_CODES）
# ════════════════════════════════════════════════════════════════════
def _write_report(path: Path, issues, verdict="needs_agent"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verdict": verdict, "issues": issues},
                               ensure_ascii=False), encoding="utf-8")


def test_residual_only_counts_hard_gate_codes():
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "audit.json"
        _write_report(rp, [_issue(_HARD_CODE), _issue(_ADVISORY_CODE, "advisory")])
        res = orc._residual_hard_gate_issues(rp)
        assert [i["code"] for i in res] == [_HARD_CODE]   # advisory 绝不计入


def test_residual_excludes_waived_hard_gate():
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "audit.json"
        _write_report(rp, [_issue(_HARD_CODE, waived=True)])
        assert orc._residual_hard_gate_issues(rp) == []   # 已豁免不计


def test_residual_advisory_only_is_empty_not_none():
    """纯 advisory 报告 → []（非 None）→ floor/reverify 视为 0 残留放行（北极星护栏命门）。"""
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "audit.json"
        _write_report(rp, [_issue(_ADVISORY_CODE, "advisory"),
                           _issue("REPEAT_NOUN", "advisory")])
        assert orc._residual_hard_gate_issues(rp) == []


def test_residual_missing_report_is_none():
    assert orc._residual_hard_gate_issues(Path("/绝不存在_xyz/audit.json")) is None
    assert orc._residual_hard_gate_issues(None) is None


def test_residual_fake_gate_level_not_in_codes_not_counted():
    """报告自报 gate_level=hard_gate 但 code 不在权威清单 → 不计（防 scanner 自立硬门）。"""
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "audit.json"
        _write_report(rp, [_issue("SOME_RANDOM_ADVISORY", "hard_gate")])
        # audit_hub 可导入时按 code 清单判定 → 不在清单 → []
        assert orc._residual_hard_gate_issues(rp) == []


def test_hard_gate_codes_loaded():
    codes = orc._hard_gate_codes()
    assert _HARD_CODE in codes and "LOCKED_FACT_CONFLICT" in codes
    assert _ADVISORY_CODE not in codes


# ════════════════════════════════════════════════════════════════════
# 集成：C04 质量地板 + C05 reverify（合成模板 + 受控 audit 报告）
# ════════════════════════════════════════════════════════════════════
class _AuditReportRunner:
    """模拟 audit_hub：被调时把受控 issues 写进 .audit/cluster_001_audit.json。
    fix_on_reaudit=True → 第 2 次起写 clean（模拟 validator 修掉后 reaudit 通过）。"""

    def __init__(self, proj_root, *, issues, exit_code=0, fix_on_reaudit=False):
        self.proj = Path(proj_root)
        self.issues = issues
        self.exit_code = exit_code
        self.fix_on_reaudit = fix_on_reaudit
        self.cmds = []
        self.audit_calls = 0

    def __call__(self, cmd, *, repo_root=None, label=""):
        self.cmds.append(cmd)
        if "audit_hub.py" in cmd:
            self.audit_calls += 1
            rp = self.proj / "_数据库" / ".audit" / "cluster_001_audit.json"
            issues = ([] if (self.fix_on_reaudit and self.audit_calls >= 2)
                      else self.issues)
            _write_report(rp, issues, "needs_agent" if issues else "pass")
            return self.exit_code
        return 0


def _floor_template(*, rv=True, dispatch_branch=False):
    cf = {}
    if dispatch_branch:
        cf["exit_codes"] = {"0": "ok", "1": "ok",
                            "2": "dispatch:novel-validator-checker", "3": "fail"}
    if rv:
        cf["hard_gate_reverify"] = {
            "audit_report": "_数据库/.audit/cluster_001_audit.json",
            "reaudit_script": ("python core/scripts/audit_hub.py {project_root} "
                               "--mode cluster --cluster-id 001 --auto-fix"),
            "fix_agent": "novel-validator-checker",
            "max_fix_rounds": 2,
        }
    step1 = {"n": 1, "name": "cluster-quality-full-stack", "required": True,
             "skip_output_allowed": True, "expected_outputs": [],
             "scripts": ["python core/scripts/audit_hub.py {project_root} "
                         "--mode cluster --cluster-id 001 --auto-fix"],
             "control_flow": cf}
    step2 = {"n": 2, "name": "splitter", "required": True,
             "skip_output_allowed": True, "expected_outputs": [],
             "scripts": ["python core/scripts/chapter_splitter.py {project_root}"]}
    return {"command": "test-flow", "total_steps": 2, "required_steps": [1, 2],
            "optional_steps": [], "steps": [step1, step2]}


def _splitter_ran(runner):
    return any("chapter_splitter.py" in c for c in runner.cmds)


def test_floor_blocks_garbage_residual_hard_gate():
    """seed 残留 hard_gate（不可修）→ C04 地板有界修复用尽 → raise·不进切章 step2。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _floor_template(rv=True))
        runner = _AuditReportRunner(sb.proj_root, issues=[_issue(_HARD_CODE)],
                                    exit_code=0, fix_on_reaudit=False)
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
            assert False, "残留 hard_gate 必须被 C04 地板拦下"
        except orc.OrchestratorError as e:
            assert "hard_gate 质量地板" in str(e)
            assert _HARD_CODE in str(e)
        assert not _splitter_ran(runner), "带病草稿绝不能进切章 step2"
        # plan 停在 step1（可 resume）：step1 未完成
        actives = [a for a in pt.find_active_plans()
                   if a["plan"].get("command") == "test-flow"]
        assert actives
        steps = {s["n"]: s["status"] for s in actives[0]["plan"]["steps"]}
        assert steps[1] != pt.STATUS_COMPLETED and steps[2] != pt.STATUS_COMPLETED


def test_floor_passes_after_validator_fixes():
    """validator 修掉（reaudit clean）→ 地板放行 → 进切章 step2·end_plan 全绿。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _floor_template(rv=True))
        runner = _AuditReportRunner(sb.proj_root, issues=[_issue(_HARD_CODE)],
                                    exit_code=0, fix_on_reaudit=True)
        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        assert _splitter_ran(runner), "修掉后应进切章 step2"
        # 修复闭环至少派了一次 validator-checker
        assert any(c["agent"] == "novel-validator-checker" for c in dispatch.calls)


def test_pure_advisory_residual_never_fails():
    """🔴 北极星护栏命门（最重要回归锁）：纯 advisory 残留（exit2 needs_agent）→ 绝不 FAIL·
    地板放行 + reverify 早返·正常进切章 step2。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow",
                          _floor_template(rv=True, dispatch_branch=True))
        runner = _AuditReportRunner(
            sb.proj_root, issues=[_issue(_ADVISORY_CODE, "advisory"),
                                  _issue("REPEAT_NOUN", "advisory")],
            exit_code=2, fix_on_reaudit=False)
        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok"), "纯 advisory 残留绝不阻断"
        assert _splitter_ran(runner)
        # advisory-only → C05 reverify pre-residual 为空 → 早返·不派 validator-checker
        assert not any(c["agent"] == "novel-validator-checker"
                       for c in dispatch.calls)


def test_c05_reverify_raises_on_genuine_hard_gate():
    """C05：exit2 派单后有界修-验·真 hard_gate 用尽仍残留 → raise·不进切章 step2。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow",
                          _floor_template(rv=True, dispatch_branch=True))
        runner = _AuditReportRunner(sb.proj_root, issues=[_issue(_HARD_CODE)],
                                    exit_code=2, fix_on_reaudit=False)
        dispatch = _FakeDispatch()
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
            assert False, "真 hard_gate 必须被 C05 reverify 拦下"
        except orc.OrchestratorError as e:
            assert "hard_gate 质量地板" in str(e)
        assert not _splitter_ran(runner)
        # reverify 有界修复确实派了 validator-checker（≥1 次）
        assert any(c["agent"] == "novel-validator-checker" for c in dispatch.calls)


def test_no_reverify_declared_backward_compat():
    """未声明 hard_gate_reverify：exit2 → 单次 dispatch（原行为）·有残留 hard_gate 也不拦·
    地板不激活（向后兼容·零回归）。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow",
                          _floor_template(rv=False, dispatch_branch=True))
        runner = _AuditReportRunner(sb.proj_root, issues=[_issue(_HARD_CODE)],
                                    exit_code=2, fix_on_reaudit=False)
        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok"), "无 reverify 声明 → 不激活地板·原样跑完"
        assert _splitter_ran(runner)
        # 原行为：exit2 派单 validator-checker 恰一次
        vc = [c for c in dispatch.calls if c["agent"] == "novel-validator-checker"]
        assert len(vc) == 1


# ════════════════════════════════════════════════════════════════════
# 集成：C03 content_check advisory（cluster-save-state validate 步形态）
# ════════════════════════════════════════════════════════════════════
def _content_check_template():
    return {"command": "test-flow", "total_steps": 1, "required_steps": [1],
            "optional_steps": [],
            "steps": [{"n": 1, "name": "validate-cluster", "required": True,
                       "skip_output_allowed": True, "expected_outputs": [],
                       "gate_content_check_advisory": True,
                       "scripts": ["python core/scripts/x.py {project_root}"]}]}


class _NoopRunner:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, *, repo_root=None, label=""):
        self.cmds.append(cmd)
        return 0


def test_content_check_advisory_inert_records_waiver_not_block():
    """缺/inert 载荷子系统 → advisory：记 waiver 放行（绝不 raise·拦截权在 cluster-write step3）。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _content_check_template())
        # _数据库 存在但 0 子系统 JSON → check_subsystems ok=False（advisory 处理）
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_NoopRunner(),
                            judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok"), "content_check advisory 绝不阻断入库"
        assert any(w["code"] == "SUBSYSTEM_CONTENT_INERT" for w in s.gate_waivers)
        sink = sb.proj_root / "_数据库" / ".gate_waivers.json"
        assert sink.exists()
        recs = json.loads(sink.read_text(encoding="utf-8"))
        assert recs[-1]["code"] == "SUBSYSTEM_CONTENT_INERT"
        assert len(recs[-1]["reason"]) <= 300


def test_content_check_advisory_bypass_no_waiver():
    with _Sandbox() as sb:
        sb.write_template("test-flow", _content_check_template())
        (sb.proj_root / "_数据库" / gates.SUBSYSTEMS_BYPASS_FILE).write_text(
            "{}", encoding="utf-8")
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_NoopRunner(),
                            judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert not s.gate_waivers          # 旁路 → 门跳过·不记 waiver
        assert not (sb.proj_root / "_数据库" / ".gate_waivers.json").exists()


def test_content_check_advisory_filled_load_bearing_no_waiver():
    """34 子系统齐 + 3 个载荷文件非空（涟漪规则/大势卡/事件簇）→ content_check 过·无 waiver。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _content_check_template())
        db = sb.proj_root / "_数据库"
        for f in gates.ALL_REQUIRED:
            (db / f).write_text("{}", encoding="utf-8")
        # 填 3 个载荷文件（marker any_of 命中 → 非 inert）
        (db / "涟漪规则.json").write_text(json.dumps(
            {"ripple_rules": [{"id": "R1", "trigger_type": "auto_tick"}]},
            ensure_ascii=False), encoding="utf-8")
        (db / "大势卡.json").write_text(json.dumps(
            {"major_events": [{"id": "ME-V1-01", "volume": 1,
                               "is_volume_finale": True}]},
            ensure_ascii=False), encoding="utf-8")
        (db / "事件簇.json").write_text(json.dumps(
            {"clusters": [{"cluster_id": "cluster_001",
                           "scene_storyboard": [{"scene": 1}]}]},
            ensure_ascii=False), encoding="utf-8")
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_NoopRunner(),
                            judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert not s.gate_waivers           # 载荷非空 → 不记 waiver
        assert s.gates_engaged >= 1         # 门确实触发过


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
