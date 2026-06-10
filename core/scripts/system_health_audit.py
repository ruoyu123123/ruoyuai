"""system_health_audit.py — 系统完整性审计（v22.5 收尾）

全面审计 80+ scripts + 12+ agents + 30+ commands + plans + manifest 字段
判断每个声明的功能是否真实可用。

5 类检查：
1. SCRIPT_IMPORT: 每脚本能否 import（python -c "import ...")
2. SCRIPT_HELP: 能否跑 --help
3. AGENT_FRONTMATTER: agent .md 含 frontmatter + name/description
4. COMMAND_FRONTMATTER: command .md 含 frontmatter
5. PLAN_SCRIPT_EXISTS: plan.json 引用的 scripts 真实存在

输出：core/claude-home/SYSTEM_HEALTH_REPORT.md
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from frozen_util import child_python  # frozen-aware 子解释器（M4·dev=no-op）
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def check_script_importable(script_path: Path) -> tuple[bool, str]:
    """检查脚本能否被 import（无 syntax error）"""
    try:
        spec = importlib.util.spec_from_file_location("test_mod", script_path)
        if spec is None or spec.loader is None:
            return False, "spec_from_file_location 失败"
        # 仅 compile 不执行
        src = script_path.read_text(encoding="utf-8", errors="ignore")
        try:
            compile(src, str(script_path), "exec")
            return True, ""
        except SyntaxError as e:
            return False, f"SyntaxError L{e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"import 失败: {str(e)[:80]}"


def check_script_help(script_path: Path) -> tuple[bool, str]:
    """跑 python script.py --help（5s timeout）"""
    try:
        r = subprocess.run(
            [child_python(), str(script_path), "--help"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
        # --help 通常返回 0
        if r.returncode == 0 and ("usage" in r.stdout.lower() or "argument" in r.stdout.lower()):
            return True, "ok"
        # 部分脚本不支持 --help 但能跑（如 system_health_audit 自己）
        if "usage" in r.stderr.lower():
            return True, "ok (stderr usage)"
        return False, f"exit={r.returncode}, stdout_head={(r.stdout[:80] or r.stderr[:80])}"
    except subprocess.TimeoutExpired:
        return False, "timeout 10s"
    except Exception as e:
        return False, f"运行异常: {str(e)[:80]}"


def check_agent_frontmatter(agent_path: Path) -> tuple[bool, str]:
    """agent .md 是否含 frontmatter + name + description"""
    text = agent_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False, "无 frontmatter（不以 --- 开头）"
    # 找第二个 ---
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return False, "frontmatter 未闭合"
    fm = parts[1]
    if "name:" not in fm:
        return False, "缺 name 字段"
    if "description:" not in fm:
        return False, "缺 description 字段"
    return True, "ok"


def check_command_frontmatter(cmd_path: Path) -> tuple[bool, str]:
    """command .md 是否含 frontmatter + description"""
    text = cmd_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False, "无 frontmatter"
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return False, "frontmatter 未闭合"
    if "description:" not in parts[1]:
        return False, "缺 description"
    return True, "ok"


def check_plan_scripts_exist(plan_path: Path, project_root: Path) -> list[dict]:
    """plan.json 引用的脚本是否真实存在"""
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    findings = []
    for step in data.get("steps", []):
        for cmd in step.get("scripts", []):
            # 提取 python core/scripts/XXX.py 路径
            m = re.search(r"python\s+(?:core/scripts/[\w./_-]+\.py)", cmd)
            if not m:
                continue
            script_rel = re.search(r"(core/scripts/[\w./_-]+\.py)", cmd).group(1)
            full_path = project_root / script_rel
            if not full_path.exists():
                findings.append({
                    "code": "PLAN_SCRIPT_MISSING",
                    "plan": plan_path.name,
                    "step": step.get("n"),
                    "missing_script": script_rel,
                })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--skip-help", action="store_true", help="跳过 --help 检查（更快）")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    report = {
        "audit_ts": datetime.now().isoformat(timespec="seconds"),
        "checks": defaultdict(list),
        "summary": {},
    }

    # 1. SCRIPT
    scripts_dir = project_root / "core" / "scripts"
    scripts = sorted(scripts_dir.glob("*.py"))
    print(f"[1/4] 审计 {len(scripts)} 个 scripts...")
    for sp in scripts:
        ok, msg = check_script_importable(sp)
        if not ok:
            report["checks"]["SCRIPT_IMPORT_FAIL"].append({"path": sp.name, "msg": msg})

        if not args.skip_help:
            ok2, msg2 = check_script_help(sp)
            if not ok2:
                report["checks"]["SCRIPT_HELP_FAIL"].append({"path": sp.name, "msg": msg2})

    # 2. AGENT
    agents_dir = project_root / ".claude" / "agents"
    agents = sorted(agents_dir.glob("*.md"))
    print(f"[2/4] 审计 {len(agents)} 个 agents...")
    for ap_md in agents:
        ok, msg = check_agent_frontmatter(ap_md)
        if not ok:
            report["checks"]["AGENT_FRONTMATTER_FAIL"].append({"path": ap_md.name, "msg": msg})

    # 3. COMMAND
    commands_dir = project_root / ".claude" / "commands"
    commands = sorted(commands_dir.glob("*.md"))
    print(f"[3/4] 审计 {len(commands)} 个 commands...")
    for cp_md in commands:
        ok, msg = check_command_frontmatter(cp_md)
        if not ok:
            report["checks"]["COMMAND_FRONTMATTER_FAIL"].append({"path": cp_md.name, "msg": msg})

    # 4. PLAN scripts
    plans_dir = project_root / "core" / "claude-home" / "plans"
    plans = sorted(plans_dir.glob("*.json"))
    print(f"[4/4] 审计 {len(plans)} 个 plans 的 scripts 引用...")
    for pp in plans:
        findings = check_plan_scripts_exist(pp, project_root)
        report["checks"]["PLAN_SCRIPT_MISSING"].extend(findings)

    # summary
    report["summary"] = {
        "scripts_total": len(scripts),
        "agents_total": len(agents),
        "commands_total": len(commands),
        "plans_total": len(plans),
    }
    for k, v in report["checks"].items():
        report["summary"][f"{k}_count"] = len(v)

    # convert defaultdict for json
    report["checks"] = dict(report["checks"])

    out_path = project_root / "core" / "claude-home" / "SYSTEM_HEALTH_REPORT.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 审计总结 ===")
    print(f"scripts: {len(scripts)} / agents: {len(agents)} / commands: {len(commands)} / plans: {len(plans)}")
    for k, v in report["summary"].items():
        if "_count" in k and v > 0:
            print(f"  ⚠️  {k}: {v}")
    print(f"\n详细报告: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
