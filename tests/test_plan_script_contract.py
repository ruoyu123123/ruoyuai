#!/usr/bin/env python3
"""plan 模板 ↔ 脚本契约测试（P2 工程债批次1 · 防幽灵引用）

缺漏报告结论：plan 模板 scripts[] 引用的 core/scripts/*.py 一旦改名/删除，
orchestrator 跑到该步才报错（git_snapshot 曾悬空引用 18 天没人发现）。
本测试静态遍历 core/claude-home/plans/*.json 全部脚本行：
  剥条件脚本前缀 / 「python 」解释器前缀 / adaptive_runner 包装
  （取 -- 后的真实目标命令）→ 提取 core/scripts/xxx.py 路径 → 断言文件存在。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]。
"""
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PLANS_DIR = _ROOT / "core" / "claude-home" / "plans"

_PY_PREFIXES = ("python ", "python3 ", "py ")
# 脚本行内出现的 core/scripts/*.py 引用（兜底全行扫·防嵌套漏抓）
_SCRIPT_REF_RE = re.compile(r"core[/\\]scripts[/\\][A-Za-z0-9_]+\.py")


def _strip_prefixes(line: str) -> str:
    """剥条件脚本前缀 + 「python/python3/py 」解释器前缀。"""
    line = line.strip()
    if line.startswith("? "):
        line = line[2:].strip()
    for p in _PY_PREFIXES:
        if line.startswith(p):
            line = line[len(p):].strip()
            break
    return line


def extract_script_paths(raw_line: str) -> list:
    """从 plan 的一行脚本命令提取所有被引用的 core/scripts/*.py 路径（统一正斜杠）。

    - 空行 / "#" 注释行 → []
    - adaptive_runner 包装：wrapper 本身 + " -- " 后的真实目标命令递归剥
    """
    line = (raw_line or "").strip()
    if not line or line.startswith("#"):
        return []
    line = _strip_prefixes(line)
    if not line:
        return []
    first = line.split()[0].replace("\\", "/")
    paths = []
    if first.endswith("adaptive_runner.py") and " -- " in line:
        wrapper, target = line.split(" -- ", 1)
        paths.append(wrapper.split()[0].replace("\\", "/"))
        paths.extend(extract_script_paths(target))
        return paths
    if first.startswith("core/scripts/") and first.endswith(".py"):
        paths.append(first)
    return paths


def iter_script_lines(plan: dict):
    """遍历一个 plan 模板里所有脚本命令行（scripts / after_pause_scripts /
    control_flow.round_loop.between_rounds_scripts）。yield (step_n, line)。"""
    for step in plan.get("steps", []):
        n = step.get("n")
        for key in ("scripts", "after_pause_scripts"):
            for line in (step.get(key) or []):
                yield n, line
        cf = step.get("control_flow") or {}
        rl = cf.get("round_loop") if isinstance(cf, dict) else None
        for line in ((rl or {}).get("between_rounds_scripts") or []):
            yield n, line


def _load_plans() -> dict:
    """{文件名: plan dict}。JSON 损坏直接抛——模板坏了也是契约破损。"""
    plans = {}
    for p in sorted(PLANS_DIR.glob("*.plan.json")):
        plans[p.name] = json.loads(p.read_text(encoding="utf-8"))
    return plans


# ============ 单元：提取器本身 ============
def test_extractor_plain_python_line():
    got = extract_script_paths(
        "python core/scripts/save_state.py {project_root} --apply-cluster-changes {key}")
    assert got == ["core/scripts/save_state.py"], got


def test_extractor_strips_conditional_prefix():
    got = extract_script_paths("? python core/scripts/style_injector.py {project_root} <cluster_start_ch>")
    assert got == ["core/scripts/style_injector.py"], got


def test_extractor_unwraps_adaptive_runner():
    """adaptive_runner 包装：wrapper + -- 后真实目标都要查（任一悬空都是幽灵引用）。"""
    got = extract_script_paths(
        "python core/scripts/adaptive_runner.py --label skill_evolver_evolve -- "
        "python core/scripts/skill_evolver.py {project_root} evolve --cluster {key}")
    assert got == ["core/scripts/adaptive_runner.py",
                   "core/scripts/skill_evolver.py"], got


def test_extractor_skips_comment_and_blank():
    assert extract_script_paths("# 注释行（spawn 描述走 must_spawn_agent）") == []
    assert extract_script_paths("") == []
    assert extract_script_paths("   ") == []


