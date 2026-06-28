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
    """save-state 走向卡停顿点（emergence 步·2026-06-28 起为 step 13·原 11·archivist+apply-archive
    插在 step5/6 后顺延 2）：声明式 choice + 选择写回脚本
    （北极星③：默认弹卡等用户·auto_pilot 是显式开关非隐式默认）。"""
    plan = _load("cluster-save-state")
    emergence = next(s for s in plan["steps"]
                     if s.get("name") == "cluster-emergence+report-and-card")
    p = emergence.get("pause_for_user")
    assert p and p["type"] == "choice" and p.get("options_field") == "candidates"
    assert p.get("answer_artifact")
    apply_lines = [l for l in emergence.get("after_pause_scripts", [])
                   if "cluster_choice_apply" in l]
    assert apply_lines, "缺 cluster_choice_apply 写回脚本"


def test_archivist_step_is_judge_with_apply_archive_followup():
    """🔴 2026-06-28 架构纠正回归锁：cluster-save-state 必须有 archivist judge 步（产 archive.json·
    权威 factual 状态源·非 writer 自报）+ 紧随的 apply_archive 确定性回库步。守住「writer 不自报
    factual → Claude 梳理 → 确定性回库」链路不被未来重构悄悄抹掉。"""
    plan = _load("cluster-save-state")
    by_name = {s.get("name"): s for s in plan["steps"]}
    arch = by_name.get("novel-archivist-cluster")
    assert arch, "缺 novel-archivist-cluster 判断步（factual 权威源）"
    # archivist 是真 judge（非 script executor）·必在 AGENT_SPECS·产 archive.json
    assert arch.get("must_spawn_agent") == "novel-archivist"
    assert arch.get("agent_executor") != "script", "archivist 是判断 agent 非创意 wrapper"
    assert "novel-archivist" in jr.AGENT_SPECS, "novel-archivist 未注册 judge_runner.AGENT_SPECS"
    assert "archive.json" in str(arch.get("judge_report_path", "")), \
        "archivist judge_report_path 应为 archive.json"
    assert any("archive" in str(o) for o in arch.get("expected_outputs", [])), \
        "archivist expected_outputs 应含 archive.json"
    # apply-archive 紧随其后·跑 apply_archive.py 回库
    applyer = by_name.get("apply-archive-to-db")
    assert applyer, "缺 apply-archive-to-db 回库步"
    assert applyer["n"] == arch["n"] + 1, "apply-archive 必紧随 archivist（archive.json 已产才回库）"
    apply_lines = [l for l in applyer.get("scripts", []) if "apply_archive.py" in l]
    assert apply_lines, "apply-archive 步缺 apply_archive.py"
    # 🔴 2026-06-28 不降级收尾：apply_archive **不再 advisory**——archive 是 factual 回库唯一
    # 权威路径，缺出场角色=archivist 失败=错误→exit 2 硬停。去掉 '? ' 前缀 + skip_output_allowed=false。
    assert not apply_lines[0].strip().startswith("? "), \
        "apply_archive 不再 advisory（archive 缺角色 → exit 2 硬停·不降级·factual 回库唯一权威路径）"
    assert applyer.get("skip_output_allowed") is False, \
        "apply-archive skip_output_allowed 必 false（必跑·不降级）"


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


def test_all_plan_placeholders_sourced():
    """🔴 占位符来源契约扩展到全 6 plan（原 test_cluster_write/save_state 只覆盖 2 个·
    outline/distill/check-quality/reconcile 是盲区）：每个角括号占位符必须有解析来源
    （data_flow 声明 / driver PRIMED 预算）——缺源 = orchestrator 在该步卡死。
    2026-06-15 实测全 6 plan 0 处无来源·锁住防漂移(揪头发拉通全命令非只主轨)。"""
    missing = []
    for f in PLANS.glob("*.plan.json"):
        plan = json.loads(f.read_text(encoding="utf-8"))
        for step in plan.get("steps", []):
            declared = set((step.get("data_flow") or {}).keys())
            unsourced = _angle_placeholders_in(step) - declared - PRIMED
            if unsourced:
                missing.append(f"{f.name} step {step.get('n')}: {unsourced}")
    assert not missing, f"占位符无解析来源(orchestrator 会卡死): {missing}"


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
