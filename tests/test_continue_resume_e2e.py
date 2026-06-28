#!/usr/bin/env python3
"""/continue 断点恢复端到端回归（确定性·零 API·2026-06-24 · G6 ContinueTest）。

/continue 命令（.claude/commands/continue.md）本身是 Claude 主代理驱动的「检测中断点 →
续跑 cluster-write / cluster-save-state」流程。它的两块**程序化地基**此前缺端到端覆盖：

  1. `wal_recovery.py`（断点检测的**唯一权威源**·continue.md 第一步）——既有
     test_wal_recovery.py 只**复现** main() 内的 _done/_aborted 闭包逻辑，从未真跑过
     main() 的 exit-code 契约 + 「续跑 --n N」续跑指令输出。本文件补真 main() 集成：
       · 全 plan 完成 → exit 0（情况 C：写下一个 cluster）
       · 有中断 plan → exit 1 + 「续跑 ... --n <done+1>」（情况 A/A2/B）
       · --cluster 过滤把多 plan 收敛到目标 cluster（cluster mode 续跑入口）

  2. orchestrator resume 的 **WAL 跳过已完成 step** 语义（orchestrator.py:797·情况 A2/B
     「plan_tracker 已记录完成的 step 跳过」）——既有 test_orchestrator_resume_semantics.py
     覆盖 paused-judge 复用 + tampered 拒跑，但没覆盖「脚本中途失败 → plan 停 → resume
     跳过已完成步 → 跑完剩余 → end_plan 绿」这条最常见的写作中断恢复路径。本文件用
     确定性 spy runner（首跑 step2 失败·resume 后成功）补上，并钉死「已完成步的脚本
     resume 时绝不重跑」。

零依赖范式（__main__ 自跑 + pytest 均可）：不用 pytest fixture，手动 monkeypatch
（save → setattr → finally 还原）。绝不打真 API。
"""
import io
import json
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc       # noqa: E402
import plan_tracker as pt        # noqa: E402
import wal_recovery as wr        # noqa: E402


