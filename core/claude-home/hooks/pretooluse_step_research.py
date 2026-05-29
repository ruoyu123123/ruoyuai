#!/usr/bin/env python3
"""PreToolUse Hook: 强制「先调研再干步骤」

触发条件：Bash 工具调用，命令含 `plan_tracker.py step ... --n N`

逻辑：
1. 解析 plan_id + step n
2. 读 plan JSON 找 step.research_ref 字段（指向 .research_cache 文件路径）
3. 若有 research_ref：
   - 文件存在 → exit 0 放行
   - 文件不存在 → exit 2 拦截 + 提示「请先 spawn novel-researcher 写 <path>」
4. 若 plan JSON 没 research_ref 字段（旧 plan）→ exit 0 放行（向后兼容）
5. plan_tracker 不可用 / plan 找不到 → exit 0 放行（不破坏）

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只检查含 `plan_tracker.py step` 的 Bash 命令，其他 Bash 直接放行
- 任何异常 → 放行（防御性，不能因为 hook 自身 bug 破坏主流程）
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
    if "plan_tracker" not in command or "step" not in command:
        sys.exit(0)

    # 2026-05-29 修【安全·绕过】：解耦提取 plan_id / --n，不再要求 plan_id 紧跟 step
    # 再紧跟 --n（否则把旗标插在中间就能让位置耦合正则 miss 而绕过）。
    if not re.search(r"plan_tracker\.py\s+step\b", command):
        sys.exit(0)
    pid_m = re.search(r"\bstep\s+(?:-\S+\s+)*[\"']?([A-Za-z0-9_][\w\-]*)[\"']?", command)
    n_m = re.search(r"--n\s+(\d+)", command)
    if not pid_m or not n_m:
        sys.exit(0)
    plan_id, n = pid_m.group(1), int(n_m.group(1))

    # 找 plan JSON（对齐 plan_tracker.runtime_plans_dir 的实际路径）
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    candidates = [
        project_dir / "core" / "claude-home" / ".plans" / f"{plan_id}.json",  # GLOBAL_PLANS_DIR
        project_dir / "_数据库" / ".plans" / f"{plan_id}.json",                # 小说项目
        project_dir / ".plans" / f"{plan_id}.json",                            # 风格库
        # 兼容 workspace 多项目
        *list(project_dir.glob(f"workspace/**/_数据库/.plans/{plan_id}.json")),
        *list(project_dir.glob(f"workspace/**/.plans/{plan_id}.json")),
    ]
    plan_file = next((c for c in candidates if c.exists()), None)
    if not plan_file:
        # plan 找不到 → 放行（plan_tracker 自己会报错）
        sys.exit(0)

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    # 找 step n 的 research_ref
    step_info = None
    for s in plan.get("steps", []):
        if s.get("n") == n:
            step_info = s
            break
    if not step_info:
        sys.exit(0)

    research_ref = step_info.get("research_ref")
    if not research_ref:
        # 旧 plan 无 research_ref → 放行（向后兼容）
        sys.exit(0)

    # research_ref 可能是 str（单文件）或 list（多文件）
    refs = [research_ref] if isinstance(research_ref, str) else research_ref
    if not isinstance(refs, list):
        sys.exit(0)

    missing = []
    for ref in refs:
        if not isinstance(ref, str):
            continue
        # ref 可以是绝对路径或相对项目根
        p = Path(ref)
        if not p.is_absolute():
            p = project_dir / ref
        if not p.exists():
            missing.append(ref)

    if missing:
        msg = (
            f"❌ [hook step-research] plan_id={plan_id} step n={n} 的 research_ref 文件不存在：\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\n请先 spawn novel-researcher 写这些 research_cache 文件（按 CLAUDE.md「没调查没发言权」原则）。\n"
            + "豁免方式：从 plan 模板移除该 step 的 research_ref 字段（不推荐）。"
        )
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 防御性：任何异常都放行
        print(f"[hook step-research] internal error (放行): {e}", file=sys.stderr)
        sys.exit(0)
