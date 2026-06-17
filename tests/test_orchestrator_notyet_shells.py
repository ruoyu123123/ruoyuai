#!/usr/bin/env python3
"""NOT-YET 空壳命令拒绝回归锁（2026-06-17 · /loop 自主硬化）。

check-quality / reconcile 的 plan 模板当前是「零 scripts/零 agent/零 pause/零 touch」
空壳（创作步骤尚未落成 gen-model 脚本，见 PROGRAM_DRIVEN.md 迁移状态表）。
orchestrator.run_command 必须**拒绝假成功执行**它们（`_step_is_shell` 全空 → 抛
OrchestratorError），而不是机械走步报「N/N 完成」。

本测试是**特征化回归锁（characterization test）**：
- 锁住「当前 NOT-YET」这一事实——任何人误以为已迁移而机械接 orchestrator 会被它抓住。
- 一旦 check-quality/reconcile 真被程序驱动化（步骤补上 scripts/must_spawn_agent），
  这两条断言会**翻红**，提示来更新本锁 + 补真正的 fake-LLM e2e（见
  workspace/_temp_research/mock_llm_cli_e2e_harness_plan.md §1.5 / §5 P4）。

拒绝发生在 create_plan 之前（orchestrator.py:626-633），**零副作用**（不建 plan、
不碰项目目录、不调任何 LLM），因此无需沙盒、无 API 风险。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402


def _assert_notyet_rejected(command):
    try:
        orc.run_command(command, "任意未建项目", key="001")
    except orc.OrchestratorError as e:
        msg = str(e)
        assert "NOT-YET" in msg, f"{command} 拒绝信息应含 NOT-YET 标记: {msg}"
        return
    except Exception as e:  # 任何别的异常都说明拒绝路径没生效
        raise AssertionError(
            f"{command} 应抛 OrchestratorError(NOT-YET)，却抛 "
            f"{type(e).__name__}: {e}")
    raise AssertionError(f"{command} 是 NOT-YET 空壳，run_command 却未拒绝（假成功风险）")


def test_check_quality_is_rejected_as_notyet_shell():
    _assert_notyet_rejected("check-quality")


def test_reconcile_is_rejected_as_notyet_shell():
    _assert_notyet_rejected("reconcile")


def test_notyet_templates_really_are_all_shell():
    """正控：确认这两个模板**确实**全 step 是空壳（拒绝有据，非误伤）。
    若哪天某步补了 scripts/agent，本断言翻红 = 迁移已发生 = 该更新回归锁。"""
    for command in ("check-quality", "reconcile"):
        tpl = pt.load_template(command)
        assert tpl, f"{command} 模板应能加载"
        steps = tpl.get("steps", [])
        assert steps, f"{command} 模板应有 steps"
        assert all(orc._step_is_shell(s) for s in steps), (
            f"{command} 已有非空壳 step（迁移发生了）——更新本回归锁 + 补 fake-LLM e2e")


def test_real_writing_command_is_not_shell():
    """负控：写作主轨 cluster-write 的 step 必须**不是**空壳——证明 `_step_is_shell`
    + NOT-YET 拒绝是针对性的，不会误伤已程序驱动化的命令。"""
    tpl = pt.load_template("cluster-write")
    assert tpl, "cluster-write 模板应能加载"
    steps = tpl.get("steps", [])
    assert steps, "cluster-write 应有 steps"
    non_shell = [s for s in steps if not orc._step_is_shell(s)]
    assert non_shell, "cluster-write 应至少有一个带 scripts/agent 的实体 step"
    # 至少 build_manifest / gen_writer 这类实体步在
    assert len(non_shell) >= 3, (
        f"cluster-write 实体 step 数异常偏少（{len(non_shell)}）——模板可能被改坏")


if __name__ == "__main__":
    for _n in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[_n]()
        print("OK", _n)
