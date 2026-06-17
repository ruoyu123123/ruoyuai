#!/usr/bin/env python3
"""PreToolUse Hook: 禁跳步门禁（v24 用户硬规则 2026-05-25）

触发条件：Bash 工具调用，命令含 `plan_tracker.py step ... --skip-output`

逻辑：
1. 解析 plan_id + step n
2. 读 plan JSON 找 step.expected_outputs 字段
3. 如果 expected_outputs 非空 且 step.skip_output_allowed != true → exit 2 拦截
4. 否则放行（场景合法）

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只钩 plan_tracker step + --skip-output 同时出现
- 任何异常 → 放行（防御性）
- 用户「轻量模式」旁路：`_数据库/.subsystems_bypass.json` 存在即放行
"""
import json
import os
import re
import sys
from pathlib import Path


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")

    # 只钩 plan_tracker step + --skip-output 同时出现
    if "plan_tracker" not in command or "step" not in command or "--skip-output" not in command:
        sys.exit(0)

    # 2026-05-29 修【安全·绕过】：原正则要求 plan_id 紧跟 step 再紧跟 --n，
    # 把 --skip-output 写在 --n 前（或 plan_id 前）就会让位置耦合正则 miss 而放行。
    # 改为解耦提取：先确认是 plan_tracker.py step 子命令，再独立提取 plan_id / --n。
    if not re.search(r"plan_tracker\.py\s+step\b", command):
        sys.exit(0)
    # plan_id：step 后第一个非 flag token（不以 - 开头），跳过 --skip-output 等任意顺序的旗标
    pid_m = re.search(r"\bstep\s+(?:-\S+\s+)*[\"']?([A-Za-z0-9_][\w\-]*)[\"']?", command)
    n_m = re.search(r"--n\s+(\d+)", command)
    if not pid_m or not n_m:
        sys.exit(0)
    plan_id, n = pid_m.group(1), int(n_m.group(1))

    # 找 plan JSON
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    candidates = [
        project_dir / "core" / "claude-home" / ".plans" / f"{plan_id}.json",
        project_dir / "_数据库" / ".plans" / f"{plan_id}.json",
        project_dir / ".plans" / f"{plan_id}.json",
        *list(project_dir.glob(f"workspace/**/_数据库/.plans/{plan_id}.json")),
        *list(project_dir.glob(f"workspace/**/.plans/{plan_id}.json")),
    ]
    plan_file = next((c for c in candidates if c.exists()), None)
    if not plan_file:
        sys.exit(0)

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    # 用户轻量模式旁路：项目 _数据库/.subsystems_bypass.json 存在即放行
    project_name = plan.get("project", "")
    if project_name:
        bypass_candidates = [
            project_dir / "workspace" / "novels" / project_name / "_数据库" / ".subsystems_bypass.json",
            project_dir / "workspace" / "styles" / project_name / "_数据库" / ".subsystems_bypass.json",
        ]
        if any(b.exists() for b in bypass_candidates):
            sys.exit(0)

    # 🔴 2026-06-17 移除 allow_skip_steps 全局旁路：无任何 plan 模板使用·是潜在 footgun
    # （单字段整盘绕过「禁止跳步」最高元规则）。合法 opt-out 走项目级 .subsystems_bypass.json。

    # 找 step n 的模板定义
    target_step = next((s for s in plan.get("steps", []) if s.get("n") == n), None)
    if not target_step:
        sys.exit(0)

    expected = target_step.get("expected_outputs", []) or []
    skip_allowed = target_step.get("skip_output_allowed", False)

    # 核心校验
    if expected and not skip_allowed:
        print(f"❌ [Hook anti_skip] plan_tracker step --skip-output 被拒绝", file=sys.stderr)
        print(f"   plan_id: {plan_id}", file=sys.stderr)
        print(f"   step: {n} ({target_step.get('name', '?')})", file=sys.stderr)
        print(f"   该 step 模板要求 expected_outputs ({len(expected)} 个文件):", file=sys.stderr)
        for f in expected[:5]:
            print(f"     - {f}", file=sys.stderr)
        if len(expected) > 5:
            print(f"     ... 及 {len(expected) - 5} 个其他文件", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"   🔴 v24 禁跳步规则（memory feedback-default-no-step-skipping-for-new-books）", file=sys.stderr)
        print(f"   解决方案：", file=sys.stderr)
        print(f"     A. 真跑 step 产生 expected_outputs 后用正常 step 命令（不带 --skip-output）", file=sys.stderr)
        print(f"     B. 项目「轻量模式」→ touch <project>/_数据库/.subsystems_bypass.json 旁路", file=sys.stderr)
        print(f"     C. plan template 该 step 改 skip_output_allowed: true（仅在场景明确无输出时合法）", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
