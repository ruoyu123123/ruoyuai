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
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")
    if "plan_tracker" not in command or "step" not in command:
        sys.exit(0)

    m = re.search(r"plan_tracker\.py\s+step\s+[\"']?([\w\-]+)[\"']?\s+--n\s+(\d+)", command)
    if not m:
        sys.exit(0)
    plan_id, n = m.group(1), int(m.group(2))

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

    if reflection_file:
        # 已有反思 → 静默放行
        sys.exit(0)

    # 选默认路径作建议（全局）
    reflection_file = candidate_dirs[0] / f"{plan_id}_step_{n}.md"

    # 提示：请补写反思（不拦截）
    msg = (
        f"⚠️ [hook step-reflection] plan_id={plan_id} step n={n} 完成但未发现反思文件。\n"
        f"  期望路径: {reflection_file}\n"
        f"  请用 Write 工具补写（参考模板）:\n"
        f"  ---\n"
        + REFLECTION_TEMPLATE.format(plan_id=plan_id, n=n).replace("\n", "\n  ")
        + "\n  ---\n"
        f"  不写反思不阻塞，但下次循环时会累积「未反思 step」list，可能触发 system_health_audit 警告。"
    )
    print(msg, file=sys.stderr)
    sys.exit(0)   # PostToolUse 不能 exit 2


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[hook step-reflection] internal error (放行): {e}", file=sys.stderr)
        sys.exit(0)