# ============ 契约：全部 plan 模板引用的脚本必须真实存在 ============
def test_all_plan_referenced_scripts_exist():
    """遍历 plans/*.json 全部脚本行 → 提取 core/scripts/xxx.py → 断言 Path 存在。
    防幽灵引用（git_snapshot 曾悬空 18 天）。"""
    plans = _load_plans()
    assert plans, f"plans 目录为空？{PLANS_DIR}"
    missing = []
    for fname, plan in plans.items():
        for n, line in iter_script_lines(plan):
            for rel in extract_script_paths(line):
                if not (_ROOT / rel).exists():
                    missing.append(f"{fname} step {n}: {rel}（行: {line[:80]}）")
    assert not missing, "plan 引用了不存在的脚本（幽灵引用）:\n  " + "\n  ".join(missing)


def test_regex_fullline_scan_no_hidden_ghost():
    """兜底：脚本行**任意位置**出现的 core/scripts/*.py（含嵌套/参数位）也必须存在。
    只扫脚本行不扫 description（描述里提到的脚本名是文档非契约）。"""
    missing = []
    for fname, plan in _load_plans().items():
        for n, line in iter_script_lines(plan):
            if line.strip().startswith("#"):
                continue
            for m in _SCRIPT_REF_RE.findall(line):
                rel = m.replace("\\", "/")
                if not (_ROOT / rel).exists():
                    missing.append(f"{fname} step {n}: {rel}")
    assert not missing, "脚本行内嵌引用了不存在的脚本:\n  " + "\n  ".join(missing)


def test_extraction_not_vacuous():
    """敌对自检：提取器若静默失效（提取到 0 条引用）→ 上面契约测试空转假绿。
    当前 6 个模板实际引用 ≥20 条脚本，锁阈值防空转。"""
    plans = _load_plans()
    assert len(plans) >= 6, f"plan 模板数异常: {list(plans)}"
    total = sum(len(extract_script_paths(line))
                for plan in plans.values()
                for _, line in iter_script_lines(plan))
    assert total >= 20, f"提取到的脚本引用过少（{total} 条）——提取器疑似失效"


def test_cluster_main_chain_has_no_observer_sidecars():
    """创作主链不得挂离线观察/学习建议 sidecar。"""
    # W4 死码清扫（2026-07-05）：audit_dashboard / causal_verifier /
    # skill_rewrite_advisor / user_edit_learner 已物理删除（生产面零引用），
    # 条目随之清空；未来出现新的离线观察/学习 sidecar 时在此登记。
    forbidden: set[str] = set()
    offenders = []
    for fname in ("cluster-write.plan.json", "cluster-save-state.plan.json"):
        plan = _load_plans()[fname]
        for n, line in iter_script_lines(plan):
            for rel in extract_script_paths(line):
                if Path(rel).name in forbidden:
                    offenders.append(f"{fname} step {n}: {rel}")
    assert not offenders, "主链仍挂离线 sidecar:\n  " + "\n  ".join(offenders)


def test_main_chain_required_steps_have_verifiable_outputs():
    """创作主链 required steps 不允许空产物假完成。"""
    offenders = []
    for fname in ("outline.plan.json", "cluster-write.plan.json", "cluster-save-state.plan.json"):
        plan = _load_plans()[fname]
        for step in plan.get("steps", []):
            if step.get("required") and not (step.get("expected_outputs") or []):
                offenders.append(f"{fname} step {step.get('n')} {step.get('name')}")
            if step.get("required") and step.get("skip_output_allowed") is True:
                offenders.append(f"{fname} step {step.get('n')} {step.get('name')} skip_output_allowed=true")
    assert not offenders, "required 主链步骤缺少可验证产物或允许跳过输出:\n  " + "\n  ".join(offenders)


def test_main_chain_adaptive_runner_is_strict():
    """主链 adaptive_runner 只负责记录 incident，不允许作为降级放行 wrapper。"""
    offenders = []
    for fname in ("cluster-write.plan.json", "cluster-save-state.plan.json"):
        plan = _load_plans()[fname]
        for n, line in iter_script_lines(plan):
            if "core/scripts/adaptive_runner.py" in line and " --strict " not in f" {line} ":
                offenders.append(f"{fname} step {n}: {line}")
    assert not offenders, "主链 adaptive_runner 缺 --strict:\n  " + "\n  ".join(offenders)


