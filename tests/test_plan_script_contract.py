#!/usr/bin/env python3
"""plan 模板 ↔ 脚本契约测试（P2 工程债批次1 · 防幽灵引用）

缺漏报告结论：plan 模板 scripts[] 引用的 core/scripts/*.py 一旦改名/删除，
orchestrator 跑到该步才报错（git_snapshot 曾悬空引用 18 天没人发现）。
本测试静态遍历 core/claude-home/plans/*.json 全部脚本行：
  剥「? 」advisory 前缀 / 「python 」解释器前缀 / adaptive_runner 包装
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
    """剥「? 」advisory 前缀 + 「python/python3/py 」解释器前缀。"""
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


def test_extractor_strips_advisory_prefix():
    got = extract_script_paths("? python core/scripts/audit_dashboard.py {project_root}")
    assert got == ["core/scripts/audit_dashboard.py"], got


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