# ============================================================================
# 1) wal_recovery.main() 集成：断点检测的唯一权威源（continue.md 第一步）
# ============================================================================
def _write_plan(plans_dir: Path, plan: dict):
    (plans_dir / f"{plan['id']}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def _mk_plan(pid, project, command, key, total, done, *, completed=False):
    """构造 plan_tracker 真实形态的 plan JSON（时间戳表完成·顶层无 status·
    steps[].status ∈ pending/completed）。done = 已完成步数。"""
    steps = []
    for i in range(1, total + 1):
        steps.append({"n": i, "name": f"s{i}",
                      "status": "completed" if i <= done else "pending"})
    return {
        "id": pid, "command": command, "project": project, "key": key,
        "chapter": None, "total_steps": total,
        "completed_at": "2026-06-24T00:00:00" if completed else None,
        "aborted_at": None, "steps": steps,
    }


@contextmanager
def _wal_sandbox():
    """造一个隔离沙盒项目 + 空 GLOBAL_PLANS_DIR（防真 .plans 里别项目 plan 串味）。"""
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "续跑测试书"
    plans_dir = proj / "_数据库" / ".plans"
    plans_dir.mkdir(parents=True)
    empty_global = tmp / "_empty_global"
    empty_global.mkdir()
    saved = pt.GLOBAL_PLANS_DIR
    pt.GLOBAL_PLANS_DIR = empty_global   # wal_recovery 内 `from plan_tracker import
                                         # GLOBAL_PLANS_DIR` 是惰性 import·会拿到这个值
    try:
        yield proj, plans_dir
    finally:
        pt.GLOBAL_PLANS_DIR = saved


def _run_wal_main(*argv) -> tuple[int, str]:
    """跑 wal_recovery.main()·捕获 SystemExit 退出码 + stdout。"""
    buf = io.StringIO()
    saved_argv = sys.argv
    sys.argv = ["wal_recovery.py", *map(str, argv)]
    try:
        with redirect_stdout(buf):
            wr.main()
        code = 0          # main() 不一定显式 exit(0)？它会——但兜底
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = saved_argv
    return code, buf.getvalue()


def test_wal_recovery_all_done_exit0():
    """情况 C：所有 plan 完整结束 → exit 0（continue 进入「写下一个 cluster」）。"""
    with _wal_sandbox() as (proj, plans_dir):
        _write_plan(plans_dir, _mk_plan(
            "续跑测试书_csv_001", "续跑测试书", "cluster-save-state",
            "cluster_001", total=14, done=14, completed=True))
        code, out = _run_wal_main(proj)
        assert code == 0, f"全完成应 exit 0·实际 {code}\n{out}"
        assert "未完成 0" in out, out


def test_wal_recovery_interrupted_exit1_with_resume_step():
    """情况 A/A2/B：有中断 plan → exit 1 + 「续跑 ... --n <done+1>」续跑指令。"""
    with _wal_sandbox() as (proj, plans_dir):
        # cluster-write 7 步只完成 3 步 → 续跑应从 step 4
        _write_plan(plans_dir, _mk_plan(
            "续跑测试书_cw_002", "续跑测试书", "cluster-write",
            "cluster_002", total=7, done=3))
        code, out = _run_wal_main(proj)
        assert code == 1, f"有中断应 exit 1·实际 {code}\n{out}"
        assert "🔴 中断" in out, out
        assert "--n 4" in out, f"续跑步号应为 done+1=4·实际输出:\n{out}"
        assert "续跑测试书_cw_002" in out


def test_wal_recovery_cluster_filter_narrows():
    """cluster mode 续跑入口：--cluster 把多 plan 收敛到目标 cluster。

    003(done)+004(中断) 共存：--cluster 004 → 只看到 004 中断 → exit 1；
    --cluster 003 → 只看到 003 完成 → exit 0。证明 _filter_plans_by_cluster 经 main()
    真生效（既有测试只直调过该函数·没走过 main 的过滤分支 + exit 语义）。"""
    with _wal_sandbox() as (proj, plans_dir):
        _write_plan(plans_dir, _mk_plan(
            "续跑测试书_csv_003", "续跑测试书", "cluster-save-state",
            "cluster_003", total=14, done=14, completed=True))
        _write_plan(plans_dir, _mk_plan(
            "续跑测试书_cw_004", "续跑测试书", "cluster-write",
            "cluster_004", total=7, done=2))
        # 不过滤 → 有中断 → exit 1
        code_all, _ = _run_wal_main(proj)
        assert code_all == 1
        # --cluster 004 → 命中中断 plan
        code4, out4 = _run_wal_main(proj, "--cluster", "004")
        assert code4 == 1, f"cluster 004 应报中断·实际 {code4}\n{out4}"
        assert "--n 3" in out4, out4
        assert "续跑测试书_cw_004" in out4
        assert "续跑测试书_csv_003" not in out4, "过滤后不应混入 003"
        # --cluster 003 → 只剩完成 plan → exit 0
        code3, out3 = _run_wal_main(proj, "--cluster", "003")
        assert code3 == 0, f"cluster 003 已完成应 exit 0·实际 {code3}\n{out3}"


# ============================================================================
# 2) orchestrator resume：WAL 跳过已完成 step（情况 A2/B 续跑机制）
# ============================================================================
class _OrcSandbox:
    """复用 test_orchestrator_resume_semantics._Sandbox 形态：重定向 plan 存储。"""
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        self.styles = self.tmp / "styles"
        self.global_plans = self.tmp / ".plans"
        for d in (self.templates, self.projects, self.styles, self.global_plans):
            d.mkdir(parents=True)
        self.proj_root = self.projects / "续跑测试书"
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

    def write_template(self, command, template):
        (self.templates / f"{command}.plan.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


def _three_script_template():
    """3 个纯脚本步（无 agent / 无 pause）·每步 skip_output·脚本行不真跑（spy runner
    拦截·路径不必存在）。"""
    def _step(n):
        return {"n": n, "name": f"写作步{n}", "required": True,
                "skip_output_allowed": True, "expected_outputs": [],
                "scripts": [f"core/scripts/_noop_step{n}.py --x"]}
    return {"command": "cluster-write", "total_steps": 3,
            "required_steps": [1, 2, 3], "optional_steps": [],
            "steps": [_step(1), _step(2), _step(3)]}


class _FailOnceRunner:
    """spy script_runner：记录每次调用的 label；step2 的脚本首次调用返回 1（失败），
    之后返回 0。其余步恒 0。"""
    def __init__(self):
        self.calls = []          # list[label]
        self._step2_failed_once = False

    def __call__(self, cmd, *, repo_root=None, label=""):
        self.calls.append(label)
        if label == "step2" and not self._step2_failed_once:
            self._step2_failed_once = True
            return 1             # 模拟写作脚本中途崩（如 audit/splitter 失败）
        return 0


def test_resume_skips_completed_steps_after_failure():
    """情况 A2/B 核心：脚本中途失败 → plan 停 → resume 跳过已完成步 → 跑完 → end 绿。"""
    with _OrcSandbox() as sb:
        sb.write_template("cluster-write", _three_script_template())
        runner = _FailOnceRunner()

        # —— 第一跑：step1 过·step2 脚本失败 → OrchestratorError·plan 停 ——
        try:
            orc.run_command("cluster-write", "续跑测试书", key="001",
                            script_runner=runner, judge_dispatch=lambda *a, **k: None,
                            pause_handler=None)
            raise AssertionError("step2 失败应抛 OrchestratorError")
        except orc.OrchestratorError as e:
            assert "退出码 1" in str(e), str(e)

        # plan 持久态：step1 completed·step2/3 未完成
        plans = list(sb.global_plans.glob("*.json"))
        # plan 落在项目 _数据库/.plans（runtime_plans_dir）·而非 global——两处都扫
        all_plan_files = (list((sb.proj_root / "_数据库" / ".plans").glob("*.json"))
                          + plans)
        assert all_plan_files, "应已落 plan 文件"
        plan_data = json.loads(all_plan_files[0].read_text(encoding="utf-8"))
        plan_id = plan_data["id"]
        st = {s["n"]: s["status"] for s in plan_data["steps"]}
        assert st[1] == pt.STATUS_COMPLETED, f"step1 应完成·实际 {st}"
        assert st[2] != pt.STATUS_COMPLETED, f"step2 不应完成·实际 {st}"

        # —— 续跑：step1 跳过（不重跑脚本）·step2 重试成功·step3 跑·end 绿 ——
        runner.calls.clear()
        summary = orc.run_command("cluster-write", "续跑测试书", key="001",
                                  resume_plan_id=plan_id,
                                  script_runner=runner,
                                  judge_dispatch=lambda *a, **k: None,
                                  pause_handler=None)
        # step1 的脚本 resume 时绝不重跑（WAL 跳过已完成步·北极星「不重复已完成工作」）
        assert "step1" not in runner.calls, \
            f"step1 已完成·resume 不应重跑其脚本·实际调用 {runner.calls}"
        assert "step2" in runner.calls and "step3" in runner.calls, \
            f"step2/3 应在 resume 跑·实际 {runner.calls}"
        # summary：step1 skipped·step2/3 completed·end_plan 绿
        outcome = {o.n: o.status for o in summary.completed}
        assert outcome.get(1) == "skipped", f"step1 应 skipped·实际 {outcome}"
        assert outcome.get(2) == "completed" and outcome.get(3) == "completed", outcome
        assert summary.end_report.get("ok"), summary.end_report


def test_resume_is_idempotent_when_already_complete():
    """情况 C 边界：plan 已全完成后再 resume → 全步 skipped·脚本零重跑·end 绿（幂等）。

    对应 continue.md「残留 active plan / 已完成不重跑」——续跑一个已 12/12 的 plan
    不能把正文重写一遍。"""
    with _OrcSandbox() as sb:
        sb.write_template("cluster-write", _three_script_template())
        runner = _FailOnceRunner()
        runner._step2_failed_once = True   # 本测试不触发失败·让 step2 直接成功
        s1 = orc.run_command("cluster-write", "续跑测试书", key="002",
                             script_runner=runner, judge_dispatch=lambda *a, **k: None,
                             pause_handler=None)
        assert s1.end_report.get("ok")
        assert {o.status for o in s1.completed} == {"completed"}

        runner.calls.clear()
        s2 = orc.run_command("cluster-write", "续跑测试书", key="002",
                             resume_plan_id=s1.plan_id,
                             script_runner=runner, judge_dispatch=lambda *a, **k: None,
                             pause_handler=None)
        assert runner.calls == [], f"全完成 plan resume 不应重跑任何脚本·实际 {runner.calls}"
        assert {o.status for o in s2.completed} == {"skipped"}
        assert s2.end_report.get("ok")


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
