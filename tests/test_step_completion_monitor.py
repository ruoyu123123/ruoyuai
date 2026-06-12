#!/usr/bin/env python3
"""step_completion_monitor Saga 缺步监控语义锁测试（MAPE-K 最后一个零测试组件 · 2026-06-13）

缺漏报告结论：step_completion_monitor 是 cluster-save-state step 9 的缺步监控 +
幂等补全器，但三类缺步分类（output_missing 假完成 / failed / not_run 中断）、
--auto-heal 的幂等重跑、以及两条北极星红线（agent/marker 步绝不自动执行 ·
不改 plan status）此前零测试锁定——任何回归都会让「缺步可补」退化回
「假完成蒙混过关」或越权替主代理跑 agent 步。

隔离策略：
  · monkeypatch plan_tracker 的 PROJECTS_DIR/STYLES_DIR/GLOBAL_PLANS_DIR 指向 tmp
    （plan fixture 直接落 tmp 项目 _数据库/.plans/·unattested plan 读路径放行）
  · auto-heal 用 stub 替身 ar.run_with_resilience（不真跑子进程 → 系统级
    core/claude-home/runtime/ 的 incidents/circuit 零污染）
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import plan_tracker as pt              # noqa: E402
import step_completion_monitor as sm   # noqa: E402

PROJECT = "testbook"


@contextmanager
def _sandbox():
    """tmp 沙箱：plan_tracker 全部落盘路径改指 tmp·退出还原（不碰真 workspace/）。"""
    saved = (pt.PROJECTS_DIR, pt.STYLES_DIR, pt.GLOBAL_PLANS_DIR, pt.ATTEST_KEY_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        pt.PROJECTS_DIR = t / "workspace" / "novels"
        pt.STYLES_DIR = t / "workspace" / "styles"
        pt.GLOBAL_PLANS_DIR = t / "core" / "claude-home" / ".plans"
        pt.ATTEST_KEY_PATH = pt.GLOBAL_PLANS_DIR / ".attest_key"
        root = pt.PROJECTS_DIR / PROJECT
        (root / "_数据库" / ".plans").mkdir(parents=True)
        try:
            yield root
        finally:
            (pt.PROJECTS_DIR, pt.STYLES_DIR,
             pt.GLOBAL_PLANS_DIR, pt.ATTEST_KEY_PATH) = saved


def _step(n, name, status, outputs=None, scripts=None, agent=None):
    s = {"n": n, "name": name, "status": status,
         "expected_outputs": outputs or [], "verified_outputs": []}
    if scripts is not None:
        s["scripts"] = scripts
    if agent:
        s["must_spawn_agent"] = agent
    return s


def _write_plan(root, steps, required=None, ended=False, suffix="000001"):
    """直接写 plan JSON fixture（无 _attestation = unattested·get_plan 只读放行）。"""
    plan_id = f"{PROJECT}_c001_cluster-write_20260613T{suffix}"
    plan = {"plan_id": plan_id, "command": "cluster-write", "project": PROJECT,
            "required_steps": required if required is not None else [s["n"] for s in steps],
            "started_at": "2026-06-13T00:00:00",
            "completed_at": "2026-06-13T01:00:00" if ended else None,
            "abort_reason": None, "steps": steps}
    p = root / "_数据库" / ".plans" / f"{plan_id}.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_id


class _StubRunner:
    """替身 run_with_resilience：记录调用·decide 决定 ok·ok 时执行 effect 产出文件。"""

    def __init__(self, effect=None, decide=None):
        self.calls = []
        self.effect = effect            # callable(toks)：模拟脚本副作用（补产出）
        self.decide = decide or (lambda toks: True)

    def __call__(self, cmd, label=None, **kw):
        toks = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        self.calls.append({"toks": toks, "label": label})
        ok = self.decide(toks)
        if ok and self.effect:
            self.effect(toks)
        return {"ok": ok, "degraded": not ok, "exit_code": 0 if ok else 1,
                "action": "ok" if ok else "degrade", "label": label}


@contextmanager
def _patched_runner(stub):
    saved = sm.ar.run_with_resilience
    sm.ar.run_with_resilience = stub
    try:
        yield stub
    finally:
        sm.ar.run_with_resilience = saved


# ============ 1) scan 三类缺步分类 ============
def test_scan_classifies_three_missing_kinds():
    """completed+产出缺=output_missing / failed / required pending=not_run；
    产出齐全的 completed 步零误报；healable/agent_needed 分桶正确。"""
    with _sandbox() as root:
        (root / "_数据库" / "ok.json").write_text("{}", encoding="utf-8")
        plan_id = _write_plan(root, [
            _step(1, "假完成步", pt.STATUS_COMPLETED,
                  outputs=["_数据库/out1.json"], scripts=["python {project_root}/gen1.py"]),
            _step(2, "失败步", pt.STATUS_FAILED, agent="novel-writer"),
            _step(3, "未跑步", pt.STATUS_PENDING),
            _step(4, "健康步", pt.STATUS_COMPLETED, outputs=["_数据库/ok.json"]),
        ])
        rep = sm.scan(plan_id)
        assert not rep.get("error")
        by_n = {f["n"]: f for f in rep["findings"]}
        assert set(by_n) == {1, 2, 3}, "健康步(4)不得误报"
        assert by_n[1]["reason"] == "output_missing"
        assert by_n[1]["missing"] and by_n[1]["missing"][0].endswith("out1.json")
        assert by_n[2]["reason"] == "failed"
        assert by_n[3]["reason"] == "not_run"
        # heal_kind 分桶：有 scripts=scripts / 仅 agent=agent / 都没有=marker
        assert by_n[1]["heal_kind"] == "scripts"
        assert by_n[2]["heal_kind"] == "agent" and by_n[2]["spawn_agent"] == "novel-writer"
        assert by_n[3]["heal_kind"] == "marker"
        assert [f["n"] for f in rep["healable"]] == [1], "只有 scripts 类 output_missing/failed 可补"
        assert [f["n"] for f in rep["agent_needed"]] == [2]


# ============ 2) not_run 抑制条件 + plan 不存在 ============
def test_scan_not_run_suppressed_when_plan_ended_or_not_required():
    """plan 已 end → pending 不算缺步；非 required pending 不算；in_progress 算 not_run；
    plan 不存在 → error 不抛异常。"""
    with _sandbox() as root:
        # 已 end 的 plan：pending required 步不报 not_run
        pid_ended = _write_plan(root, [_step(1, "尾步", pt.STATUS_PENDING)],
                                ended=True, suffix="000002")
        assert sm.scan(pid_ended)["findings"] == []
        # 非 required 的 pending 步不报；required 的 in_progress 步算 not_run
        pid = _write_plan(root, [
            _step(1, "可选步", pt.STATUS_PENDING),
            _step(2, "进行中步", pt.STATUS_IN_PROGRESS),
        ], required=[2], suffix="000003")
        rep = sm.scan(pid)
        assert [(f["n"], f["reason"]) for f in rep["findings"]] == [(2, "not_run")]
        # plan 不存在：返回 error 字段（不抛异常·findings 空）
        rep2 = sm.scan("no_such_plan_xyz")
        assert rep2.get("error") and rep2["findings"] == []


# ============ 3) _clean_scripts：注释/空行/advisory 前缀 ============
def test_clean_scripts_comment_blank_advisory():
    """`#` 注释行/空行/None 剔除；`? ` advisory 前缀剥掉并标 advisory=True。"""
    out = sm._clean_scripts({"scripts": [
        "# 注释行不跑", "", "   ", None,
        "python a.py --x 1",
        "? python b.py --advisory",
    ]})
    assert out == [("python a.py --x 1", False), ("python b.py --advisory", True)]
    assert sm._clean_scripts({}) == []


# ============ 4) auto-heal：scripts 步重跑补产出 ============
def test_auto_heal_scripts_step_heals_output():
    """stub 重跑产出文件 → healed；{project_root} 占位替换；` || true` 剥除；
    advisory 行失败 → action=advisory_nonzero_ignored 不算败。"""
    with _sandbox() as root:
        target = root / "_数据库" / "healed.json"
        plan_id = _write_plan(root, [
            _step(1, "假完成步", pt.STATUS_COMPLETED, outputs=["_数据库/healed.json"],
                  scripts=["python {project_root}/tool.py --make || true",
                           "? python advisory_check.py"]),
        ])
        stub = _StubRunner(
            effect=lambda toks: target.write_text("{}", encoding="utf-8"),
            decide=lambda toks: "advisory_check.py" not in " ".join(toks))
        with _patched_runner(stub):
            rep = sm.auto_heal(plan_id)
        assert len(stub.calls) == 2
        # 占位符替换成项目根 + ` || true` 剥除（不交给 shell 静默吞错）
        toks0 = stub.calls[0]["toks"]
        assert toks0[1] == f"{root}/tool.py" and toks0[2] == "--make"
        assert "||" not in toks0 and "true" not in toks0
        assert stub.calls[0]["label"] == "heal_s1_cluster-write"
        # advisory 行 stub 判败 → 被忽略为 advisory_nonzero_ignored（不影响 heal 结论）
        actions = {r["action"] for r in rep["ran"]}
        assert "advisory_nonzero_ignored" in actions
        assert all(r["ok"] for r in rep["ran"])
        assert rep["healed_steps"] == [1] and rep["still_missing_steps"] == []
        assert rep["needs_main_agent"] == []
        assert target.exists()


# ============ 5) auto-heal 幂等：跑两次结果一致 ============
def test_auto_heal_idempotent_two_runs_same_result():
    """第 1 次补全产出；第 2 次立刻重跑 = 零目标 no-op（不重复执行脚本）；
    删产出后第 3 次重跑 → 与第 1 次结论逐字段一致（Saga 补偿幂等纪律）。"""
    with _sandbox() as root:
        target = root / "_数据库" / "idem.json"
        plan_id = _write_plan(root, [
            _step(1, "假完成步", pt.STATUS_COMPLETED, outputs=["_数据库/idem.json"],
                  scripts=["python {project_root}/gen.py"]),
        ])
        stub = _StubRunner(effect=lambda toks: target.write_text("{}", encoding="utf-8"))
        with _patched_runner(stub):
            r1 = sm.auto_heal(plan_id)
            r2 = sm.auto_heal(plan_id)          # 产出已在 → 不该再跑脚本
            target.unlink()                      # 模拟产出再次丢失
            r3 = sm.auto_heal(plan_id)
        assert r1["healed_steps"] == [1] and len(stub.calls) == 2, \
            "第 2 次必须 no-op（共 2 次调用 = 第 1 次 + 第 3 次）"
        assert r2["ran"] == [] and r2["healed_steps"] == [] and r2["still_missing_steps"] == []
        # 幂等：再次补全与首次结论一致
        for k in ("healed_steps", "still_missing_steps", "needs_main_agent"):
            assert r3[k] == r1[k], f"幂等重跑 {k} 不一致: {r3[k]} != {r1[k]}"
        assert target.exists()


# ============ 6) 红线：agent / marker 步绝不自动执行 + 不改 plan status ============
def test_auto_heal_red_line_never_executes_agent_or_marker():
    """must_spawn_agent 步（judge/writer 这类 hard_gate 守门步）与 marker 步
    绝不被 auto-heal 自动执行——只进 needs_main_agent brief；
    且 auto-heal 全程不改 plan status（status 权威仍是 plan_tracker）。"""
    with _sandbox() as root:
        plan_id = _write_plan(root, [
            _step(1, "writer假完成", pt.STATUS_COMPLETED,
                  outputs=["_数据库/draft.txt"], agent="novel-writer"),
            _step(2, "judge失败", pt.STATUS_FAILED, agent="novel-quality-judge"),
            _step(3, "marker失败", pt.STATUS_FAILED),
            _step(4, "agent未跑", pt.STATUS_PENDING, agent="novel-chapter-splitter"),
        ])
        plan_path = root / "_数据库" / ".plans" / f"{plan_id}.json"
        before = json.loads(plan_path.read_text(encoding="utf-8"))
        stub = _StubRunner()
        with _patched_runner(stub):
            rep = sm.auto_heal(plan_id)
            rep_inc = sm.auto_heal(plan_id, include_not_run=True)
        assert stub.calls == [], "红线：agent/marker 步绝不自动执行（include_not_run 也不行）"
        for r in (rep, rep_inc):
            assert r["ran"] == [] and r["healed_steps"] == []
        na = {x["n"]: x for x in rep["needs_main_agent"]}
        assert set(na) == {1, 2, 3, 4}
        assert na[1]["spawn_agent"] == "novel-writer" and na[1]["heal_kind"] == "agent"
        assert na[2]["spawn_agent"] == "novel-quality-judge"
        assert na[3]["heal_kind"] == "marker"
        # 北极星：monitor 只补产出·不碰 plan（status/attestation 一字不动）
        after = json.loads(plan_path.read_text(encoding="utf-8"))
        assert after == before, "auto-heal 不得改写 plan 文件"


# ============ 7) not_run 默认不补 · --include-not-run 才补 ============
def test_auto_heal_not_run_default_excluded_flag_included():
    """not_run 的 scripts 步：默认只进 needs_main_agent（交主代理决策）；
    显式 include_not_run=True 才幂等重跑补产出。"""
    with _sandbox() as root:
        target = root / "_数据库" / "nr.json"
        plan_id = _write_plan(root, [
            _step(1, "未跑步", pt.STATUS_PENDING, outputs=["_数据库/nr.json"],
                  scripts=["python {project_root}/gen_nr.py"]),
        ])
        stub = _StubRunner(effect=lambda toks: target.write_text("{}", encoding="utf-8"))
        with _patched_runner(stub):
            rep = sm.auto_heal(plan_id)
            assert stub.calls == [] and rep["healed_steps"] == []
            assert [(x["n"], x["reason"]) for x in rep["needs_main_agent"]] == [(1, "not_run")]
            rep2 = sm.auto_heal(plan_id, include_not_run=True)
        assert len(stub.calls) == 1
        assert rep2["healed_steps"] == [1] and rep2["needs_main_agent"] == []
        assert target.exists()


# ============ 8) heal 结论信产出复查·不信脚本 exit ============
def test_auto_heal_reverify_outputs_not_script_exit():
    """stub 返回 ok=True 但没真产出文件 → 步必须落 still_missing（heal 结论
    以产出复查为准·不信 exit code——Monitor 纪律同源）。"""
    with _sandbox() as root:
        plan_id = _write_plan(root, [
            _step(1, "假完成步", pt.STATUS_COMPLETED, outputs=["_数据库/ghost.json"],
                  scripts=["python {project_root}/noop.py"]),
        ])
        stub = _StubRunner()   # ok=True 但无 effect → 产出仍缺
        with _patched_runner(stub):
            rep = sm.auto_heal(plan_id)
        assert len(stub.calls) == 1 and rep["ran"][0]["ok"] is True
        assert rep["healed_steps"] == [] and rep["still_missing_steps"] == [1], \
            "脚本 exit 0 不等于补全成功·必须复查 expected_outputs"


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
