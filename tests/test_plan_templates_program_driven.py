#!/usr/bin/env python3
"""plan 模板 ⇄ orchestrator 能力对齐回归（程序驱动 M3 · v28 结构化字段）。

锁住对抗审查识别的「占位符卡死」风险：真实模板里每个角括号占位符必须有解析来源
（data_flow 声明 / driver prime 预算 / round 循环 / 用户答案）——缺源 = 上线后 driver
在该步必卡死。模板一改这里立刻红。
"""
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import judge_runner as jr  # noqa: E402

PLANS = _ROOT / "core" / "claude-home" / "plans"

# driver 运行时保证可解析的占位符（来源非 data_flow）
PRIMED = {"<cluster_id>", "<cluster_num>", "<cluster_start_ch>", "<prev_key>",
          "<prev_pending_tail>", "<round>"}

ANGLE = re.compile(r"<[a-z][a-z0-9_]*>")


def _load(command):
    return json.loads((PLANS / f"{command}.plan.json").read_text(encoding="utf-8"))


def _angle_placeholders_in(step) -> set:
    found = set()
    for line in (step.get("scripts") or []) + (step.get("after_pause_scripts") or []):
        if line.strip().startswith("#"):
            continue
        found |= set(ANGLE.findall(line))
    for v in (step.get("agent_input") or {}).values():
        found |= set(ANGLE.findall(str(v)))
    return found


def test_cluster_write_placeholders_all_sourced():
    plan = _load("cluster-write")
    for step in plan["steps"]:
        declared = set((step.get("data_flow") or {}).keys())
        missing = _angle_placeholders_in(step) - declared - PRIMED
        assert not missing, f"step {step['n']} 占位符无解析来源: {missing}"


def test_cluster_save_state_placeholders_all_sourced():
    plan = _load("cluster-save-state")
    for step in plan["steps"]:
        declared = set((step.get("data_flow") or {}).keys())
        missing = _angle_placeholders_in(step) - declared - PRIMED
        assert not missing, f"step {step['n']} 占位符无解析来源: {missing}"


def test_judge_steps_have_agent_input_and_report_path():
    """判断 agent step（非 script executor）必须声明 agent_input + judge_report_path
    ——spawn 参数从 .md 散文上移的核心契约。"""
    for cmd in ("cluster-write", "cluster-save-state"):
        plan = _load(cmd)
        for step in plan["steps"]:
            agents = step.get("must_spawn_agent")
            if not agents or step.get("agent_executor") == "script":
                continue
            if isinstance(agents, str):
                agents = [agents]
            assert step.get("agent_input"), \
                f"{cmd} step {step['n']} 判断 agent 缺 agent_input"
            jrp = step.get("judge_report_path")
            assert jrp, f"{cmd} step {step['n']} 缺 judge_report_path"
            if isinstance(jrp, dict):
                for a in agents:
                    assert a in jrp, f"{cmd} step {step['n']} dict 缺 agent {a}"


def test_judge_step_agents_all_registered():
    """模板里派发到 judge_runner 的 agent 必须在 AGENT_SPECS 注册表里。"""
    for cmd in ("cluster-write", "cluster-save-state"):
        plan = _load(cmd)
        for step in plan["steps"]:
            agents = step.get("must_spawn_agent")
            if not agents or step.get("agent_executor") == "script":
                continue
            if isinstance(agents, str):
                agents = [agents]
            for a in agents:
                assert a in jr.AGENT_SPECS, \
                    f"{cmd} step {step['n']} agent {a} 未注册 judge_runner.AGENT_SPECS"


def test_creative_wrappers_marked_script_executor():
    """novel-writer / novel-chapter-splitter 是创意 wrapper 非 judge（北极星④）——
    必须标 agent_executor=script 且带可执行 scripts。"""
    plan = _load("cluster-write")
    by_agent = {}
    for step in plan["steps"]:
        a = step.get("must_spawn_agent")
        if isinstance(a, str):
            by_agent[a] = step
    for wrapper in ("novel-writer", "novel-chapter-splitter"):
        step = by_agent[wrapper]
        assert step.get("agent_executor") == "script", wrapper
        real = [l for l in step.get("scripts", []) if not l.strip().startswith("#")]
        assert real, f"{wrapper} step 无可执行脚本"


def test_pause_point_is_explicit_choice_with_apply():
    """save-state step 11 走向卡停顿点：声明式 choice + 选择写回脚本
    （北极星③：默认弹卡等用户·auto_pilot 是显式开关非隐式默认）。"""
    plan = _load("cluster-save-state")
    step11 = next(s for s in plan["steps"] if s["n"] == 11)
    p = step11.get("pause_for_user")
    assert p and p["type"] == "choice" and p.get("options_field") == "candidates"
    assert p.get("answer_artifact")
    apply_lines = [l for l in step11.get("after_pause_scripts", [])
                   if "cluster_choice_apply" in l]
    assert apply_lines, "缺 cluster_choice_apply 写回脚本"


def test_round_loop_is_control_flow_only():
    """control_flow 只表达控制流（轮数/pass 条件），不编码创作决策（北极星⑤）：
    round_loop 字段集是封闭白名单。"""
    plan = _load("cluster-write")
    step3 = next(s for s in plan["steps"] if s["n"] == 3)
    loop = step3["control_flow"]["round_loop"]
    allowed = {"agent", "max_rounds", "consecutive_clean", "pass_field",
               "pass_value", "between_rounds_scripts"}
    assert set(loop.keys()) <= allowed, f"round_loop 出现控制流之外的字段: {set(loop) - allowed}"
    assert loop["consecutive_clean"] == 3 and loop["max_rounds"] == 5


def test_audit_exit_codes_match_semantics():
    plan = _load("cluster-write")
    step3 = next(s for s in plan["steps"] if s["n"] == 3)
    ec = step3["control_flow"]["exit_codes"]
    assert ec["0"] == "ok" and ec["1"] == "ok"          # pass / auto_fixed
    assert ec["2"].startswith("dispatch:")               # needs_agent → 派单
    assert ec["3"] == "fail"                             # fatal


def test_all_six_templates_still_valid_json():
    for f in PLANS.glob("*.plan.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(d.get("steps"), list) and d["steps"], f.name


def test_all_plan_script_refs_exist():
    """🔴 plan↔脚本幽灵引用契约（全 6 plan·原占位符测试只覆盖 cluster-write/save-state）：
    每个 plan template 的 scripts/after_pause_scripts 行引用的 `core/scripts/X.py` 必须真实
    存在。防 plan 改动(脚本改名/删除但 plan 没跟)引入幽灵引用 → orchestrator 跑到该步炸
    FileNotFoundError(同 resolve/BOM 一类 plan-脚本集成面 bug)。含 `?` advisory 前缀脚本。
    2026-06-15 实测当前 41 引用 0 缺失·此测试锁住防未来漂移(project_audit_hardening P2 盲区)。"""
    script_ref = re.compile(r"(core/scripts/[A-Za-z0-9_]+\.py)")
    missing = []
    for f in PLANS.glob("*.plan.json"):
        plan = json.loads(f.read_text(encoding="utf-8"))
        for step in plan.get("steps", []):
            for line in (step.get("scripts") or []) + (step.get("after_pause_scripts") or []):
                if line.strip().startswith("#"):
                    continue
                for m in script_ref.findall(line):   # 正则不管 `? ` advisory 前缀·照查
                    if not (_ROOT / m).exists():
                        missing.append(f"{f.name} step {step.get('n')}: {m}")
    assert not missing, f"plan 引用了不存在的脚本(幽灵引用): {missing}"


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
