#!/usr/bin/env python3
"""PreToolUse Hook: 强制「先调研再干步骤」

触发条件：Bash 工具调用，命令含 `plan_tracker.py step ... --n N`

逻辑：
1. 解析 plan_id + step n
2. 读 plan JSON 找 step.research_ref 字段（指向 .research_cache 文件路径）
3. 若有 research_ref：
   - 文件存在 → exit 0 放行
   - 文件不存在 → exit 2 拦截 + 提示「请先 spawn novel-researcher 写 <path>」
4. 若该 step 没 research_ref 字段 → exit 0（非调研前置步骤）
5. plan 找不到 / 解析失败 / step 不存在 → exit 2

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只检查含 `plan_tracker.py step` 的 Bash 命令，其他 Bash 直接放行
- 相关 plan 状态不可验证时 exit 2，避免主链在未知状态继续执行
"""
import json
import os
import re
import sys
from pathlib import Path

# 判定逻辑在共享库 plan_step_gates（单一真相源）。本 hook 是薄
# wrapper：解析 stdin → 找 step → 调 check_research_ref → ok?exit0:exit2。
# research 门是 required gate：决策前置 step 必须有可核验 research artifact。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_research_ref  # noqa: E402


def main():
    try:
        # stdin 按 bytes 读·json 自动 UTF-8 解码（文本模式在 GBK 控制台会把载荷读花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")
    if "plan_tracker" not in command or "step" not in command:
        sys.exit(0)

    # 【安全】解耦提取 plan_id / --n，不要求 plan_id 紧跟 step 再紧跟 --n
    # （位置耦合正则会被「旗标插在中间」绕过），禁止回退。
    if not re.search(r"plan_tracker\.py\s+step\b", command):
        sys.exit(0)
    # 【安全·防 CJK fail-open】首字符类必须用 \w（unicode 感知·匹配 CJK·排除首字 -
    # 不误吞 flag）：换成 [A-Za-z0-9_] 会让中文书名 plan_id 匹配不上 → pid_m=None →
    # exit 0 静默放行调研门。与 anti_skip 同一约束。
    pid_m = re.search(r"\bstep\s+(?:-\S+\s+)*[\"']?(\w[\w\-]*)[\"']?", command)
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
        # workspace 多项目候选路径
        *list(project_dir.glob(f"workspace/**/_数据库/.plans/{plan_id}.json")),
        *list(project_dir.glob(f"workspace/**/.plans/{plan_id}.json")),
    ]
    plan_file = next((c for c in candidates if c.exists()), None)
    if not plan_file:
        print(f"❌ [hook step-research] 找不到 plan: {plan_id}", file=sys.stderr)
        sys.exit(2)

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"❌ [hook step-research] plan 解析失败: {plan_file}: {exc}", file=sys.stderr)
        sys.exit(2)

    # 找 step n 的 research_ref
    step_info = None
    for s in plan.get("steps", []):
        if s.get("n") == n:
            step_info = s
            break
    if not step_info:
        print(f"❌ [hook step-research] plan {plan_id} 不存在 step {n}", file=sys.stderr)
        sys.exit(2)

    # 【相对路径基准】research_ref（如「_数据库/.research_cache」）是相对**小说项目目录**的，
    # 而 CLAUDE_PROJECT_DIR 是仓库根。plan_file 已定位到真实 plan JSON，从它反推小说项目根
    # （含 _数据库 的目录）；直接拿仓库根解析相对 ref 必不存在 → 误拦合法 step。
    if plan_file.parent.name == ".plans" and plan_file.parent.parent.name == "_数据库":
        research_base = plan_file.parent.parent.parent   # 小说项目根
    elif plan_file.parent.name == ".plans":
        research_base = plan_file.parent.parent          # 风格库 / 项目根直挂 .plans
    else:
        research_base = project_dir                      # 全局 plan 兜底仓库根
    # 判定走 check_research_ref。hook 路径不传 auto_pilot/research_skipped
    # → 缺 research_ref 文件时 exit 2。
    result = check_research_ref(step_info, project_dir=research_base,
                               auto_pilot=False, research_skipped=False)
    if result["ok"]:
        sys.exit(0)

    print(f"❌ [hook step-research] plan_id={plan_id} step n={n}\n   {result['msg']}",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
