#!/usr/bin/env python3
"""
PreToolUse Hook: 校验 Agent 工具调用的 prompt 合规性
只拦截 Agent 工具，其他工具直接放行
exit 0 = 放行, exit 2 = 拒绝

🔴 2026-06-27 C16-L3-GATES-IN-ORCHESTRATOR
-------------------------------------------
全部判定逻辑（规则 1 契约字段 / 2 长度 / 3 frontmatter / 4 规则文本 warn /
5 多步 PLAN_ID / 8 plan 防篡改 / 9 注入模板 warn / 10 ECAS RESEARCH_REF /
11 蒸馏复刻禁 Agent）连同关键词常量已抽到共享库
`plan_step_gates.check_agent_injection`（北极星⑥消重复·单一真相源）。
本 hook 现为薄 wrapper：解析 stdin → 抽 prompt/desc/subagent_type → 算
plan_state(tampered) → 调 check → 打印 warnings → ok ? exit 0 : exit 2。

与原 hook **exit 语义完全等价**：所有硬规则命中 → exit 2；warn-only（规则 4/9）
只打印不退出。

历史背景（保留供追溯）：
- 规则 5/6：多步流水线 Agent 必须含 PLAN_ID/STEP；含 PLAN_ID 视为契约完整跳过
  PROJECT/CHAPTER/MANIFEST 强制。
- 规则 8（P1-1）：PLAN_ID 引用的 plan 防篡改——plan JSON 被旁路篡改（伪造 step 状态
  绕过跳步防御）→ 在 Agent spawn 前拦下。增值项，校验自身出错一律放行。
- 规则 9（P2-10）：内容级注入模式检测（warn-only，不拦截）。
- v26 清理：删除原规则 12（novel-writer single 模式废弃门禁 + .allow_single_mode.flag）。
"""
import json
import os
import sys

# 🔴 C16：判定逻辑 + 关键词常量抽到共享库 plan_step_gates（北极星⑥消重复）。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_agent_injection  # noqa: E402


def _compute_plan_state(prompt: str):
    """规则 8 防篡改：prompt 含 PLAN_ID → plan_tracker.verify_plan 结果（"tampered"/...）。

    【增值】项：plan_tracker 不可导入 / plan 找不到 / 校验出错——一律 None（放行），
    绝不让防篡改校验本身成为新故障点（与原 hook 一致）。
    """
    import re
    m = re.search(r"PLAN_ID:\s*(\S+)", prompt or "")
    if not m:
        return None
    try:
        import plan_tracker  # _SCRIPTS 已在 sys.path
        return plan_tracker.verify_plan(m.group(1).strip())
    except Exception:
        return None


def main():
    raw = sys.stdin.read(32768)
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name", "") != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    prompt = tool_input.get("prompt", "")
    desc = tool_input.get("description", "")
    subagent_type = tool_input.get("subagent_type", "") or ""

    if not prompt:
        sys.exit(0)

    # 🔴 C16：全部判定下沉到 check_agent_injection（同序判定·首个硬命中即 block）。
    plan_state = _compute_plan_state(prompt)
    result = check_agent_injection(prompt, desc, subagent_type,
                                   plan_state=plan_state)

    # warn-only（规则 4 大段规则文本 / 规则 9 注入模板）：打印不退出（保持原行为）。
    for w in result.get("warnings", []) or []:
        print(f"⚠️ [Hook] {w}", file=sys.stderr)

    if result["ok"]:
        sys.exit(0)

    # 硬规则命中 → exit 2（保持原 hook exit 语义）
    print(f"❌ [Hook agent_gate] {result['msg']}", file=sys.stderr)
    print(f"   prompt 前 200 字: {prompt[:200]}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
