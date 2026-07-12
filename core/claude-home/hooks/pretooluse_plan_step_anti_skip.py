#!/usr/bin/env python3
"""PreToolUse Hook: 禁跳步门禁（用户硬规则）

触发条件：Bash 工具调用，命令含 `plan_tracker.py step ... --skip-output`

逻辑：
1. 解析 plan_id + step n
2. 读 plan JSON 找 step.expected_outputs 字段
3. 如果 expected_outputs 非空 且 step.skip_output_allowed != true → exit 2 拦截
4. 否则放行（场景合法）

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只钩 plan_tracker step + --skip-output 同时出现
- plan 解析失败、step 不存在或 required 输出被跳过 → exit 2
"""
import json
import os
import re
import sys
from pathlib import Path

# 判定逻辑在共享库 plan_step_gates（单一真相源）。
# 本 hook 是薄 wrapper：解析 stdin → 找 step → 调 check_anti_skip → ok?exit0:exit2。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_anti_skip  # noqa: E402


def main():
    try:
        # stdin 按 bytes 读·json 自动 UTF-8 解码（文本模式在 GBK 控制台会把载荷读花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")

    # 只钩 plan_tracker step + --skip-output 同时出现
    if "plan_tracker" not in command or "step" not in command or "--skip-output" not in command:
        sys.exit(0)

    # 【安全】解耦提取：先确认是 plan_tracker.py step 子命令，再独立提取 plan_id / --n。
    # 位置耦合正则（要求 plan_id 紧跟 step 再紧跟 --n）会被「flag 插在中间」绕过放行，禁止回退。
    if not re.search(r"plan_tracker\.py\s+step\b", command):
        sys.exit(0)
    # plan_id：step 后第一个非 flag token（不以 - 开头），跳过 --skip-output 等任意顺序的旗标。
    # 【安全·防 CJK fail-open】首字符类必须用 \w（Python re unicode 感知·匹配 CJK·且排除首字 -
    # 不误吞 flag）：换成 [A-Za-z0-9_] 会让中文书名 plan_id（如首字『验』）匹配不上 →
    # pid_m=None → exit 0 放行 --skip-output，防跳步守卫对中文项目形同虚设。
    pid_m = re.search(r"\bstep\s+(?:-\S+\s+)*[\"']?(\w[\w\-]*)[\"']?", command)
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
        print(f"❌ [Hook anti_skip] 找不到 plan: {plan_id}", file=sys.stderr)
        sys.exit(2)

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"❌ [Hook anti_skip] plan 解析失败: {plan_file}: {exc}", file=sys.stderr)
        sys.exit(2)

    # 找 step n 的模板定义
    target_step = next((s for s in plan.get("steps", []) if s.get("n") == n), None)
    if not target_step:
        print(f"❌ [Hook anti_skip] plan {plan_id} 不存在 step {n}", file=sys.stderr)
        sys.exit(2)

    # 核心校验走 check_anti_skip（进到这里的命令必带 --skip-output → requested=True）。
    result = check_anti_skip(target_step, skip_output_requested=True)
    if result["ok"]:
        sys.exit(0)

    print(f"❌ [Hook anti_skip] plan_tracker step --skip-output 被拒绝（plan {plan_id}）",
          file=sys.stderr)
    print(f"   {result['msg']}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
