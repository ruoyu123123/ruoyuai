#!/usr/bin/env python3
"""PostToolUse Hook: 步骤完成后强制反思

触发条件：Bash 工具调用完成后，命令含 `plan_tracker.py step ... --n N`

逻辑：
1. 解析 plan_id + step n
2. 检查 `<project>/.reflections/<plan_id>_step_<n>.md` 是否存在
3. 不存在 → stderr 提示「请补写反思」（**不拦截 · exit 0**，因为 PostTool 不可拒）
4. 存在 → 静默 exit 0

【约束】
- PostToolUse 永不 exit 2（会污染主流水线）
- 只观察 + 提醒，不阻断
- 反思文件最小内容：本步骤做了什么 / 学到什么 / 下次怎么做更好（≥ 50 字）
- 主代理收到 stderr 提示后应主动 Write 反思文件
"""
import json
import os
import re
import sys
from pathlib import Path


REFLECTION_TEMPLATE = """# Step {n} 反思 · plan_id={plan_id}

## 本步骤做了什么
<具体执行了什么操作 / 调用了什么工具 / 产出了什么文件>

## 学到了什么
<这一步揭示了什么新信息 / 哪些假设被验证或推翻>

## 下次怎么做更好
<如果重做，会改进什么 / 遇到的坑 / 给后续 step 的提示>

---
**生成时间**: <auto-fill>
**触发**: posttooluse_step_reflection hook 提示
"""


def main():
    try:
        # 2026-07-08 修（Windows 编码根因）：bytes 读 stdin·json 自动 UTF-8（GBK 控制台文本读会花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")
    if "plan_tracker" not in command or "step" not in command:
        sys.exit(0)

    # 2026-05-29 修【安全·绕过】：解耦提取 plan_id / --n（与两个 PreToolUse hook 一致）。
    # 注意：本 hook 是 PostToolUse 观察层，全程只 exit 0，严禁引入非 0 退出。
    if not re.search(r"plan_tracker\.py\s+step\b", command):
        sys.exit(0)
    pid_m = re.search(r"\bstep\s+(?:-\S+\s+)*[\"']?([A-Za-z0-9_][\w\-]*)[\"']?", command)
    n_m = re.search(r"--n\s+(\d+)", command)
    if not pid_m or not n_m:
        sys.exit(0)
    plan_id, n = pid_m.group(1), int(n_m.group(1))

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    # v22.gov 修：反思路径优先放在 plan project 根（小说项目/风格库），fallback 全局
    candidate_dirs = [
        project_dir / "core" / "claude-home" / ".reflections",  # 全局
        project_dir / "_数据库" / ".reflections",
        project_dir / ".reflections",
    ]
    # 也加 workspace 多项目检测
    for d in project_dir.glob("workspace/**/_数据库/.reflections"):
        candidate_dirs.insert(0, d)

    reflection_file = None
    for d in candidate_dirs:
        f = d / f"{plan_id}_step_{n}.md"
        if f.exists():
            reflection_file = f
            break

    # v28 降噪（2026-06-18）：反思文件从未被流水线采纳，打印模板=纯噪声。
    # 静默放行，不再打印模板提示。
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[hook step-reflection] internal error (放行): {e}", file=sys.stderr)
        sys.exit(0)