def test_main_chain_templates_have_no_encoding_damage():
    """锁住 PowerShell 管道写中文造成的 ??? / _??? 路径损坏。"""
    offenders = []
    for fname in ("cluster-write.plan.json", "cluster-save-state.plan.json"):
        text = (PLANS_DIR / fname).read_text(encoding="utf-8")
        for bad in ("???", "_???"):
            if bad in text:
                offenders.append(f"{fname}: contains {bad}")
    assert not offenders, "主链 plan 存在编码损坏标记:\n  " + "\n  ".join(offenders)


def test_main_chain_command_docs_do_not_use_skip_output():
    """主链命令文档不得指示 plan_tracker --skip-output。"""
    offenders = []
    for rel in (
        ".claude/commands/write.md",
        ".claude/commands/outline.md",
        ".claude/commands/cluster-write.md",
        ".claude/commands/cluster-save-state.md",
        ".claude/commands/export.md",
        ".claude/commands/continue.md",
    ):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        if "--skip-output" in text or "skip_output_allowed" in text:
            offenders.append(rel)
    assert not offenders, "主链命令文档仍包含 skip-output 口径:\n  " + "\n  ".join(offenders)


def test_hooks_do_not_expose_subsystems_bypass():
    """主链 hook 不得保留 .subsystems_bypass.json 或轻量旁路放行口径。"""
    offenders = []
    for rel in (
        "core/scripts/plan_step_gates.py",
        "core/claude-home/hooks/pretooluse_subsystems_gate.py",
        "core/claude-home/hooks/pretooluse_plan_step_anti_skip.py",
        "core/claude-home/hooks/pretooluse_step_research.py",
        "core/scripts/scaffold_subsystems.py",
    ):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        for bad in (".subsystems_bypass.json", "轻量模式旁路", "旁路: touch", "bypass_active"):
            if bad in text:
                offenders.append(f"{rel}: {bad}")
    assert not offenders, "主链门禁仍暴露旁路口径:\n  " + "\n  ".join(offenders)


def test_retired_side_commands_are_not_registered():
    """章级质检/历史修正文入口已退役，不得继续注册为 slash command 或 plan 命令。"""
    check_quality = "check-" + "quality"
    retired_reconcile = "recon" + "cile"
    offenders = []
    for rel in (
        f".claude/commands/{check_quality}.md",
        f".claude/commands/{retired_reconcile}.md",
        f"core/claude-home/plans/{check_quality}.plan.json",
        f"core/claude-home/plans/{retired_reconcile}.plan.json",
    ):
        if (_ROOT / rel).exists():
            offenders.append(rel)
    assert not offenders, "退役命令/plan 仍存在:\n  " + "\n  ".join(offenders)


def test_user_docs_do_not_recommend_retired_or_direct_db_write_flows():
    """用户可见文档不得推荐退役命令或直接写库修改流程。"""
    docs = [
        "README.md",
        "CLAUDE.md",
        "使用说明.md",
        "workspace/novels/README.md",
        ".claude/commands/README.md",
        ".claude/commands/QUICK_REFERENCE.md",
        ".claude/commands/db.md",
        ".claude/commands/session-start.md",
    ]
    forbidden = (
        "/" + "recon" + "cile",
        "/" + "check-" + "quality",
        "/write-chapter",
        "`/save-state`",
        "/db update",
        "/db 修复",
        "/db 重建",
        "手动微调",
        "手动改了任何一个 `_数据库/*.json`",
    )
    offenders = []
    for rel in docs:
        path = _ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for bad in forbidden:
            if bad in text:
                offenders.append(f"{rel}: {bad}")
    assert not offenders, "用户文档仍推荐退役或直接写库流程:\n  " + "\n  ".join(offenders)


def test_export_contract_has_no_format_or_flush_ghosts():
    """正式 /export 只支持 TXT 全书导出；不得保留 flush/range/Markdown/EPUB 已实现口径。"""
    text = (_ROOT / ".claude/commands/export.md").read_text(encoding="utf-8")
    forbidden = ("--flush", "append/split", "Markdown 导出已实现", "EPUB 导出已实现", "范围导出已实现")
    hits = [s for s in forbidden if s in text]
    assert not hits, f"/export 文档仍包含旧导出口径: {hits}"


def test_write_command_never_writes_fulltext_directly():
    """/write 不得直接写 全文.txt；成品只从 /export 产生。"""
    text = (_ROOT / ".claude/commands/write.md").read_text(encoding="utf-8")
    assert "Write `全文.txt`" not in text
    assert "直接 Write" not in text


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
