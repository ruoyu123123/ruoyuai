#!/usr/bin/env python3
"""orchestrator 空壳 plan 拒跑测试（P2 工程债批次1 · 2026-06-12）

缺漏报告结论：check-quality / reconcile 的 plan 模板是 NOT-YET 空壳（零 scripts/
零 must_spawn_agent/零 pause/零 touch_outputs），orchestrator 机械走步会变成
「每步啥都不干 → step_complete → end_plan ok」的假成功。run_command 必须在
steps 循环前检测「全部 step 全空」并 raise OrchestratorError 拒跑。
边界：单个空 step 合法（纯 expected_outputs 校验步），只拦「全空」。

沙箱范式照搬 tests/test_orchestrator.py（plan_tracker 目录全指临时区·不污染真 plans）。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402


# ============ 测试隔离：plan_tracker 目录全部指向临时区（同 test_orchestrator.py） ============
class _Sandbox:
    """monkeypatch plan_tracker 模块常量 → 临时目录（不污染真实 plans/模板）。"""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        self.styles = self.tmp / "styles"
        self.global_plans = self.tmp / ".plans"
        for d in (self.templates, self.projects, self.styles, self.global_plans):
            d.mkdir(parents=True)
        self.proj_root = self.projects / "测试书"
        (self.proj_root / "_数据库").mkdir(parents=True)
        self._saved = {}

    def __enter__(self):
        for k, v in [("TEMPLATES_DIR", self.templates),
                     ("PROJECTS_DIR", self.projects),
                     ("STYLES_DIR", self.styles),
                     ("GLOBAL_PLANS_DIR", self.global_plans),
                     ("ATTEST_KEY_PATH", self.global_plans / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)

    def write_template(self, command: str, template: dict):
        (self.templates / f"{command}.plan.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


class _FakeRunner:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, *, repo_root=None, label=""):
        self.cmds.append(cmd)
        return 0


def _fake_dispatch(agent, step, ctx):
    """假 judge 派发：落一个 JudgeReport 文件（end_plan 会校验它真存在·anti-skip）。"""
    jrp = step.get("judge_report_path")
    out_raw = jrp.get(agent) if isinstance(jrp, dict) else jrp
    path = None
    if out_raw:
        p = Path(orc.resolve_placeholders(out_raw, ctx))
        if not p.is_absolute():
            p = Path(ctx["project_root"]) / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        path = p

    class _O:
        pass

    o = _O()
    o.data, o.ok, o.output_path = {"ok": True}, True, path
    return o


def _shell_step(n, name="bare"):
    """纯空壳 step：无 scripts/must_spawn_agent/pause_for_user/touch_outputs。"""
    return {"n": n, "name": f"{name}-{n}", "required": True,
            "skip_output_allowed": True, "expected_outputs": []}


def _shell_template(n_steps=3, command="test-flow"):
    return {"command": command, "total_steps": n_steps,
            "required_steps": list(range(1, n_steps + 1)), "optional_steps": [],
            "steps": [_shell_step(i) for i in range(1, n_steps + 1)]}


# ============ 核心：全空壳必拒 ============
def test_all_empty_shell_plan_rejected():
    """全部 step 全空 → OrchestratorError（拒绝假成功）且零脚本被执行。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _shell_template(3))
        runner = _FakeRunner()
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_fake_dispatch)
            assert False, "全空壳 plan 必须拒跑"
        except orc.OrchestratorError as e:
            msg = str(e)
            assert "空壳" in msg and "NOT-YET" in msg, f"报错信息不达标: {msg}"
        assert runner.cmds == []  # 拒跑发生在 steps 循环前·零执行


def test_zero_steps_plan_rejected():
    """steps 为空列表也是空壳（end_plan 会平凡通过 → 同款假成功）。"""
    with _Sandbox() as sb:
        sb.write_template("test-flow", _shell_template(0))
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(), judge_dispatch=_fake_dispatch)
            assert False, "零 step plan 必须拒跑"
        except orc.OrchestratorError as e:
            assert "空壳" in str(e)


# ============ 边界：单个空 step 合法 · 任一执行性字段即非空壳 ============
def test_single_empty_step_among_real_steps_is_legal():
    """混合形态：step1 空（纯校验步）+ step2 有 scripts → 不拦·正常跑完。"""
    with _Sandbox() as sb:
        t = _shell_template(2)
        t["steps"][1]["scripts"] = ["python core/scripts/fake.py {project_root}"]
        sb.write_template("test-flow", t)
        runner = _FakeRunner()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_fake_dispatch)
        assert s.end_report and s.end_report.get("ok")
        assert len(runner.cmds) == 1  # 单空 step 合法·真实脚本照跑


def test_each_executable_field_alone_unblocks():
    """4 个执行性字段任一存在即非空壳：scripts / must_spawn_agent /
    pause_for_user / touch_outputs 逐一验证（其余 step 全空）。"""
    variants = [
        ("scripts", ["python core/scripts/fake.py {project_root}"]),
        ("must_spawn_agent", "novel-summarizer"),
        ("pause_for_user", {"type": "integer", "default": 5}),
        ("touch_outputs", ["_数据库/.wal/.placeholder"]),
    ]
    for field, value in variants:
        with _Sandbox() as sb:
            t = _shell_template(2)
            t["steps"][0][field] = value
            if field == "must_spawn_agent":
                t["steps"][0]["judge_report_path"] = "_数据库/.wal/r.json"
            sb.write_template("test-flow", t)
            s = orc.run_command("test-flow", "测试书", key="001", auto_pilot=True,
                                script_runner=_FakeRunner(),
                                judge_dispatch=_fake_dispatch)
            assert s.end_report and s.end_report.get("ok"), \
                f"字段 {field} 存在时不应被当空壳拦截"


# ============ 真实回归：仓库里的 NOT-YET 模板必须被拒 ============
def test_real_not_yet_templates_rejected():
    """把仓库真实 NOT-YET 模板拷进沙箱 → run_command 必拒。
    锁住「NOT-YET 模板被 orchestrator 假成功执行」这条工程债不复发。
    🔴 2026-06-22 G2 P0a：check-quality 已迁移程序驱动 → 从清单移除（见
    test_check_quality_is_no_longer_notyet），只剩 reconcile。"""
    real_plans = _ROOT / "core" / "claude-home" / "plans"
    for cmd_name in ("reconcile",):
        src = real_plans / f"{cmd_name}.plan.json"
        assert src.exists(), f"真实模板缺失: {src}"
        # 前置自检：它们当前确实是空壳（哪天补齐了 scripts 本测试应同步退役）
        tpl = json.loads(src.read_text(encoding="utf-8"))
        assert all(not (s.get("scripts") or s.get("must_spawn_agent")
                        or s.get("pause_for_user") or s.get("touch_outputs"))
                   for s in tpl["steps"]), \
            f"{cmd_name} 模板已程序驱动化——请删除/更新本测试"
        with _Sandbox() as sb:
            shutil.copy(src, sb.templates / src.name)
            try:
                orc.run_command(cmd_name, "测试书", key="001",
                                script_runner=_FakeRunner(),
                                judge_dispatch=_fake_dispatch)
                assert False, f"真实 NOT-YET 模板 {cmd_name} 必须拒跑"
            except orc.OrchestratorError as e:
                assert "空壳" in str(e) and cmd_name in str(e)


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
